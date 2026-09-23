import json

from temporalio import activity

from enums.PromptName import PromptName
from exceptions.llm import LLMResponseError
from interfaces import get_llm
from prompts import get_prompt
from schemas.legal_advice import LegalAdvice
from schemas.merge_advice import MergeAdviceInput, MergeAdviceOutput
from utils.evidence import carry_verification


def _as_text(parts: list[LegalAdvice]) -> str:
    """Renders the per-batch advice for the merge prompt."""
    return "\n\n".join(
        json.dumps(
            {
                "summary": part.summary,
                "key_risks": [risk.to_prompt_dict() for risk in part.key_risks],
                "needs_human": part.needs_human,
                "question": part.question,
            },
            indent=2,
        )
        for part in parts
    )


@activity.defn
async def merge_advice(payload: MergeAdviceInput) -> MergeAdviceOutput:
    """
    Reduces the per-batch advice for one document to a single answer.

    A document that fitted in one batch needs no model call, so it skips
    straight through rather than paying for a round trip that cannot change
    anything.
    """
    activity.logger.info("[task %s] merging %d part(s) for %s", payload.task_id, len(payload.parts), payload.pdf_key)

    if not payload.parts:
        raise LLMResponseError(f"nothing to merge for {payload.pdf_key}")

    if len(payload.parts) == 1:
        activity.logger.info("[task %s] %s had one batch; nothing to merge", payload.task_id, payload.pdf_key)
        return MergeAdviceOutput(advice=payload.parts[0])

    try:
        raw = await get_llm().complete_json(
            get_prompt(PromptName.MERGE_ADVICE),
            pdf_key=payload.pdf_key,
            parts=_as_text(payload.parts),
        )
        advice = LegalAdvice.from_model(raw)
    except ValueError as exc:
        activity.logger.error("[task %s] unusable merge for %s: %s", payload.task_id, payload.pdf_key, exc)
        raise LLMResponseError(f"merged advice for {payload.pdf_key} was unusable: {exc}") from exc
    except Exception:
        activity.logger.exception("[task %s] failed to merge advice for %s", payload.task_id, payload.pdf_key)
        raise

    # the merge never sees the pages, so only quotes it kept intact stay verified
    advice.key_risks = carry_verification(advice.key_risks, [risk for part in payload.parts for risk in part.key_risks])

    activity.logger.info(
        "[task %s] %s merged into %d risk(s)%s",
        payload.task_id,
        payload.pdf_key,
        len(advice.key_risks),
        ", needs a human" if advice.needs_human else "",
    )

    return MergeAdviceOutput(advice=advice)
