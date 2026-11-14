"""Corpus annotation support for issues #62/#68: per-page metadata specs and
an annotation-status report that shows exactly what a multilingual corpus is
still missing (languages, directions, empty transcriptions) before the
quality gate is consulted.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from scribeforge.benchmark.annotation import (
    annotation_status,
    annotation_status_json,
    load_page_spec,
    render_annotation_status,
    resolve_case_metadata,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _builder():
    sys.path.insert(0, str(REPO_ROOT / "benchmark" / "analysis"))
    try:
        import build_corpus  # type: ignore[import-not-found]
    finally:
        sys.path.pop(0)
    return build_corpus


# ---------------------------------------------------------------------------
# load_page_spec / resolve_case_metadata
# ---------------------------------------------------------------------------


def test_load_page_spec_reads_per_page_overrides(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(
        json.dumps(
            {
                "pages": {
                    "3": {"language": "ja", "direction": "vertical-rl", "tags": ["vertical"]},
                    "7": {"language": "en", "tags": ["two-column", "body"]},
                }
            }
        ),
        encoding="utf-8",
    )

    spec = load_page_spec(spec_path)

    assert set(spec) == {3, 7}
    assert spec[3].language == "ja"
    assert spec[3].direction == "vertical-rl"
    assert spec[3].tags == ("vertical",)
    assert spec[7].language == "en"
    assert spec[7].direction is None
    assert spec[7].tags == ("two-column", "body")


def test_load_page_spec_none_is_empty() -> None:
    assert load_page_spec(None) == {}


def test_load_page_spec_rejects_bad_shapes(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"pages": {"x": {}}}), encoding="utf-8")
    with pytest.raises(ValueError, match="page index"):
        load_page_spec(bad)

    bad.write_text(json.dumps({"pages": {"3": {"direction": "diagonal"}}}), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown direction"):
        load_page_spec(bad)

    bad.write_text(json.dumps({"pages": {"3": {"tags": "vertical"}}}), encoding="utf-8")
    with pytest.raises(TypeError, match="tags"):
        load_page_spec(bad)


def test_resolve_case_metadata_defaults_and_override() -> None:
    from scribeforge.benchmark.annotation import PageOverride

    spec_map = {5: PageOverride(language="ja", direction="vertical-rl", tags=("vertical",))}

    language, direction, tags = resolve_case_metadata(
        page_index=1,
        default_language="zh-Hant",
        default_direction="horizontal-lr",
        default_tags=("body",),
        spec=spec_map,
    )
    assert (language, direction, tags) == ("zh-Hant", "horizontal-lr", ("body",))

    language, direction, tags = resolve_case_metadata(
        page_index=5,
        default_language="zh-Hant",
        default_direction="horizontal-lr",
        default_tags=("body",),
        spec=spec_map,
    )
    assert (language, direction, tags) == ("ja", "vertical-rl", ("vertical",))


def test_resolve_case_metadata_partial_override_keeps_other_defaults() -> None:
    from scribeforge.benchmark.annotation import PageOverride

    spec_map = {2: PageOverride(language="en", direction=None, tags=())}

    language, direction, tags = resolve_case_metadata(
        page_index=2,
        default_language="zh",
        default_direction=None,
        default_tags=("body",),
        spec=spec_map,
    )
    # Only the language is overridden; direction/tags fall back to defaults.
    assert language == "en"
    assert direction is None
    assert tags == ("body",)


# ---------------------------------------------------------------------------
# build_corpus with a page spec
# ---------------------------------------------------------------------------


def test_builder_applies_page_spec_per_page(tmp_path: Path) -> None:
    builder = _builder()
    pdf = REPO_ROOT / "benchmark" / "V..pdf"
    if not pdf.is_file():
        pytest.skip("benchmark PDF not present")

    spec_path = tmp_path / "spec.json"
    spec_path.write_text(
        json.dumps({"pages": {"1": {"language": "ja", "direction": "vertical-rl"}}}),
        encoding="utf-8",
    )

    manifest = builder.build_corpus(
        pdf=pdf,
        out=tmp_path / "corpus",
        name="mix",
        pages=(0, 1),
        direction=None,
        language="zh",
        dpi=72,
        source_note="spec test",
        page_spec=load_page_spec(spec_path),
    )

    cases = {case["id"]: case for case in manifest["cases"]}
    assert cases["mix-000"]["language"] == "zh"
    assert "direction" not in cases["mix-000"]
    assert cases["mix-001"]["language"] == "ja"
    assert cases["mix-001"]["direction"] == "vertical-rl"


def test_builder_rejects_spec_for_unknown_page(tmp_path: Path) -> None:
    builder = _builder()
    from scribeforge.benchmark.annotation import PageOverride

    with pytest.raises(ValueError, match="not in the selected pages"):
        builder.build_corpus(
            pdf=tmp_path / "missing.pdf",
            out=tmp_path / "corpus",
            name="x",
            pages=(1,),
            direction=None,
            language="zh",
            dpi=100,
            source_note="",
            page_spec={9: PageOverride(language="en", direction=None, tags=())},
        )


# ---------------------------------------------------------------------------
# annotation_status report
# ---------------------------------------------------------------------------


def _corpus(root: Path, *, empty_truth: bool = False) -> Path:
    (root / "images").mkdir(parents=True)
    (root / "ground_truth").mkdir(parents=True)
    (root / "images" / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (root / "images" / "b.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (root / "ground_truth" / "a.txt").write_text("鸦", encoding="utf-8")
    (root / "ground_truth" / "b.txt").write_text("", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "license": "CC0-1.0",
        "ground_truth_review_status": "unreviewed",
        "cases": [
            {
                "id": "a",
                "image": "images/a.png",
                "ground_truth": "ground_truth/a.txt",
                "language": "zh",
                "tags": ["body"],
                "direction": "horizontal-lr",
                "source": "test",
            },
            {
                "id": "b",
                "image": "images/b.png",
                "ground_truth": "ground_truth/b.txt",
                "language": "ja",
                "tags": ["vertical"],
                "direction": "vertical-rl",
                "source": "test",
            },
        ],
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root / "manifest.json"


def test_annotation_status_counts_languages_directions_and_gaps(tmp_path: Path) -> None:
    manifest_path = _corpus(tmp_path / "corpus", empty_truth=True)

    status = annotation_status(manifest_path.parent)

    assert status.case_count == 2
    assert status.languages == {"zh": 1, "ja": 1}
    assert status.directions == {"horizontal-lr": 1, "vertical-rl": 1}
    assert status.empty_ground_truths == ("b",)
    assert status.review_status == "unreviewed"
    assert status.license == "CC0-1.0"
    assert status.annotation_complete is False


def test_annotation_status_markdown_lists_gaps_and_gate(tmp_path: Path) -> None:
    manifest_path = _corpus(tmp_path / "corpus", empty_truth=True)

    text = render_annotation_status(annotation_status(manifest_path.parent))

    assert "# Corpus annotation status" in text
    assert "| Language | Pages |" in text
    assert "| zh | 1 |" in text and "| ja | 1 |" in text
    assert "empty/missing ground truth" in text and "`b`" in text
    assert "## Quality gate" in text  # embeds the gate verdict for the corpus


def test_annotation_status_json_is_schema_v1_and_stable(tmp_path: Path) -> None:
    manifest_path = _corpus(tmp_path / "corpus")

    first = annotation_status_json(annotation_status(manifest_path.parent))
    second = annotation_status_json(annotation_status(manifest_path.parent))

    assert first == second
    payload = json.loads(first)
    assert payload["schema_version"] == 1
    assert payload["languages"] == {"ja": 1, "zh": 1}
    assert payload["empty_ground_truths"] == ["b"]
    assert payload["annotation_complete"] is False


def test_annotation_status_complete_corpus(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    (root / "images").mkdir(parents=True)
    (root / "ground_truth").mkdir(parents=True)
    (root / "images" / "a.png").write_bytes(b"\x89PNG")
    (root / "ground_truth" / "a.txt").write_text("text", encoding="utf-8")
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "license": "CC0-1.0",
                "cases": [
                    {
                        "id": "a",
                        "image": "images/a.png",
                        "ground_truth": "ground_truth/a.txt",
                        "language": "zh",
                        "tags": ["body"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    status = annotation_status(root)
    assert status.annotation_complete is True
    assert status.empty_ground_truths == ()


def test_cli_benchmark_annotation_status_writes_report_and_fails_on_gaps(tmp_path: Path) -> None:
    import io

    from scribeforge.cli import run

    manifest_path = _corpus(tmp_path / "corpus", empty_truth=True)
    out = tmp_path / "status.md"
    stdout = io.StringIO()

    code = run(
        [
            "benchmark",
            "--manifest",
            str(manifest_path),
            "--out",
            str(out),
            "--annotation-status",
        ],
        stdout=stdout,
    )

    assert code == 2  # empty ground truth = annotation incomplete
    text = out.read_text(encoding="utf-8")
    assert "# Corpus annotation status" in text
    assert "| ja | 1 |" in text


# ---------------------------------------------------------------------------
# more coverage: CLI JSON variant, real repo corpus, determinism, spec edges
# ---------------------------------------------------------------------------


def test_cli_benchmark_annotation_status_quality_json(tmp_path: Path) -> None:
    import io

    from scribeforge.cli import run

    manifest_path = _corpus(tmp_path / "corpus")
    out = tmp_path / "status.json"

    code = run(
        [
            "benchmark",
            "--manifest",
            str(manifest_path),
            "--out",
            str(out),
            "--annotation-status",
            "--quality-json",
        ],
        stdout=io.StringIO(),
    )

    assert code == 2
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["annotation_complete"] is False
    assert payload["languages"] == {"ja": 1, "zh": 1}


def test_annotation_status_on_shipped_example_manifest(tmp_path: Path) -> None:
    """The shipped example manifest must produce a well-formed status."""
    example = REPO_ROOT / "benchmark" / "manifest.example.json"
    corpus_dir = tmp_path / "example"
    corpus_dir.mkdir()
    (corpus_dir / "manifest.json").write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    status = annotation_status(corpus_dir)

    assert status.case_count == 3  # zh + en + ja example cases
    assert set(status.languages) == {"en", "ja", "zh"}
    assert "vertical-rl" in status.directions


def test_render_annotation_status_is_deterministic(tmp_path: Path) -> None:
    manifest_path = _corpus(tmp_path / "corpus", empty_truth=True)
    first = render_annotation_status(annotation_status(manifest_path.parent))
    second = render_annotation_status(annotation_status(manifest_path.parent))
    assert first == second


def test_load_page_spec_empty_pages_object(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps({"pages": {}}), encoding="utf-8")
    assert load_page_spec(spec_path) == {}


def test_load_page_spec_rejects_non_object_root(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps([1, 2]), encoding="utf-8")
    with pytest.raises(TypeError, match="JSON object"):
        load_page_spec(spec_path)


def test_resolve_case_metadata_empty_tags_override_keeps_default() -> None:
    from scribeforge.benchmark.annotation import PageOverride

    spec_map = {0: PageOverride(language=None, direction="vertical-rl", tags=())}
    language, direction, tags = resolve_case_metadata(
        page_index=0,
        default_language="zh",
        default_direction="horizontal-lr",
        default_tags=("body", "footnote"),
        spec=spec_map,
    )
    assert language == "zh"  # None override keeps the default
    assert direction == "vertical-rl"
    assert tags == ("body", "footnote")  # empty override tags keep the default


def test_builder_cli_accepts_page_spec_flag(tmp_path: Path) -> None:
    """The analysis script's argparse surface exposes --page-spec."""
    builder = _builder()
    import inspect

    source = inspect.getsource(builder.main)
    assert "--page-spec" in source


