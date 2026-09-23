from dataclasses import dataclass, field


@dataclass
class ChatTurn:
    """One question about a review, and the answer it got."""

    question: str
    answer: str
    # "<pdf_key> p. 7" for each page the answer was drawn from
    citations: list[str] = field(default_factory=list)
    asked_at: str = ""
    # whether the question arrived as speech, which is worth knowing when a
    # transcription turns out to have misheard something
    spoken: bool = False
    # the recordings in the bucket, when they are being kept
    question_audio: str = ""
    answer_audio: str = ""

    def to_dict(self) -> dict:
        return {
            "question": self.question,
            "answer": self.answer,
            "citations": list(self.citations),
            "asked_at": self.asked_at,
            "spoken": self.spoken,
            "question_audio": self.question_audio,
            "answer_audio": self.answer_audio,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "ChatTurn":
        return cls(
            question=str(raw.get("question", "")),
            answer=str(raw.get("answer", "")),
            citations=[str(item) for item in raw.get("citations", [])],
            asked_at=str(raw.get("asked_at", "")),
            spoken=bool(raw.get("spoken", False)),
            question_audio=str(raw.get("question_audio", "")),
            answer_audio=str(raw.get("answer_audio", "")),
        )


@dataclass
class ChatThread:
    """Everything that has been asked about one review."""

    task_id: str
    project_id: str
    turns: list[ChatTurn] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "project_id": self.project_id,
            "turns": [turn.to_dict() for turn in self.turns],
            "turn_count": len(self.turns),
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "ChatThread":
        return cls(
            task_id=str(raw.get("task_id", "")),
            project_id=str(raw.get("project_id", "")),
            turns=[ChatTurn.from_dict(item) for item in raw.get("turns", [])],
        )

    def render(self, limit: int = 6) -> str:
        """
        The recent turns, as the prompt shows them.

        Only the last few: the whole thread would crowd out the pages of the
        document, which are what the answer actually has to come from.
        """
        if not self.turns:
            return ""

        recent = "\n\n".join(f"Q: {turn.question}\nA: {turn.answer}" for turn in self.turns[-limit:])

        return f"Earlier in this conversation:\n\n{recent}\n\n"
