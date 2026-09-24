"""
Rebuilds the README's table of contents from its own headings.

    uv run python scripts/toc.py            # rewrite it
    uv run python scripts/toc.py --check    # fail if it is out of date

Written because the hand-maintained one drifted: four sections and three
endpoints existed in the document and not in its contents, which is the failure
mode of every table of contents somebody has to remember to update.

Anchors follow GitHub's rule — lower-case, punctuation dropped, spaces to
hyphens — so the links work on the rendered page rather than only locally.
"""

import argparse
import re
import sys
from pathlib import Path

README = Path(__file__).resolve().parent.parent / "README.md"

START = "## Contents"
# headings below this level are too granular to be worth listing
MAX_DEPTH = 3


def anchor(heading: str) -> str:
    """GitHub's anchor for a heading."""
    text = heading.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text.replace("`", ""))

    return re.sub(r"\s+", "-", text).strip("-")


def headings(markdown: str) -> list[tuple[int, str]]:
    """Every heading worth listing, as (depth, text), skipping fenced code."""
    found, fenced = [], False

    for line in markdown.split("\n"):
        if line.startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue

        match = re.match(rf"^(#{{2,{MAX_DEPTH}}})\s+(.*)$", line)
        if match and match.group(2).strip() != "Contents":
            found.append((len(match.group(1)), match.group(2).strip()))

    return found


def build(markdown: str) -> str:
    """The contents block, indented by depth."""
    lines = []

    for depth, text in headings(markdown):
        lines.append(f"{'  ' * (depth - 2)}- [{text}](#{anchor(text)})")

    return f"{START}\n\n" + "\n".join(lines) + "\n"


def replace(markdown: str) -> str:
    """The document with its contents block rebuilt."""
    start = markdown.index(START)
    # the block runs to the next heading of any level
    rest = markdown.index("\n## ", start + len(START))

    return markdown[:start] + build(markdown) + markdown[rest:]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit non-zero when it is out of date")
    arguments = parser.parse_args()

    current = README.read_text(encoding="utf-8")
    rebuilt = replace(current)

    if arguments.check:
        if current != rebuilt:
            print("README.md's contents are out of date; run: uv run python scripts/toc.py")
            sys.exit(1)
        print("contents are up to date")
        return

    README.write_text(rebuilt, encoding="utf-8")
    print(f"rebuilt the contents from {len(headings(rebuilt))} headings")


if __name__ == "__main__":
    main()
