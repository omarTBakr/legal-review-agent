import json

from temporalio import activity

from enums.PromptName import PromptName
from enums.ReviewDecision import ReviewDecision
from exceptions.llm import LLMResponseError
from interfaces import get_llm
from prompts import get_prompt
from schemas.human_followup import HumanFollowupInput, HumanFollowupOutput
from schemas.legal_advice import LegalAdvice
from utils.evidence import carry_verification


@activity.defn
async def human_followup(payload: HumanFollowupInput) -> HumanFollowupOutput:
    """
    Revises draft advice using a human's answer to the question it raised.

    The result is marked HUMAN_APPROVED: a person saw the question and replied,
    which is the distinction the review decision exists to record.
    """
    activity.logger.info("[task %s] revising %s with the human's answer", payload.task_id, payload.pdf_key)

    draft = json.dumps(
        {
            "summary": payload.advice.summary,
            "key_risks": [risk.to_prompt_dict() for risk in payload.advice.key_risks],
        },
        indent=2,
    )

    try:
        raw = await get_llm().complete_json(
            get_prompt(PromptName.HUMAN_FOLLOWUP),
            pdf_key=payload.pdf_key,
            draft=draft,
            question=payload.question,
            answer=payload.answer,
        )
        advice = LegalAdvice.from_model(raw)
    except ValueError as exc:
        activity.logger.error("[task %s] unusable revision for %s: %s", payload.task_id, payload.pdf_key, exc)
        raise LLMResponseError(f"revised advice for {payload.pdf_key} was unusable: {exc}") from exc
    except Exception:
        activity.logger.exception("[task %s] failed to revise %s", payload.task_id, payload.pdf_key)
        raise

    advice.key_risks = carry_verification(advice.key_risks, payload.advice.key_risks)

    advice.review_decision = ReviewDecision.HUMAN_APPROVED
    # the question has been answered; the model must not re-raise it
    advice.needs_human = False
    advice.question = ""

    activity.logger.info("[task %s] %s revised, %d risk(s)", payload.task_id, payload.pdf_key, len(advice.key_risks))

    return HumanFollowupOutput(advice=advice)