def test_annotation_status_flags_missing_image(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    (root / "images").mkdir(parents=True)
    (root / "ground_truth").mkdir(parents=True)
    (root / "ground_truth" / "a.txt").write_text("text", encoding="utf-8")
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "license": "CC0-1.0",
                "cases": [
                    {
                        "id": "a",
                        "image": "images/missing.png",
                        "ground_truth": "ground_truth/a.txt",
                        "language": "zh",
                        "tags": ["body"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    status = annotation_status(root)
    assert status.missing_images == ("a",)
    assert status.annotation_complete is False
    text = render_annotation_status(status)
    assert "missing images (1): `a`" in text


def test_annotation_status_gate_section_embeds_issue_codes(tmp_path: Path) -> None:
    manifest_path = _corpus(tmp_path / "corpus", empty_truth=True)
    text = render_annotation_status(annotation_status(manifest_path.parent))

    # The embedded gate must name the blocking issues (small corpus, languages).
    assert "corpus-too-small" in text
    assert "language-underrepresented" in text


def test_cli_annotation_status_exit_zero_when_complete(tmp_path: Path) -> None:
    import io

    from scribeforge.cli import run

    root = tmp_path / "corpus"
    (root / "images").mkdir(parents=True)
    (root / "ground_truth").mkdir(parents=True)
    (root / "images" / "a.png").write_bytes(b"\x89PNG")
    (root / "ground_truth" / "a.txt").write_text("text", encoding="utf-8")
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "license": "CC0-1.0",
                "cases": [
                    {
                        "id": "a",
                        "image": "images/a.png",
                        "ground_truth": "ground_truth/a.txt",
                        "language": "zh",
                        "tags": ["body"],
                        "source": "test",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    out = tmp_path / "status.md"
    code = run(
        ["benchmark", "--manifest", str(manifest), "--out", str(out), "--annotation-status"],
        stdout=io.StringIO(),
    )

    assert code == 0
    assert "annotation complete: yes" in out.read_text(encoding="utf-8")


def test_annotation_status_blank_language_is_visible(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    (root / "images").mkdir(parents=True)
    (root / "ground_truth").mkdir(parents=True)
    (root / "images" / "a.png").write_bytes(b"\x89PNG")
    (root / "ground_truth" / "a.txt").write_text("x", encoding="utf-8")
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "cases": [
                    {
                        "id": "a",
                        "image": "images/a.png",
                        "ground_truth": "ground_truth/a.txt",
                        "language": "  ",
                        "tags": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    status = annotation_status(root)
    assert status.languages == {"(blank)": 1}
    assert status.annotation_complete is True  # image+truth present; language gap shows in gate
