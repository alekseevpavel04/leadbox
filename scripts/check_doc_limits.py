"""Check the public texts against the task's literal limits.

Length is counted the way QUALITY.md section 4 defines a page: visible text with spaces, markdown
markup removed, link text and image captions kept, HTML comments dropped. One A4 page at 11 pt is
3000 characters, so the product sketch is 1500-3000 and the review is at most 1500.

Also fails on an em dash in any public text and on an image without a caption: the alt text must be
non-empty and the next non-blank line must be a caption paragraph, because the AI screener may not
see images at all.

Usage: .venv/Scripts/python.exe scripts/check_doc_limits.py [repo_root]
"""

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

EM_DASH = "\N{EM DASH}"

# (path, min chars, max chars); None means the length is not limited.
DOCS = [
    ("docs/PRODUCT.md", 1500, 3000),
    ("docs/REVIEW.md", None, 1500),
    ("README.md", None, None),
]
REQUIRED = {"docs/PRODUCT.md", "docs/REVIEW.md"}

COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
AUTOLINK = re.compile(r"<(https?://[^>]+)>")
HTML_TAG = re.compile(r"</?[A-Za-z][^>]*>")
HEADING = re.compile(r"^\s{0,3}#{1,6}\s+")
BULLET = re.compile(r"^\s*[-*+]\s+")
QUOTE = re.compile(r"^\s*>\s?")
RULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")
# Emphasis markers only; a single "_" stays because identifiers like notified_at are visible text.
EMPHASIS = re.compile(r"\*{1,3}|_{2,3}")


def visible_lines(markdown: str) -> list[str]:
    lines = []
    for raw in COMMENT.sub("", markdown).splitlines():
        if FENCE.match(raw) or RULE.match(raw) or TABLE_SEPARATOR.match(raw):
            continue
        line = HEADING.sub("", raw)
        line = QUOTE.sub("", line)
        line = BULLET.sub("", line)
        line = IMAGE.sub(r"\1", line)
        line = LINK.sub(r"\1", line)
        line = AUTOLINK.sub(r"\1", line)
        line = HTML_TAG.sub("", line)
        line = EMPHASIS.sub("", line).replace("`", "").replace("|", " ")
        line = " ".join(line.split())
        if line:
            lines.append(line)
    return lines


def count_chars(markdown: str) -> int:
    """Characters with spaces; line breaks between paragraphs are not counted."""
    return sum(len(line) for line in visible_lines(markdown))


def caption_problems(markdown: str) -> list[str]:
    lines = COMMENT.sub("", markdown).splitlines()
    problems = []
    for index, line in enumerate(lines):
        for match in IMAGE.finditer(line):
            image = match.group(0)
            if not match.group(1).strip():
                problems.append(f"{image}: empty alt text")
            rest = line[match.end() :].strip()
            following = next((x.strip() for x in lines[index + 1 :] if x.strip()), "")
            caption = rest or following
            if not caption or caption.startswith(("#", "!", "|", "```")):
                problems.append(f"{image}: no caption paragraph after the image")
    return problems


@dataclass
class Report:
    path: str
    chars: int | None = None
    limits: str = ""
    problems: list[str] = field(default_factory=list)


def check(root: Path) -> list[Report]:
    reports = []
    for path, low, high in DOCS:
        report = Report(path)
        file = root / path
        if not file.exists():
            if path in REQUIRED:
                report.problems.append("file is missing")
            reports.append(report)
            continue
        text = file.read_text(encoding="utf-8")
        report.chars = count_chars(text)
        if low is not None or high is not None:
            report.limits = f"{low or 0}-{high}" if high else f">={low}"
        if low is not None and report.chars < low:
            report.problems.append(f"{report.chars} chars, below {low}")
        if high is not None and report.chars > high:
            report.problems.append(f"{report.chars} chars, above {high}")
        for number, line in enumerate(text.splitlines(), start=1):
            if EM_DASH in line:
                report.problems.append(f"em dash on line {number}")
        report.problems.extend(caption_problems(text))
        reports.append(report)
    return reports


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parents[1]
    reports = check(root)
    print(f"{'file':<18} {'chars':>6}  {'limit':<10} status")
    for report in reports:
        chars = "-" if report.chars is None else str(report.chars)
        status = "ok" if not report.problems else "FAIL"
        if report.chars is None and not report.problems:
            status = "absent"
        print(f"{report.path:<18} {chars:>6}  {report.limits or '-':<10} {status}")
        for problem in report.problems:
            print(f"  {problem}")
    return 1 if any(report.problems for report in reports) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
