"""Counting what a run spent, from what OpenRouter actually billed.

OpenRouterLLM.complete returns only the message text, so the usage block in the
reply is dropped before the caller sees it. Rather than copy that method to keep
the numbers, the meter hands the client an httpx response hook: the hook reads
every reply on its way past and records `usage`, and the pipeline underneath is
the shipped one, unmodified.

Token counts are therefore the provider's own, not an estimate. The price per
token is fetched from OpenRouter's public model list, so the cost is only as
current as that list; a model the list does not mention is counted and reported
with its cost left unknown rather than guessed at zero.
"""

import json
from collections import defaultdict
from dataclasses import dataclass, field

import httpx

from enums.LLMProvider import LLMProvider
from interfaces.ollama_llm import OllamaLLM
from interfaces.openrouter_llm import OpenRouterLLM
from utils.config import Settings
from utils.logger import get_logger

logger = get_logger(__name__)

PRICING_URL = "https://openrouter.ai/api/v1/models"


@dataclass
class ModelUsage:
    """What one model was asked for and what it returned."""

    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def to_dict(self) -> dict:
        return {
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
        }


@dataclass
class TokenMeter:
    """
    Per-model token totals for a run, and the cost they add up to.

    One meter per run, shared by the reviewer, the extractor and the judge, so
    the printed total is the whole bill rather than one layer's share.
    """

    usage: dict[str, ModelUsage] = field(default_factory=lambda: defaultdict(ModelUsage))
    prices: dict[str, tuple[float, float]] = field(default_factory=dict)

    def record(self, model: str, prompt_tokens: int, completion_tokens: int) -> None:
        entry = self.usage[model]
        entry.calls += 1
        entry.prompt_tokens += prompt_tokens
        entry.completion_tokens += completion_tokens

    def _hook(self):
        """An httpx response hook that files away the usage block of every reply."""

        async def record_usage(response: httpx.Response) -> None:
            # the hook runs before OpenRouterLLM reads the body, so read it here;
            # httpx caches it and the caller's .json() still works
            await response.aread()

            try:
                body = response.json()
            except ValueError:
                return

            model = body.get("model")
            if not model:
                return

            usage = body.get("usage")
            if isinstance(usage, dict):
                self.record(model, int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0))
                return

            # Ollama reports the same two numbers under its own names, and only
            # on the final object of a reply
            if body.get("done"):
                self.record(model, int(body.get("prompt_eval_count") or 0), int(body.get("eval_count") or 0))

        return record_usage

    def client(self, settings: Settings) -> httpx.AsyncClient:
        """An OpenRouter client that reports to this meter."""
        return httpx.AsyncClient(
            base_url=settings.openrouter_base_url,
            timeout=settings.llm_timeout_seconds,
            headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
            event_hooks={"response": [self._hook()]},
        )

    def ollama_client(self, settings: Settings) -> httpx.AsyncClient:
        """A local Ollama client that reports to this meter."""
        return httpx.AsyncClient(
            base_url=settings.ollama_base_url,
            timeout=httpx.Timeout(settings.llm_timeout_seconds, connect=10.0),
            event_hooks={"response": [self._hook()]},
        )

    def llm(
        self,
        settings: Settings,
        model: str = "",
        temperature: float | None = None,
        max_tokens: int = 0,
        provider: str = "",
    ):
        """
        A metered LLM, optionally pointed at a different model — and a different
        provider — than the service uses.

        The overrides go through Settings.model_copy rather than the environment,
        so asking for the judge cannot change which model the reviewer gets.

        `provider` is per level, not global. The reviewer is the system under
        test and belongs wherever the product runs; the judge and the expert are
        measuring instruments and can live somewhere else entirely — which is
        how a hosted reviewer gets graded by a model on this machine for
        nothing. Empty means "whatever LLM_PROVIDER says".

        The meter counts tokens either way. Ollama reports the same two numbers
        under its own names, and they price at nothing, which is what they cost.
        """
        chosen = LLMProvider.parse(provider or settings.llm_provider)
        local = chosen is LLMProvider.OLLAMA

        overrides = {"llm_provider": chosen.value}
        if model:
            overrides["ollama_model" if local else "openrouter_model"] = model
        if temperature is not None:
            overrides["llm_temperature"] = temperature
        if max_tokens:
            overrides["llm_max_tokens"] = max_tokens

        scoped = settings.model_copy(update=overrides)

        if local:
            return OllamaLLM(scoped, client=self.ollama_client(scoped))

        return OpenRouterLLM(scoped, client=self.client(scoped))

    async def load_prices(self) -> None:
        """
        Fills in dollars per token from OpenRouter's public model list.

        Unauthenticated and free to call. A failure is logged and left alone: a
        run should not die because the price list was unreachable, it should
        report its tokens and say the cost is unknown.
        """
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                body = (await client.get(PRICING_URL)).json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("could not fetch OpenRouter prices (%s); cost will be reported as unknown", exc)
            return

        for entry in body.get("data") or []:
            pricing = entry.get("pricing") or {}
            try:
                self.prices[entry["id"]] = (float(pricing["prompt"]), float(pricing["completion"]))
            except (KeyError, TypeError, ValueError):
                continue

    def cost(self) -> tuple[float, list[str]]:
        """
        The run's estimated dollar cost, and the models it could not price.

        Estimated, not billed: OpenRouter's own invoice applies discounts, cache
        hits and rounding this does not know about.
        """
        total, unpriced = 0.0, []

        for model, entry in sorted(self.usage.items()):
            price = self.prices.get(model)
            if price is None:
                unpriced.append(model)
                continue
            total += entry.prompt_tokens * price[0] + entry.completion_tokens * price[1]

        return total, unpriced

    def report(self) -> dict:
        """The usage block that goes into the scorecard and onto the terminal."""
        total, unpriced = self.cost()

        return {
            "by_model": {model: entry.to_dict() for model, entry in sorted(self.usage.items())},
            "calls": sum(entry.calls for entry in self.usage.values()),
            "prompt_tokens": sum(entry.prompt_tokens for entry in self.usage.values()),
            "completion_tokens": sum(entry.completion_tokens for entry in self.usage.values()),
            "estimated_cost_usd": round(total, 4),
            "unpriced_models": unpriced,
        }

    def summary_line(self) -> str:
        """One line for the end of a run."""
        report = self.report()
        cost = f"${report['estimated_cost_usd']:.4f}"
        if report["unpriced_models"]:
            cost += f" (+ unpriced: {', '.join(report['unpriced_models'])})"

        return (
            f"{report['calls']} model call(s), "
            f"{report['prompt_tokens']:,} prompt + {report['completion_tokens']:,} completion tokens, "
            f"estimated {cost}"
        )

    def write(self, path) -> None:
        path.write_text(json.dumps(self.report(), indent=2), encoding="utf-8")
