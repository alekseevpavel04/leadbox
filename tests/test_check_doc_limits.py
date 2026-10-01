import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_doc_limits.py"
spec = importlib.util.spec_from_file_location("check_doc_limits", SCRIPT)
limits = importlib.util.module_from_spec(spec)
spec.loader.exec_module(limits)


def write_docs(root: Path, product: str, review: str, readme: str | None = None) -> None:
    (root / "docs").mkdir()
    (root / "docs" / "PRODUCT.md").write_text(product, encoding="utf-8")
    (root / "docs" / "REVIEW.md").write_text(review, encoding="utf-8")
    if readme is not None:
        (root / "README.md").write_text(readme, encoding="utf-8")


def problems(root: Path) -> dict[str, list[str]]:
    return {report.path: report.problems for report in limits.check(root)}


@pytest.mark.parametrize(
    ("markdown", "expected"),
    [
        ("## Заголовок", "Заголовок"),
        ("**жирный** и *курсив*", "жирный и курсив"),
        ("[бот](https://t.me/x) рядом", "бот рядом"),
        ("![Схема](img/scheme.png)", "Схема"),
        ("текст <!-- TODO T4.2: проверить --> дальше", "текст дальше"),
        ("- пункт списка", "пункт списка"),
        ("поле `notified_at`", "поле notified_at"),
        ("> цитата", "цитата"),
    ],
)
def test_markup_is_not_counted(markdown, expected):
    assert limits.visible_lines(markdown) == [expected]
    assert limits.count_chars(markdown) == len(expected)


def test_multiline_comment_and_blank_lines_are_not_counted():
    markdown = "раз\n\n<!-- предложение,\nПавел выбирает -->\n\n---\n\nдва"
    assert limits.visible_lines(markdown) == ["раз", "два"]
    assert limits.count_chars(markdown) == 6


def test_within_limits_passes(tmp_path):
    write_docs(tmp_path, "а" * 2000, "б" * 1500)
    assert limits.main(["x", str(tmp_path)]) == 0


def test_short_product_and_long_review_fail(tmp_path):
    write_docs(tmp_path, "а" * 1499, "б" * 1501)
    found = problems(tmp_path)
    assert found["docs/PRODUCT.md"] == ["1499 chars, below 1500"]
    assert found["docs/REVIEW.md"] == ["1501 chars, above 1500"]
    assert limits.main(["x", str(tmp_path)]) == 1


def test_em_dash_fails_in_any_public_text(tmp_path):
    write_docs(tmp_path, "а" * 2000, "б", readme="строка\nтут \N{EM DASH} тире")
    assert problems(tmp_path)["README.md"] == ["em dash on line 2"]


def test_image_needs_alt_and_caption(tmp_path):
    product = "а" * 2000 + "\n\n![](img/a.png)\n\nподпись\n\n![Схема](img/b.png)\n\n## Дальше"
    write_docs(tmp_path, product, "б")
    assert problems(tmp_path)["docs/PRODUCT.md"] == [
        "![](img/a.png): empty alt text",
        "![Схема](img/b.png): no caption paragraph after the image",
    ]


def test_image_with_caption_passes(tmp_path):
    write_docs(tmp_path, "а" * 2000, "![Промпт](img/p.png)\nПодпись: текст промпта.")
    assert problems(tmp_path)["docs/REVIEW.md"] == []


def test_missing_required_doc_fails_and_missing_readme_does_not(tmp_path):
    (tmp_path / "docs").mkdir()
    found = problems(tmp_path)
    assert found["docs/PRODUCT.md"] == ["file is missing"]
    assert found["README.md"] == []
