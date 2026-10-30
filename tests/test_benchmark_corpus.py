from __future__ import annotations

import json
from pathlib import Path

import pytest

from scribeforge.benchmark.corpus import fingerprint_corpus, validate_corpus
from scribeforge.benchmark.manifest import BenchmarkCase, BenchmarkManifest, load_manifest


def _case(case_id: str = "case-1") -> BenchmarkCase:
    return BenchmarkCase(case_id, f"{case_id}.png", f"{case_id}.txt", "zh", ("body",))


def _manifest(*cases: BenchmarkCase) -> BenchmarkManifest:
    return BenchmarkManifest(1, cases)


def test_validate_corpus_accepts_a_complete_case(tmp_path: Path) -> None:
    (tmp_path / "case-1.png").write_bytes(b"image")
    (tmp_path / "case-1.txt").write_text("你好", encoding="utf-8")

    assert validate_corpus(_manifest(_case()), tmp_path) == ()


def test_corpus_fingerprint_binds_manifest_images_and_transcriptions(
    tmp_path: Path,
) -> None:
    image = tmp_path / "case-1.png"
    truth = tmp_path / "case-1.txt"
    image.write_bytes(b"real scan bytes")
    truth.write_text("初始参照文本", encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    manifest_data = {
        "schema_version": 1,
        "description": "corpus revision one",
        "cases": [
            {
                "id": "case-1",
                "image": image.name,
                "ground_truth": truth.name,
                "language": "zh-Hant",
                "tags": ["vertical"],
                "direction": "vertical-rl",
            }
        ],
    }
    manifest_path.write_text(json.dumps(manifest_data), encoding="utf-8")
    manifest = load_manifest(manifest_path)

    original = fingerprint_corpus(manifest_path, manifest, tmp_path)

    assert len(original.manifest_sha256) == 64
    assert len(original.corpus_sha256) == 64
    assert fingerprint_corpus(manifest_path, manifest, tmp_path) == original

    truth.write_text("校訂后的参照文本", encoding="utf-8")
    transcription_changed = fingerprint_corpus(manifest_path, manifest, tmp_path)
    assert transcription_changed.corpus_sha256 != original.corpus_sha256

    image.write_bytes(b"changed scan bytes")
    image_changed = fingerprint_corpus(manifest_path, manifest, tmp_path)
    assert image_changed.corpus_sha256 != transcription_changed.corpus_sha256

    manifest_data["description"] = "corpus revision two"
    manifest_path.write_text(json.dumps(manifest_data), encoding="utf-8")
    manifest_changed = fingerprint_corpus(
        manifest_path, load_manifest(manifest_path), tmp_path
    )
    assert manifest_changed.manifest_sha256 != image_changed.manifest_sha256
    assert manifest_changed.corpus_sha256 != image_changed.corpus_sha256


def test_fingerprint_refuses_a_manifest_changed_after_loading(tmp_path: Path) -> None:
    (tmp_path / "case-1.png").write_bytes(b"real scan bytes")
    (tmp_path / "case-1.txt").write_text("参照文本", encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "cases": [
                    {
                        "id": "case-1",
                        "image": "case-1.png",
                        "ground_truth": "case-1.txt",
                        "language": "zh-Hant",
                        "tags": ["vertical"],
                        "direction": "vertical-rl",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest = load_manifest(manifest_path)
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8").replace("vertical", "body"),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="manifest changed since it was loaded"):
        fingerprint_corpus(manifest_path, manifest, tmp_path)


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
