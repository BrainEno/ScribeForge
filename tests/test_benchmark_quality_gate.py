"""Benchmark corpus quality-gate tests (issues #62 / #68).

The project's own benchmark README demands 30-50 representative pages with
manually verified ground truth spanning clear/poor scans, chapter starts,
punctuation, footnotes, images, headers/footers and Chinese/English/Japanese
samples. The quality gate turns those prose requirements into machine-checked
rules so a too-small or single-language corpus fails loudly instead of
silently producing an engine ranking nobody can trust.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scribeforge.benchmark.manifest import BenchmarkCase, BenchmarkManifest, load_manifest
from scribeforge.benchmark.quality_gate import (
    QualityGateSettings,
    QualityIssue,
    check_corpus_quality,
    coverage_warnings,
    quality_gate_json,
    render_quality_report,
)


def _case(
    case_id: str,
    *,
    language: str = "zh",
    tags: tuple[str, ...] = ("body",),
    direction: str | None = None,
    image: str | None = None,
    ground_truth: str = "文本",
) -> BenchmarkCase:
    return BenchmarkCase(
        id=case_id,
        image=image or f"{case_id}.png",
        ground_truth=ground_truth,
        language=language,
        tags=tags,
        direction=direction,
    )


def _manifest(cases: list[BenchmarkCase]) -> BenchmarkManifest:
    return BenchmarkManifest(schema_version=1, cases=tuple(cases), license="CC0-1.0")


def _healthy_cases() -> list[BenchmarkCase]:
    cases: list[BenchmarkCase] = []
    for i in range(10):
        cases.append(_case(f"zh-{i}", language="zh", tags=("body", "clear")))
    for i in range(8):
        cases.append(
            _case(f"en-{i}", language="en", tags=("body", "two-column" if i == 0 else "body"))
        )
    for i in range(8):
        direction = "vertical-rl" if i < 2 else "horizontal-rl"
        cases.append(_case(f"ja-{i}", language="ja", tags=("body",), direction=direction))
    for i in range(4):
        cases.append(_case(f"zh-extra-{i}", language="zh", tags=("footnote", "poor-scan")))
    return cases


def test_healthy_corpus_passes_with_no_errors() -> None:
    issues = check_corpus_quality(_manifest(_healthy_cases()))
    assert [i for i in issues if i.severity == "error"] == []


def test_too_few_total_pages_is_an_error() -> None:
    cases = _healthy_cases()[:4]
    issues = check_corpus_quality(_manifest(cases))
    errors = [i for i in issues if i.severity == "error"]
    assert any(i.code == "corpus-too-small" for i in errors)


def test_missing_required_language_is_an_error() -> None:
    cases = [c for c in _healthy_cases() if c.language != "ja"]
    issues = check_corpus_quality(_manifest(cases))
    assert any(
        i.code == "language-missing" and "ja" in i.message for i in issues if i.severity == "error"
    )


def test_language_below_minimum_pages_is_an_error() -> None:
    cases = _healthy_cases()
    cases = [c for c in cases if not (c.language == "en" and c.id != "en-0")]
    # only en-0 remains for English
    issues = check_corpus_quality(_manifest(cases))
    assert any(
        i.code == "language-underrepresented" and "en" in i.message
        for i in issues
        if i.severity == "error"
    )


def test_japanese_without_vertical_sample_is_flagged() -> None:
    cases = [
        c if c.language != "ja" else _case(c.id, language="ja", direction="horizontal-rl")
        for c in _healthy_cases()
    ]
    issues = check_corpus_quality(_manifest(cases))
    assert any(i.code == "vertical-sample-missing" for i in issues)


def test_english_without_two_column_sample_is_flagged() -> None:
    cases = [
        c if c.language != "en" else _case(c.id, language="en", tags=("body",))
        for c in _healthy_cases()
    ]
    issues = check_corpus_quality(_manifest(cases))
    assert any(i.code == "two-column-sample-missing" for i in issues)


def test_case_without_layout_tag_warns() -> None:
    cases = _healthy_cases()
    cases[0] = _case("zh-0", language="zh", tags=())
    issues = check_corpus_quality(_manifest(cases))
    assert any(i.code == "layout-tag-missing" and "zh-0" in i.message for i in issues)


def test_duplicate_image_path_is_an_error() -> None:
    cases = _healthy_cases()
    cases[1] = _case("zh-1", language="zh", image=cases[0].image)
    issues = check_corpus_quality(_manifest(cases))
    assert any(i.code == "duplicate-image" for i in issues if i.severity == "error")


def test_empty_ground_truth_is_an_error() -> None:
    cases = _healthy_cases()
    cases[0] = _case("zh-0", language="zh", ground_truth="")
    issues = check_corpus_quality(_manifest(cases))
    assert any(
        i.code == "empty-ground-truth" and "zh-0" in i.message
        for i in issues
        if i.severity == "error"
    )


def test_blank_language_is_an_error() -> None:
    cases = _healthy_cases()
    cases[0] = _case("zh-0", language="  ")
    issues = check_corpus_quality(_manifest(cases))
    assert any(i.code == "blank-language" for i in issues if i.severity == "error")


def test_custom_settings_relax_requirements() -> None:
    settings = QualityGateSettings(
        min_total_pages=4, min_pages_per_language=1, required_languages=("zh",)
    )
    cases = [c for c in _healthy_cases() if c.language == "zh"][:4]
    issues = check_corpus_quality(_manifest(cases), settings=settings)
    assert [i for i in issues if i.severity == "error"] == []


def test_settings_validation() -> None:
    with pytest.raises(ValueError, match="min_total_pages"):
        QualityGateSettings(min_total_pages=0)
    with pytest.raises(ValueError, match="min_pages_per_language"):
        QualityGateSettings(min_pages_per_language=-1)
    with pytest.raises(ValueError, match="required_languages"):
        QualityGateSettings(required_languages=())


def test_render_quality_report_marks_pass_and_fail(tmp_path: Path) -> None:
    ok = render_quality_report(
        _manifest(_healthy_cases()), check_corpus_quality(_manifest(_healthy_cases()))
    )
    assert "PASS" in ok
    bad_cases = _healthy_cases()[:3]
    bad = render_quality_report(_manifest(bad_cases), check_corpus_quality(_manifest(bad_cases)))
    assert "FAIL" in bad
    assert "corpus-too-small" in bad


def test_quality_gate_json_is_stable_and_parseable() -> None:
    manifest = _manifest(_healthy_cases()[:2])
    issues = check_corpus_quality(manifest)
    payload = json.loads(quality_gate_json(manifest, issues))
    assert payload["case_count"] == 2
    assert payload["languages"] == {"zh": 2}
    assert isinstance(payload["issues"], list)
    assert all(set(i) == {"severity", "code", "message"} for i in payload["issues"])
    assert payload["ok"] is False


def test_coverage_warnings_for_report_embedding() -> None:
    class FakeScored:
        def __init__(self, language: str, tags: tuple[str, ...], direction: str | None) -> None:
            self.language = language
            self.tags = tags
            self.direction = direction

    cases = [FakeScored("zh", ("body",), None) for _ in range(3)]
    warnings = coverage_warnings(cases)
    assert any("language" in w for w in warnings)
    assert any("unstable" in w for w in warnings)

    ja_horizontal = [
        *[FakeScored("zh", ("body",), None) for _ in range(8)],
        FakeScored("ja", ("body",), "horizontal-rl"),
    ]
    ja_warnings = coverage_warnings(ja_horizontal)
    assert any("vertical" in w for w in ja_warnings)

    healthy = [
        *[FakeScored("zh", ("body",), None) for _ in range(10)],
        *[FakeScored("en", ("two-column",), None) for _ in range(10)],
        *[FakeScored("ja", ("body",), "vertical-rl") for _ in range(10)],
    ]
    assert coverage_warnings(healthy) == []


def test_load_manifest_roundtrip_through_gate(tmp_path: Path) -> None:
    raw = {
        "schema_version": 1,
        "cases": [
            {
                "id": f"zh-{i}",
                "image": f"zh-{i}.png",
                "ground_truth": "文本",
                "language": "zh",
                "tags": ["body"],
            }
            for i in range(30)
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    manifest = load_manifest(path)
    issues = check_corpus_quality(manifest)
    codes = {i.code for i in issues}
    assert "language-missing" in codes  # en and ja absent
    assert "corpus-too-small" not in codes  # 30 pages is enough


def test_issue_dataclass_is_immutable() -> None:
    issue = QualityIssue(severity="error", code="x", message="m")
    with pytest.raises(AttributeError):
        issue.code = "y"  # type: ignore[misc]


def _write_manifest(path: Path, cases: list[dict]) -> None:
    path.write_text(
        json.dumps({"schema_version": 1, "license": "CC0-1.0", "cases": cases}),
        encoding="utf-8",
    )


def _case_dict(case_id: str, language: str = "zh", tags=None, direction=None) -> dict:
    item = {
        "id": case_id,
        "image": f"{case_id}.png",
        "ground_truth": "文本",
        "language": language,
        "tags": tags if tags is not None else ["body"],
    }
    if direction is not None:
        item["direction"] = direction
    return item


def test_cli_benchmark_check_only_passes_healthy_corpus(tmp_path: Path) -> None:
    from scribeforge.cli import run

    cases = (
        [_case_dict(f"zh-{i}") for i in range(10)]
        + [
            _case_dict(f"en-{i}", "en", tags=["two-column"] if i == 0 else ["body"])
            for i in range(10)
        ]
        + [
            _case_dict(f"ja-{i}", "ja", direction="vertical-rl" if i < 2 else "horizontal-rl")
            for i in range(10)
        ]
    )
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest, cases)
    for case in cases:
        (tmp_path / case["image"]).write_bytes(b"png")
    out = tmp_path / "gate.md"

    code = run(["benchmark", "--manifest", str(manifest), "--out", str(out), "--check-only"])

    assert code == 0
    text = out.read_text(encoding="utf-8")
    assert "**PASS**" in text


def test_cli_benchmark_check_only_fails_thin_corpus_and_writes_json(tmp_path: Path) -> None:
    from scribeforge.cli import run

    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest, [_case_dict(f"zh-{i}") for i in range(4)])
    out = tmp_path / "gate.json"

    code = run(
        [
            "benchmark",
            "--manifest",
            str(manifest),
            "--out",
            str(out),
            "--check-only",
            "--quality-json",
        ]
    )

    assert code == 2
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["ok"] is False
    assert any(i["code"] == "corpus-too-small" for i in payload["issues"])


def test_render_report_embeds_coverage_warnings() -> None:
    from scribeforge.benchmark.report import render_report
    from scribeforge.benchmark.runner import BenchmarkReport, CaseScore, aggregate_case_scores

    case = CaseScore(
        case_id="zh-0",
        language="zh",
        tags=("body",),
        cer=0.01,
        wer=None,
        punctuation_accuracy=0.9,
        missing_lines=0,
        extra_lines=0,
        reading_order_errors=0,
        elapsed_seconds=1.0,
    )
    report = BenchmarkReport(engine="fake", cases=(case,), aggregate=aggregate_case_scores((case,)))
    text = render_report([report], corpus="c", manifest_sha256="x", generated_at="now")
    assert "## Corpus coverage warnings" in text
    assert "no corpus cases for required language(s)" in text


def test_vertical_sample_required_only_when_japanese_present() -> None:
    cases = [c for c in _healthy_cases() if c.language != "ja"]
    settings = QualityGateSettings(required_languages=("zh", "en"))
    issues = check_corpus_quality(_manifest(cases), settings=settings)
    assert not any(i.code == "vertical-sample-missing" for i in issues)


def test_two_column_tag_synonyms_accepted() -> None:
    for tag in ("two-column", "multi-column", "双栏", "双欄"):
        cases = [c for c in _healthy_cases() if c.language != "en"]
        cases += [
            _case(f"en-{i}", language="en", tags=("body",) if i else (tag,)) for i in range(8)
        ]
        issues = check_corpus_quality(_manifest(cases))
        assert not any(i.code == "two-column-sample-missing" for i in issues), tag


def test_vertical_direction_synonyms_accepted() -> None:
    for direction in ("vertical-rl", "vertical-lr"):
        cases = [c for c in _healthy_cases() if c.language != "ja"]
        cases += [
            _case(f"ja-{i}", language="ja", direction=direction if i == 0 else "horizontal-rl")
            for i in range(8)
        ]
        issues = check_corpus_quality(_manifest(cases))
        assert not any(i.code == "vertical-sample-missing" for i in issues), direction


def test_undeclared_language_warns_but_does_not_block() -> None:
    cases = _healthy_cases() + [_case(f"ko-{i}", language="ko") for i in range(8)]
    issues = check_corpus_quality(_manifest(cases))
    ko = [i for i in issues if i.code == "undeclared-language"]
    assert len(ko) == 1 and ko[0].severity == "warning"
    assert not any(i.severity == "error" for i in issues)


def test_language_matching_is_case_and_whitespace_insensitive() -> None:
    cases = _healthy_cases()
    cases[0] = _case("zh-0", language=" ZH ")
    issues = check_corpus_quality(_manifest(cases))
    assert not any(i.code in ("blank-language", "language-missing") for i in issues)


def test_multiple_duplicate_images_each_reported() -> None:
    cases = _healthy_cases()
    cases[1] = _case("zh-1", language="zh", image=cases[0].image)
    cases[2] = _case("zh-2", language="zh", image=cases[0].image)
    issues = check_corpus_quality(_manifest(cases))
    dups = [i for i in issues if i.code == "duplicate-image"]
    assert len(dups) == 2


def test_quality_gate_json_pass_case() -> None:
    manifest = _manifest(_healthy_cases())
    payload = json.loads(quality_gate_json(manifest, check_corpus_quality(manifest)))
    assert payload["ok"] is True
    assert payload["issues"] == []
    assert payload["languages"] == {"en": 8, "ja": 8, "zh": 14}


def test_render_quality_report_escapes_pipes() -> None:
    manifest = _manifest([_case("a|b", language="zh", image="x|y.png", tags=())])
    issues = check_corpus_quality(manifest)
    text = render_quality_report(manifest, issues)
    assert "a\\|b" in text


def test_coverage_warnings_underrepresented_language() -> None:
    class FakeScored:
        def __init__(self, language: str) -> None:
            self.language = language
            self.tags = ("body",)
            self.direction = None

    cases = (
        [FakeScored("zh") for _ in range(10)]
        + [FakeScored("en") for _ in range(10)]
        + [FakeScored("ja") for _ in range(3)]
    )
    warnings = coverage_warnings(cases)
    assert any("underrepresented" in w and "ja (3/8)" in w for w in warnings)


def test_vertical_requirement_can_be_disabled() -> None:
    settings = QualityGateSettings(require_vertical_sample=False, require_two_column_sample=False)
    cases = [
        c if c.language != "ja" else _case(c.id, language="ja", direction="horizontal-rl")
        for c in _healthy_cases()
    ]
    issues = check_corpus_quality(_manifest(cases), settings=settings)
    assert not any(i.code == "vertical-sample-missing" for i in issues)


def test_layout_tag_requirement_can_be_disabled() -> None:
    settings = QualityGateSettings(require_layout_tag_per_case=False)
    cases = _healthy_cases()
    cases[0] = _case("zh-0", language="zh", tags=())
    issues = check_corpus_quality(_manifest(cases), settings=settings)
    assert not any(i.code == "layout-tag-missing" for i in issues)


def test_render_report_warns_once_across_variants() -> None:
    from scribeforge.benchmark.report import render_report
    from scribeforge.benchmark.runner import BenchmarkReport, CaseScore, aggregate_case_scores

    def make_case(cid: str) -> CaseScore:
        return CaseScore(
            case_id=cid,
            language="zh",
            tags=("body",),
            cer=0.02,
            wer=None,
            punctuation_accuracy=0.9,
            missing_lines=0,
            extra_lines=0,
            reading_order_errors=0,
            elapsed_seconds=1.0,
        )

    case = make_case("zh-0")
    reports = [
        BenchmarkReport(engine=f"e{i}", cases=(case,), aggregate=aggregate_case_scores((case,)))
        for i in range(3)
    ]
    text = render_report(reports, corpus="c", manifest_sha256="x", generated_at="now")
    assert text.count("## Corpus coverage warnings") == 1
    assert text.count("no corpus cases for required language(s)") == 1


def test_check_only_defaults_to_markdown_not_json(tmp_path: Path) -> None:
    from scribeforge.cli import run

    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest, [_case_dict(f"zh-{i}") for i in range(4)])
    out = tmp_path / "gate.md"
    code = run(["benchmark", "--manifest", str(manifest), "--out", str(out), "--check-only"])
    assert code == 2
    text = out.read_text(encoding="utf-8")
    assert text.startswith("# Benchmark corpus quality gate")
    with pytest.raises(json.JSONDecodeError):
        json.loads(text)


def test_gate_uses_manifest_review_status_in_json(tmp_path: Path) -> None:
    raw = {
        "schema_version": 1,
        "ground_truth_review_status": "double-proofread",
        "cases": [_case_dict(f"zh-{i}") for i in range(30)],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    manifest = load_manifest(path)
    assert manifest.ground_truth_review_status == "double-proofread"
    issues = check_corpus_quality(manifest)
    assert any(i.code == "language-missing" for i in issues)


def test_cli_check_only_json_includes_license_fields(tmp_path: Path) -> None:
    import io

    from scribeforge.cli import run

    root = tmp_path
    cases = []
    for i in range(30):
        (root / f"zh-{i}.png").write_bytes(b"png")
        cases.append(_case_dict(f"zh-{i}"))
    raw = {
        "schema_version": 1,
        "license": "CC0-1.0",
        "license_url": "https://example.org/cc0",
        "cases": cases,
    }
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(raw), encoding="utf-8")
    out = tmp_path / "gate.json"
    run(
        [
            "benchmark",
            "--manifest",
            str(manifest),
            "--out",
            str(out),
            "--check-only",
            "--quality-json",
        ],
        stdout=io.StringIO(),
    )
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["license"] == "CC0-1.0"
    assert payload["license_url"] == "https://example.org/cc0"
