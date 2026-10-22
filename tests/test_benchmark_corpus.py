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



def test_validate_corpus_rejects_absolute_paths_even_when_targets_exist(
    tmp_path: Path,
) -> None:
    root = tmp_path / "corpus"
    root.mkdir()
    outside_image = tmp_path / "outside.png"
    outside_truth = tmp_path / "outside.txt"
    outside_image.write_bytes(b"image")
    outside_truth.write_text("must not be read", encoding="utf-8")
    case = BenchmarkCase(
        "outside",
        str(outside_image),
        str(outside_truth),
        "en",
        (),
    )

    problems = validate_corpus(_manifest(case), root)

    assert [(problem.case_id, problem.problem) for problem in problems] == [
        ("outside", f"absolute image path is not allowed: {outside_image}"),
        ("outside", f"absolute ground truth path is not allowed: {outside_truth}"),
    ]


def test_validate_corpus_rejects_parent_traversal_paths_even_when_targets_exist(
    tmp_path: Path,
) -> None:
    root = tmp_path / "corpus"
    root.mkdir()
    (tmp_path / "outside.png").write_bytes(b"image")
    (tmp_path / "outside.txt").write_text("must not be read", encoding="utf-8")
    case = BenchmarkCase("outside", "../outside.png", "../outside.txt", "en", ())

    problems = validate_corpus(_manifest(case), root)

    assert [(problem.case_id, problem.problem) for problem in problems] == [
        ("outside", "image path escapes corpus root: ../outside.png"),
        ("outside", "ground truth path escapes corpus root: ../outside.txt"),
    ]


def test_validate_corpus_rejects_symlink_that_resolves_outside_root(
    tmp_path: Path,
) -> None:
    import pytest

    root = tmp_path / "corpus"
    root.mkdir()
    outside_image = tmp_path / "outside.png"
    outside_image.write_bytes(b"image")
    try:
        (root / "linked.png").symlink_to(outside_image)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks are not available in this environment: {exc}")
    (root / "case-1.txt").write_text("verified", encoding="utf-8")
    case = BenchmarkCase("linked", "linked.png", "case-1.txt", "en", ())

    problems = validate_corpus(_manifest(case), root)

    assert [(problem.case_id, problem.problem) for problem in problems] == [
        ("linked", "image path escapes corpus root: linked.png")
    ]
