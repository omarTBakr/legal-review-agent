from enum import StrEnum


class VerificationStatus(StrEnum):
    """Whether the evidence checker located a finding's quote in the source."""

    VERIFIED = "verified"
    UNVERIFIED = "unverified"
