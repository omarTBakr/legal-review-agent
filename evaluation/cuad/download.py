"""Fetching CUAD, once, into a gitignored directory.

Only the SQuAD-style JSON is taken. The archive it lives in is 18 MB and also
contains the train/test split, which is not wanted: the suite is not training
anything, and scoring on the training half of a public dataset the reviewer
model has probably read is a separate problem the README is honest about.

The 510 contracts' plain text is inside the JSON, so the PDFs — 100 MB more on
Zenodo — are never downloaded. Nothing here is committed: the licence permits
redistribution with attribution, but a 40 MB blob in git history is a cost with
no benefit when the download is one command.
"""

import hashlib
import zipfile
from pathlib import Path

import httpx

from evaluation.config import DATA_DIR
from utils.logger import get_logger

logger = get_logger(__name__)

ARCHIVE_URL = "https://github.com/TheAtticusProject/cuad/raw/main/data.zip"

# sha256 of data.zip as published; verified before anything is extracted, so a
# truncated download or a changed upstream file fails loudly instead of being
# scored
ARCHIVE_SHA256 = "f8161d18bea4e9c05e78fa6dda61c19c846fb8087ea969c172753bc2f45b999a"

# the only member wanted: all 510 contracts with their annotated spans
MEMBER = "CUADv1.json"

ATTRIBUTION = (
    "CUAD v1 (Contract Understanding Atticus Dataset), The Atticus Project, "
    "licensed CC BY 4.0. https://www.atticusprojectai.org/cuad"
)


def sha256_of(path: Path) -> str:
    """The file's digest, read in chunks so a 40 MB file is not held twice."""
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)

    return digest.hexdigest()


def download_archive(destination: Path, url: str = ARCHIVE_URL) -> Path:
    """Downloads the archive to `destination`, streaming rather than buffering it."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")

    logger.info("downloading CUAD from %s", url)

    with httpx.stream("GET", url, follow_redirects=True, timeout=300) as response:
        response.raise_for_status()
        with partial.open("wb") as handle:
            for chunk in response.iter_bytes(1 << 16):
                handle.write(chunk)

    # renamed only once complete, so an interrupted download is never mistaken
    # for a cached one
    partial.replace(destination)

    return destination


def ensure_dataset(data_dir: Path = DATA_DIR, expected_sha256: str = ARCHIVE_SHA256) -> Path:
    """
    The path to CUADv1.json, downloading and verifying the archive if needed.

    Raises ValueError on a checksum mismatch and leaves the bad archive in place
    under a .rejected name, so whoever investigates has the file rather than a
    message about it.
    """
    extracted = data_dir / MEMBER
    if extracted.is_file():
        return extracted

    archive = data_dir / "cuad-data.zip"
    if not archive.is_file():
        download_archive(archive)

    actual = sha256_of(archive)
    if actual != expected_sha256:
        rejected = archive.with_suffix(".zip.rejected")
        archive.replace(rejected)
        raise ValueError(
            f"CUAD archive checksum mismatch: expected {expected_sha256}, got {actual}. "
            f"The file was kept as {rejected}; upstream may have republished it."
        )

    with zipfile.ZipFile(archive) as bundle:
        bundle.extract(MEMBER, path=data_dir)

    logger.info("CUAD ready at %s", extracted)

    return extracted
