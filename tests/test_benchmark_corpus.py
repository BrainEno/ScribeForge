from __future__ import annotations

from pathlib import Path

from scribeforge.benchmark.corpus import validate_corpus
from scribeforge.benchmark.manifest import BenchmarkCase, BenchmarkManifest


def _case(case_id: str = "case-1") -> BenchmarkCase:
    return BenchmarkCase(case_id, f"{case_id}.png", f"{case_id}.txt", "zh", ("body",))


def _manifest(*cases: BenchmarkCase) -> BenchmarkManifest:
    return BenchmarkManifest(1, cases)


def test_validate_corpus_accepts_a_complete_case(tmp_path: Path) -> None:
    (tmp_path / "case-1.png").write_bytes(b"image")
    (tmp_path / "case-1.txt").write_text("你好", encoding="utf-8")

    assert validate_corpus(_manifest(_case()), tmp_path) == ()


def test_validate_corpus_reports_a_missing_image(tmp_path: Path) -> None:
    (tmp_path / "case-1.txt").write_text("你好", encoding="utf-8")

    problems = validate_corpus(_manifest(_case()), tmp_path)

    assert len(problems) == 1
    assert problems[0].case_id == "case-1"
    assert problems[0].problem == "missing image: case-1.png"


def test_validate_corpus_reports_an_empty_image(tmp_path: Path) -> None:
    (tmp_path / "case-1.png").write_bytes(b"")
    (tmp_path / "case-1.txt").write_text("你好", encoding="utf-8")

    problems = validate_corpus(_manifest(_case()), tmp_path)

    assert [problem.problem for problem in problems] == ["empty image: case-1.png"]


def test_validate_corpus_reports_a_missing_ground_truth(tmp_path: Path) -> None:
    (tmp_path / "case-1.png").write_bytes(b"image")

    problems = validate_corpus(_manifest(_case()), tmp_path)

    assert [problem.problem for problem in problems] == [
        "missing ground truth: case-1.txt"
    ]


def test_validate_corpus_reports_blank_ground_truth(tmp_path: Path) -> None:
    (tmp_path / "case-1.png").write_bytes(b"image")
    (tmp_path / "case-1.txt").write_text("  \n", encoding="utf-8")

    problems = validate_corpus(_manifest(_case()), tmp_path)

    assert [problem.problem for problem in problems] == ["ground truth is blank: case-1.txt"]


def test_validate_corpus_reports_every_broken_case(tmp_path: Path) -> None:
    problems = validate_corpus(_manifest(_case("a"), _case("b")), tmp_path)

    assert [(problem.case_id, problem.problem) for problem in problems] == [
        ("a", "missing image: a.png"),
        ("a", "missing ground truth: a.txt"),
        ("b", "missing image: b.png"),
        ("b", "missing ground truth: b.txt"),
    ]


def test_validate_corpus_rejects_an_empty_manifest(tmp_path: Path) -> None:
    problems = validate_corpus(_manifest(), tmp_path)

    assert [problem.problem for problem in problems] == ["manifest contains no cases"]
