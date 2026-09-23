import asyncio
import json

from temporalio import activity

from schemas.upload_advice import UploadAdviceInput, UploadAdviceOutput
from utils.advice_store import advice_key
from utils.config import get_setting
from utils.utility import upload_s3_file


@activity.defn
async def upload_advice(payload: UploadAdviceInput) -> UploadAdviceOutput:
    """Stores finished advice as JSON and reports where it landed."""
    settings = get_setting()
    advice = payload.advice
    bucket = payload.bucket or settings.s3_legal_advice
    key = advice_key(payload.pdf_key)

    activity.logger.info("[task %s] storing advice -> %s/%s", payload.task_id, bucket, key)

    document = {
        "task_id": payload.task_id,
        "pdf_key": payload.pdf_key,
        "summary": advice.summary,
        "key_risks": [risk.to_dict() for risk in advice.key_risks],
        "review_decision": advice.review_decision.value,
        "question": advice.question,
    }

    try:
        # boto3 blocks; keep the event loop free for the other documents in flight
        body = json.dumps(document, indent=2).encode("utf-8")
        await asyncio.to_thread(upload_s3_file, body, bucket, key)
    except Exception:
        activity.logger.exception("[task %s] failed to store advice for %s", payload.task_id, payload.pdf_key)
        raise

    s3_path = f"s3://{bucket}/{key}"

    activity.logger.info("[task %s] stored advice at %s", payload.task_id, s3_path)

    return UploadAdviceOutput(bucket=bucket, key=key, s3_path=s3_path)
