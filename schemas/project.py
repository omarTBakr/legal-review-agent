from dataclasses import dataclass, field


@dataclass
class Project:
    """
    A folder in the bucket that a client's reviews belong to.

    `id` is the folder name: a slug of the project's name plus a short random
    suffix, so two projects called "NDAs" do not share a folder. `email`, when
    set, is where a finished review's report is sent.
    """

    id: str
    name: str
    description: str = ""
    email: str = ""
    created_at: str = ""

    @property
    def prefix(self) -> str:
        """
        The key prefix every object belonging to this project sits under.

        Inside S3_PROJECTS, which is the projects folder: the id alone, with no
        `projects/` in front of it.
        """
        return f"{self.id}/"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "email": self.email,
            "created_at": self.created_at,
            "prefix": self.prefix,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "Project":
        """Reads a manifest back, ignoring anything a later version added."""
        return cls(
            id=str(raw.get("id", "")),
            name=str(raw.get("name", "")),
            description=str(raw.get("description", "")),
            email=str(raw.get("email", "")),
            created_at=str(raw.get("created_at", "")),
        )


@dataclass
class ProjectReview:
    """
    One review submitted into a project.

    Kept so a review can still be found after Temporal has dropped its history:
    the advice itself lives in the advice bucket under the same keys.
    """

    task_id: str
    workflow_id: str
    pdf_keys: list[str] = field(default_factory=list)
    submitted_at: str = ""

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "workflow_id": self.workflow_id,
            "pdf_keys": list(self.pdf_keys),
            "submitted_at": self.submitted_at,
            "document_count": len(self.pdf_keys),
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "ProjectReview":
        return cls(
            task_id=str(raw.get("task_id", "")),
            workflow_id=str(raw.get("workflow_id", "")),
            pdf_keys=[str(key) for key in raw.get("pdf_keys", [])],
            submitted_at=str(raw.get("submitted_at", "")),
        )
