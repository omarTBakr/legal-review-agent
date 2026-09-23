from dataclasses import dataclass, field


@dataclass
class CleanupScratchInput:
    """Local files a document is finished with."""

    task_id: str
    pdf_key: str
    paths: list[str] = field(default_factory=list)


@dataclass
class CleanupScratchOutput:
    """How many files went, and how many could not be removed."""

    removed: int = 0
    failed: int = 0
