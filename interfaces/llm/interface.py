import json
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from json_repair import repair_json

from exceptions.llm import LLMResponseError
from prompts.prompt import Prompt
from utils.logger import get_logger

logger = get_logger(__name__)


class LLMInterface(ABC):
    """
    What the rest of the project is allowed to know about a language model.

    Activities depend on this, never on a concrete client, so a provider can be
    swapped and tests can substitute a fake without touching the pipeline.
    """

    @abstractmethod
    async def complete(self, prompt: Prompt, **variables) -> str:
        """Renders the prompt, sends it, and returns the raw reply."""

    async def stream(self, prompt: Prompt, **variables) -> AsyncIterator[str]:
        """
        The reply as it is written, piece by piece.

        Only worth it where someone is reading along — the chat answers. The
        pipeline's own calls want the whole reply validated before anything is
        done with it, so they keep using `complete`.

        The default implementation waits for the whole reply and yields it in
        one piece, so a provider that cannot stream, and the fakes in the
        tests, still satisfy the interface. Read it as "at least one chunk",
        not "many".
        """
        yield await self.complete(prompt, **variables)

    async def complete_json(self, prompt: Prompt, **variables) -> dict:
        """
        Same, but insists the reply is a JSON object.

        Implemented here rather than per provider because every provider has
        the same problem: models like to wrap JSON in prose or code fences.
        """
        raw = await self.complete(prompt, **variables)

        return self.parse_json_object(raw, prompt_name=prompt.name.value)

    @staticmethod
    def parse_json_object(raw: str, prompt_name: str = "") -> dict:
        """
        Pulls a JSON object out of a model reply.

        Tolerates a ```json fence or leading commentary, because that is what
        models actually do. When strict parsing fails, json_repair is the
        safeguard: it mends trailing commas, unquoted keys, single quotes and
        replies cut off at the token limit. Raises LLMResponseError when there
        is no object at all, or nothing usable survives the repair, so the
        caller can retry.
        """
        text = (raw or "").strip()

        if "```" in text:
            # keep the largest fenced block, which is where the JSON lives
            blocks = [block for block in text.split("```") if block.strip()]
            blocks = [block[4:] if block.lstrip().lower().startswith("json") else block for block in blocks]
            text = max(blocks, key=len).strip() if blocks else text

        who = prompt_name or "model"

        # try the whole reply first: an array of objects must be rejected, not
        # silently reduced to the first object inside it
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None

        if parsed is None:
            start, end = text.find("{"), text.rfind("}")
            # without an opening brace there is nothing to repair; json_repair
            # would otherwise turn a refusal into an empty result
            if start == -1:
                raise LLMResponseError(f"{who} reply contained no JSON object: {text[:200]!r}")

            try:
                parsed = json.loads(text[start : end + 1])
            except json.JSONDecodeError as exc:
                parsed = LLMInterface._repair(text[start:], who, exc)

        if not isinstance(parsed, dict):
            raise LLMResponseError(f"{who} reply was {type(parsed).__name__}, expected an object")

        return parsed

    @staticmethod
    def _repair(text: str, who: str, error: json.JSONDecodeError):
        """
        Last attempt at a reply strict parsing rejected.

        Repairs from the first brace to the end rather than to the last brace,
        so a reply truncated inside a nested object keeps its later fields
        instead of being cut back to the inner one.
        """
        repaired = repair_json(text, return_objects=True)

        # "" is json_repair's way of saying it found nothing, and {} from a
        # lone brace is no more use to the caller than a failure
        if repaired == "" or repaired == {}:
            raise LLMResponseError(f"{who} reply was not valid JSON and could not be repaired: {error}") from error

        logger.warning("%s reply was malformed JSON (%s); used the repaired version", who, error)

        return repaired
