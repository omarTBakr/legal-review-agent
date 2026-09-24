import uuid
from pathlib import Path
from typing import NamedTuple

import boto3
from botocore.client import BaseClient
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from exceptions.storage import (
    DownloadError,
    LocalFileNotFoundError,
    ObjectNotFoundError,
    StorageConnectionError,
    UploadError,
)
from utils.config import Settings, get_setting

_s3_client = None


def get_s3_client() -> BaseClient:
    """
    Returns a singleton boto3 S3 client pointed at the endpoint from .env.
    """
    global _s3_client
    if _s3_client is None:
        settings = get_setting()
        _s3_client = boto3.client(
            "s3",
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            region_name=settings.aws_region,
            endpoint_url=settings.aws_endpoint_url,
            config=Config(signature_version="s3v4"),
        )
    return _s3_client


def upload_s3_file(source: Path | str | bytes, bucket: str, key: str) -> str:
    """
    Uploads a local file (path) or raw bytes to `bucket` under `key`.

    Returns the key it was stored under.
    """
    client = get_s3_client()

    try:
        if isinstance(source, bytes):
            client.put_object(Bucket=bucket, Key=key, Body=source)
        else:
            source = Path(source)
            if not source.is_file():
                raise LocalFileNotFoundError(f"cannot upload, no such file: {source}")
            client.upload_file(str(source), bucket, key)
    except ClientError as exc:
        raise UploadError(f"could not upload {bucket}/{key}: {exc}") from exc
    except BotoCoreError as exc:
        raise StorageConnectionError(f"could not reach the object store: {exc}") from exc

    return key


def download_s3_file(bucket: str, key: str, destination: Path | str) -> Path:
    """
    Downloads `key` from `bucket` to `destination`.

    If `destination` is a directory the file keeps the basename of the key.
    Returns the path it was written to.
    """
    destination = Path(destination)

    if destination.is_dir():
        destination = destination / Path(key).name

    destination.parent.mkdir(parents=True, exist_ok=True)

    try:
        get_s3_client().download_file(bucket, key, str(destination))
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NoSuchBucket"):
            raise ObjectNotFoundError(f"no such object: {bucket}/{key}") from exc
        raise DownloadError(f"could not download {bucket}/{key}: {exc}") from exc
    except BotoCoreError as exc:
        raise StorageConnectionError(f"could not reach the object store: {exc}") from exc

    return destination


def download_s3_bytes(bucket: str, key: str) -> bytes:
    """
    Reads an object straight into memory.

    For the small JSON documents the project keeps in the bucket, where a
    round trip through a local file would be pointless.
    """
    try:
        return get_s3_client().get_object(Bucket=bucket, Key=key)["Body"].read()
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NoSuchBucket"):
            raise ObjectNotFoundError(f"no such object: {bucket}/{key}") from exc
        raise DownloadError(f"could not download {bucket}/{key}: {exc}") from exc
    except BotoCoreError as exc:
        raise StorageConnectionError(f"could not reach the object store: {exc}") from exc


def delete_s3_file(bucket: str, key: str) -> None:
    """
    Removes one object, treating "it was not there" as success.

    Used to undo a partly-stored upload, where the caller is already handling a
    failure and a second one would only bury the first.
    """
    try:
        get_s3_client().delete_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        raise UploadError(f"could not delete {bucket}/{key}: {exc}") from exc
    except BotoCoreError as exc:
        raise StorageConnectionError(f"could not reach the object store: {exc}") from exc


def list_s3_keys(bucket: str, prefix: str) -> list[str]:
    """Every key under `prefix`, following the pagination boto3 hides behind a token."""
    client = get_s3_client()
    keys = []
    token = None

    try:
        while True:
            kwargs = {"Bucket": bucket, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token

            page = client.list_objects_v2(**kwargs)
            keys.extend(item["Key"] for item in page.get("Contents", []))

            if not page.get("IsTruncated"):
                return keys
            token = page.get("NextContinuationToken")
    except ClientError as exc:
        raise DownloadError(f"could not list {bucket}/{prefix}: {exc}") from exc
    except BotoCoreError as exc:
        raise StorageConnectionError(f"could not reach the object store: {exc}") from exc


class RunArtifacts(NamedTuple):
    """Where one PDF's inputs and outputs live, for a single run."""

    task_id: str
    pdf_key: str
    md_key: str
    local_pdf: Path


def build_run_artifacts(filename: str, settings: Settings, prefix: str = "", run_id: str | None = None) -> RunArtifacts:
    """
    Derives the object keys and the local PDF path for one run.

    The pdf and the markdown share a random run id so that two uploads of the
    same filename cannot overwrite each other in the buckets. That same id is
    the task id: it names the workflow run and is logged by every activity.

    `prefix` puts the keys inside a project's folder. The local scratch path
    keeps the bare filename: the prefix organises the bucket, not the worker's
    disk.
    """
    stem = Path(filename).stem or "document"
    run_id = run_id or uuid.uuid4().hex[:8]
    name = f"{stem}-{run_id}"
    pdf_key = f"{prefix}{name}.pdf"
    md_key = f"{prefix}{name}.md"

    return RunArtifacts(
        task_id=run_id,
        pdf_key=pdf_key,
        md_key=md_key,
        local_pdf=settings.temp_pdf_path / f"{name}.pdf",
    )
