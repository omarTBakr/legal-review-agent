from temporalio import activity

from enums.PromptName import PromptName
from exceptions.llm import LLMResponseError
from interfaces import get_llm
from prompts import get_prompt
from schemas.analyze_batch import AnalyzeBatchInput, AnalyzeBatchOutput
from schemas.legal_advice import LegalAdvice
from utils.evidence import verify_risks


@activity.defn
async def analyze_batch(payload: AnalyzeBatchInput) -> AnalyzeBatchOutput:
    """Sends one batch of pages to the model and validates what comes back."""
    batch = payload.batch

    activity.logger.info(
        "[task %s] analysing %s %s (%d of %d)",
        payload.task_id,
        payload.pdf_key,
        batch.label,
        batch.index + 1,
        payload.batch_count,
    )

    try:
        raw = await get_llm().complete_json(
            get_prompt(PromptName.LEGAL_ADVICE),
            pdf_key=payload.pdf_key,
            batch_label=batch.label,
            batch_number=batch.index + 1,
            batch_count=payload.batch_count,
            markdown=batch.markdown,
        )
    except Exception:
        activity.logger.exception("[task %s] the model failed on %s %s", payload.task_id, payload.pdf_key, batch.label)
        raise

    # the model is not trusted past this point
    try:
        advice = LegalAdvice.from_model(raw)
    except ValueError as exc:
        activity.logger.error("[task %s] unusable advice for %s: %s", payload.task_id, payload.pdf_key, exc)
        raise LLMResponseError(f"advice for {payload.pdf_key} {batch.label} was unusable: {exc}") from exc

    # nor is anything it says about where a risk came from
    advice.key_risks = verify_risks(advice.key_risks, batch.markdown)
    unverified = sum(not risk.quote_verified for risk in advice.key_risks)

    if unverified:
        activity.logger.warning(
            "[task %s] %s %s: %d quote(s) not found in the excerpt",
            payload.task_id,
            payload.pdf_key,
            batch.label,
            unverified,
        )

    activity.logger.info(
        "[task %s] %s %s: %d risk(s), %d unverified%s",
        payload.task_id,
        payload.pdf_key,
        batch.label,
        len(advice.key_risks),
        unverified,
        ", needs a human" if advice.needs_human else "",
    )

    return AnalyzeBatchOutput(advice=advice)
