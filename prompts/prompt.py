from dataclasses import dataclass

from enums.PromptName import PromptName


@dataclass(frozen=True)
class Prompt:
    """
    A named system/user pair.

    Frozen so a prompt cannot be mutated by whoever renders it, and templated
    with str.format so the text stays readable as text.
    """

    name: PromptName
    system: str
    user_template: str
    # most replies here are parsed as JSON; a prompt that wants prose says so,
    # and the client stops demanding a JSON object for it
    expects_json: bool = True

    def render(self, **variables) -> str:
        """
        Fills the template.

        Raises KeyError naming the missing variable rather than sending a
        half-filled prompt to a paid API.
        """
        try:
            return self.user_template.format(**variables)
        except KeyError as exc:
            raise KeyError(f"prompt {self.name.value} is missing variable {exc}") from exc
