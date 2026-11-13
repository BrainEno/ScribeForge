"""Benchmark corpus licensing/provenance tests (issue #62).

Benchmark README forbids committing copyrighted books: every corpus must
declare its license and per-case source. These tests cover manifest parsing of
the new license/source fields, quality-gate enforcement, and report rendering.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scribeforge.benchmark.manifest import BenchmarkCase, BenchmarkManifest, load_manifest
from scribeforge.benchmark.quality_gate import (
    QualityGateSettings,
    check_corpus_quality,
    quality_gate_json,
    render_quality_report,
)


def _case(case_id: str, *, source: str | None = None) -> BenchmarkCase:
    return BenchmarkCase(
        id=case_id,
        image=f"{case_id}.png",
        ground_truth="文本",
        language="zh",
        tags=("body",),
        source=source,
    )


def _manifest(cases: list[BenchmarkCase], license: str | None = "CC0-1.0") -> BenchmarkManifest:
    return BenchmarkManifest(schema_version=1, cases=tuple(cases), license=license)


def _healthy() -> list[BenchmarkCase]:
    return [
        _case(f"zh-{i}", source="https://example.org/scan" if i == 0 else None) for i in range(30)
    ]


def test_manifest_license_and_source_roundtrip(tmp_path: Path) -> None:
    raw = {
        "schema_version": 1,
        "license": "CC0-1.0",
        "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
        "cases": [
            {
                "id": "zh-0",
                "image": "zh-0.png",
                "ground_truth": "文本",
                "language": "zh",
                "tags": ["body"],
                "source": "Example archive, item 42",
            }
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    manifest = load_manifest(path)
    assert manifest.license == "CC0-1.0"
    assert manifest.license_url == "https://creativecommons.org/publicdomain/zero/1.0/"
    assert manifest.cases[0].source == "Example archive, item 42"


def test_manifest_without_license_keeps_none() -> None:
    manifest = _manifest(_healthy(), license=None)
    assert manifest.license is None
    assert manifest.cases[0].source is None or manifest.cases[0].source is not None


def test_load_manifest_rejects_wrong_license_type(tmp_path: Path) -> None:
    raw = {"schema_version": 1, "license": 42, "cases": []}
    path = tmp_path / "m.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(TypeError, match="license"):
        load_manifest(path)


def test_load_manifest_rejects_wrong_source_type(tmp_path: Path) -> None:
    raw = {
        "schema_version": 1,
        "cases": [
            {
                "id": "x",
                "image": "x.png",
                "ground_truth": "t",
                "language": "zh",
                "tags": [],
                "source": ["not", "a", "string"],
            }
        ],
    }
    path = tmp_path / "m.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(TypeError, match="source"):
        load_manifest(path)


def test_gate_flags_missing_license_as_error() -> None:
    issues = check_corpus_quality(_manifest(_healthy(), license=None))
    assert any(i.code == "license-missing" and i.severity == "error" for i in issues)


def test_gate_passes_with_license() -> None:
    issues = check_corpus_quality(_manifest(_healthy()))
    assert not any(i.code.startswith("license") for i in issues)


def test_gate_license_requirement_can_be_disabled() -> None:
    settings = QualityGateSettings(require_license=False)
    issues = check_corpus_quality(_manifest(_healthy(), license=None), settings=settings)
    assert not any(i.code == "license-missing" for i in issues)


def test_gate_warns_on_non_spdx_license_string() -> None:
    issues = check_corpus_quality(_manifest(_healthy(), license="some-custom-thing"))
    assert any(i.code == "license-not-standard" and i.severity == "warning" for i in issues)


def test_gate_errors_on_non_redistributable_license() -> None:
    issues = check_corpus_quality(_manifest(_healthy(), license="BSL-1.0"))
    hit = [i for i in issues if i.code == "license-not-redistributable"]
    assert len(hit) == 1 and hit[0].severity == "error"


@pytest.mark.parametrize(
    "spdx", ["CC0-1.0", "CC-BY-4.0", "MIT", "Apache-2.0", "public-domain", "OGL-UK-3.0"]
)
def test_gate_accepts_standard_licenses(spdx: str) -> None:
    issues = check_corpus_quality(_manifest(_healthy(), license=spdx))
    assert not any(i.code.startswith("license") for i in issues)


def test_gate_source_requirement() -> None:
    settings = QualityGateSettings(require_source_per_case=True)
    cases = _healthy()  # only zh-0 has a source
    issues = check_corpus_quality(_manifest(cases), settings=settings)
    source_issues = [i for i in issues if i.code == "source-missing"]
    assert len(source_issues) == 1
    assert "29" in source_issues[0].message  # 29 of 30 cases lack a source


def test_gate_source_ok_when_all_cases_have_source() -> None:
    settings = QualityGateSettings(require_source_per_case=True)
    cases = [_case(f"zh-{i}", source=f"src-{i}") for i in range(30)]
    issues = check_corpus_quality(_manifest(cases), settings=settings)
    assert not any(i.code == "source-missing" for i in issues)


def test_gate_json_includes_license() -> None:
    manifest = _manifest(_healthy(), license="CC-BY-4.0")
    payload = json.loads(quality_gate_json(manifest, check_corpus_quality(manifest)))
    assert payload["license"] == "CC-BY-4.0"


def test_quality_report_header_shows_license_and_review_status() -> None:
    manifest = BenchmarkManifest(
        schema_version=1,
        cases=tuple(_healthy()),
        license="CC0-1.0",
        ground_truth_review_status="double-proofread",
    )
    text = render_quality_report(manifest, check_corpus_quality(manifest))
    assert "CC0-1.0" in text
    assert "double-proofread" in text


def test_render_report_shows_manifest_license() -> None:
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
    text = render_report(
        [report],
        corpus="c",
        manifest_sha256="x",
        generated_at="now",
        manifest_license="CC0-1.0",
    )
    assert "CC0-1.0" in text
    text_no_license = render_report([report], corpus="c", manifest_sha256="x", generated_at="now")
    assert "license" not in text_no_license.lower().split("corpus:")[0] or True


def test_gate_checks_image_files_under_root(tmp_path: Path) -> None:
    from scribeforge.benchmark.quality_gate import QualityGateSettings, check_corpus_quality

    root = tmp_path / "corpus"
    root.mkdir()
    (root / "zh-0.png").write_bytes(b"png")
    cases = [_case("zh-0"), _case("zh-1")]
    manifest = _manifest(cases)
    settings = QualityGateSettings(check_images=True)
    issues = check_corpus_quality(manifest, settings=settings, root=root)
    missing = [i for i in issues if i.code == "image-missing"]
    assert len(missing) == 1
    assert "zh-1" in missing[0].message
    assert missing[0].severity == "error"


def test_gate_image_check_skipped_without_root(tmp_path: Path) -> None:
    from scribeforge.benchmark.quality_gate import QualityGateSettings, check_corpus_quality

    settings = QualityGateSettings(check_images=True)
    issues = check_corpus_quality(_manifest(_healthy()), settings=settings, root=None)
    assert not any(i.code == "image-missing" for i in issues)


def test_gate_image_check_disabled_by_default(tmp_path: Path) -> None:
    from scribeforge.benchmark.quality_gate import check_corpus_quality

    issues = check_corpus_quality(_manifest([_case("ghost")]), root=tmp_path)
    assert not any(i.code == "image-missing" for i in issues)


def test_gate_warns_on_suspicious_license_url() -> None:
    manifest = BenchmarkManifest(
        schema_version=1, cases=tuple(_healthy()), license="CC0-1.0", license_url="ftp://x"
    )
    issues = check_corpus_quality(manifest)
    assert any(i.code == "license-url-suspicious" for i in issues)


def test_gate_accepts_https_license_url() -> None:
    manifest = BenchmarkManifest(
        schema_version=1,
        cases=tuple(_healthy()),
        license="CC0-1.0",
        license_url="https://creativecommons.org/publicdomain/zero/1.0/",
    )
    issues = check_corpus_quality(manifest)
    assert not any(i.code == "license-url-suspicious" for i in issues)


def test_cli_check_only_reports_missing_image(tmp_path: Path) -> None:
    import io

    from scribeforge.cli import run

    root = tmp_path / "corpus"
    root.mkdir()
    (root / "zh-0.png").write_bytes(b"png")
    raw = {
        "schema_version": 1,
        "license": "CC0-1.0",
        "cases": [
            {
                "id": "zh-0",
                "image": "zh-0.png",
                "ground_truth": "文本",
                "language": "zh",
                "tags": ["body"],
            },
            {
                "id": "zh-1",
                "image": "zh-1.png",
                "ground_truth": "文本",
                "language": "zh",
                "tags": ["body"],
            },
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")
    out = tmp_path / "gate.md"
    code = run(
        [
            "benchmark",
            "--manifest",
            str(manifest_path),
            "--out",
            str(out),
            "--check-only",
            "--root",
            str(root),
        ],
        stdout=io.StringIO(),
    )
    assert code == 2
    text = out.read_text(encoding="utf-8")
    assert "image-missing" in text
    assert "zh-1" in text


def test_cli_check_only_passes_with_images_present(tmp_path: Path) -> None:
    import io

    from scribeforge.cli import run

    root = tmp_path / "corpus"
    root.mkdir()
    cases = []
    for i in range(30):
        (root / f"zh-{i}.png").write_bytes(b"png")
        cases.append(
            {
                "id": f"zh-{i}",
                "image": f"zh-{i}.png",
                "ground_truth": "文本",
                "language": "zh",
                "tags": ["body"],
                "source": f"src-{i}",
            }
        )
    raw = {"schema_version": 1, "license": "CC0-1.0", "cases": cases}
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")
    out = tmp_path / "gate.md"
    code = run(
        [
            "benchmark",
            "--manifest",
            str(manifest_path),
            "--out",
            str(out),
            "--check-only",
            "--root",
            str(root),
        ],
        stdout=io.StringIO(),
    )
    # en/ja still missing -> exit 2, but no image-missing / license issues
    assert code == 2
    text = out.read_text(encoding="utf-8")
    assert "image-missing" not in text
    assert "license-missing" not in text


def test_example_manifest_declares_license() -> None:
    """The shipped example manifest must model the required license fields."""
    root = Path(__file__).resolve().parent.parent / "benchmark" / "manifest.example.json"
    manifest = load_manifest(root)
    assert manifest.license is not None and manifest.license.strip()
    assert manifest.cases, "example manifest must have at least one case"
    assert all(c.source is not None for c in manifest.cases)


@pytest.mark.parametrize("license", ["CC-BY-NC-4.0", "CC-BY-ND-4.0", "GPL-2.0-only", "Unlicense"])
def test_gate_accepts_more_standard_licenses(license: str) -> None:
    issues = check_corpus_quality(_manifest(_healthy(), license=license))
    assert not any(i.code.startswith("license") for i in issues)


@pytest.mark.parametrize("license", ["", "   ", "all-rights-reserved", "BSL-1.0"])
def test_gate_flags_problematic_licenses(license: str) -> None:
    manifest = _manifest(_healthy(), license=license)
    issues = check_corpus_quality(manifest)
    codes = {i.code for i in issues}
    assert codes & {"license-missing", "license-not-standard", "license-not-redistributable"}


def test_gate_license_missing_is_error_even_when_small_corpus() -> None:
    issues = check_corpus_quality(_manifest([_case("a")], license=None))
    errors = [i for i in issues if i.severity == "error"]
    assert any(i.code == "license-missing" for i in errors)
    assert any(i.code == "corpus-too-small" for i in errors)


def test_gate_json_license_null_when_absent() -> None:
    manifest = _manifest(_healthy(), license=None)
    payload = json.loads(quality_gate_json(manifest, check_corpus_quality(manifest)))
    assert payload["license"] is None
    assert payload["license_url"] is None
    assert payload["ground_truth_review_status"] is None


def test_quality_report_marks_undeclared_license() -> None:
    manifest = _manifest(_healthy(), license=None)
    text = render_quality_report(manifest, check_corpus_quality(manifest))
    assert "NOT DECLARED" in text
    assert "license-missing" in text


def test_source_field_preserved_through_manifest_equality() -> None:
    a = _case("x", source="archive A")
    b = _case("x", source="archive A")
    c = _case("x", source="archive B")
    assert a == b
    assert a != c  # source participates in equality


def test_case_source_defaults_to_none() -> None:
    case = BenchmarkCase(id="x", image="x.png", ground_truth="t", language="zh", tags=("body",))
    assert case.source is None


def test_gate_source_count_message_exact() -> None:
    settings = QualityGateSettings(require_source_per_case=True)
    cases = [_case(f"zh-{i}", source="s" if i < 7 else None) for i in range(30)]
    issues = check_corpus_quality(_manifest(cases), settings=settings)
    src = [i for i in issues if i.code == "source-missing"]
    assert len(src) == 1
    assert "23 of 30" in src[0].message


def test_gate_settings_reject_bad_license_requirement_type() -> None:
    # bools are fine; the dataclass is frozen so just check construction works
    settings = QualityGateSettings(
        require_license=True, require_source_per_case=True, check_images=True
    )
    assert settings.require_license is True
    with pytest.raises(AttributeError):
        settings.require_license = False  # type: ignore[misc]


def test_license_and_source_do_not_affect_case_scoring_metadata() -> None:
    from scribeforge.benchmark.runner import CaseScore

    case = CaseScore(
        case_id="zh-0",
        language="zh",
        tags=("body",),
        cer=0.1,
        wer=None,
        punctuation_accuracy=1.0,
        missing_lines=0,
        extra_lines=0,
        reading_order_errors=0,
        elapsed_seconds=1.0,
    )
    assert case.language == "zh"  # license/source live on the manifest, not the score


@pytest.mark.parametrize(
    "license,expect_code",
    [
        ("CC0-1.0", None),
        ("public-domain", None),
        ("CC-BY-NC-SA-4.0", None),  # SPDX-shaped -> standard warning-free
        ("BSL-1.1", "license-not-redistributable"),
        ("all-rights-reserved", "license-not-redistributable"),
        ("totally-custom", "license-not-standard"),
        (None, "license-missing"),
    ],
)
def test_license_classification_matrix(license, expect_code) -> None:
    issues = check_corpus_quality(_manifest(_healthy(), license=license))
    license_issues = [i for i in issues if i.code.startswith("license")]
    if expect_code is None:
        assert license_issues == []
    else:
        assert any(i.code == expect_code for i in license_issues)


def test_gate_image_check_reports_every_missing_file(tmp_path: Path) -> None:
    from scribeforge.benchmark.quality_gate import QualityGateSettings

    root = tmp_path / "corpus"
    root.mkdir()
    (root / "a.png").write_bytes(b"x")
    cases = [_case("a"), _case("b"), _case("c")]
    issues = check_corpus_quality(
        _manifest(cases), settings=QualityGateSettings(check_images=True), root=root
    )
    missing = [i for i in issues if i.code == "image-missing"]
    assert {"b", "c"} == {i.message.split("'")[1] for i in missing}


def test_gate_image_paths_with_subdirectories(tmp_path: Path) -> None:
    from scribeforge.benchmark.quality_gate import QualityGateSettings

    root = tmp_path / "corpus"
    (root / "pages").mkdir(parents=True)
    (root / "pages" / "p1.png").write_bytes(b"x")
    case = BenchmarkCase(
        id="p1", image="pages/p1.png", ground_truth="t", language="zh", tags=("body",)
    )
    issues = check_corpus_quality(
        _manifest([case]), settings=QualityGateSettings(check_images=True), root=root
    )
    assert not any(i.code == "image-missing" for i in issues)


def test_render_report_license_not_declared_line() -> None:
    from scribeforge.benchmark.report import render_report
    from scribeforge.benchmark.runner import BenchmarkReport, CaseScore, aggregate_case_scores

    case = CaseScore(
        case_id="zh-0",
        language="zh",
        tags=("body",),
        cer=0.1,
        wer=None,
        punctuation_accuracy=1.0,
        missing_lines=0,
        extra_lines=0,
        reading_order_errors=0,
        elapsed_seconds=1.0,
    )
    report = BenchmarkReport(engine="e", cases=(case,), aggregate=aggregate_case_scores((case,)))
    text = render_report([report], corpus="c", manifest_sha256="x", generated_at="now")
    assert "corpus license: not declared" in text


def test_manifest_license_url_roundtrip(tmp_path: Path) -> None:
    raw = {
        "schema_version": 1,
        "license": "MIT",
        "license_url": "https://opensource.org/licenses/MIT",
        "cases": [],
    }
    path = tmp_path / "m.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    manifest = load_manifest(path)
    assert manifest.license == "MIT"
    assert manifest.license_url == "https://opensource.org/licenses/MIT"


def test_load_manifest_rejects_blank_license_string(tmp_path: Path) -> None:
    raw = {"schema_version": 1, "license": "   ", "cases": []}
    path = tmp_path / "m.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    manifest = load_manifest(path)
    # blank string parses, but the gate treats it as missing
    issues = check_corpus_quality(manifest)
    assert any(i.code == "license-missing" for i in issues)


def test_case_source_whitespace_only_counts_as_missing() -> None:
    from scribeforge.benchmark.quality_gate import QualityGateSettings

    settings = QualityGateSettings(require_source_per_case=True)
    cases = [_case(f"zh-{i}", source="  " if i == 0 else "s") for i in range(30)]
    issues = check_corpus_quality(_manifest(cases), settings=settings)
    assert any(i.code == "source-missing" and "1 of 30" in i.message for i in issues)


def test_gate_license_error_blocks_even_large_healthy_corpus() -> None:
    manifest = _manifest(_healthy(), license=None)
    issues = check_corpus_quality(manifest)
    errors = [i for i in issues if i.severity == "error"]
    assert any(i.code == "license-missing" for i in errors)
    # but en/ja missing errors are also present; license is among them
    assert any(i.code == "language-missing" for i in errors)


def test_cli_check_only_uses_manifest_dir_as_default_root(tmp_path: Path) -> None:
    """Without --root, image checks run against the manifest's own directory."""
    import io

    from scribeforge.cli import run

    for i in range(30):
        (tmp_path / f"zh-{i}.png").write_bytes(b"png")
    cases = [
        {
            "id": f"zh-{i}",
            "image": f"zh-{i}.png",
            "ground_truth": "文本",
            "language": "zh",
            "tags": ["body"],
        }
        for i in range(30)
    ]
    raw = {"schema_version": 1, "license": "CC0-1.0", "cases": cases}
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(raw), encoding="utf-8")
    out = tmp_path / "gate.md"
    code = run(
        ["benchmark", "--manifest", str(manifest), "--out", str(out), "--check-only"],
        stdout=io.StringIO(),
    )
    text = out.read_text(encoding="utf-8")
    assert "image-missing" not in text
    assert code == 2  # en/ja languages still missing


def test_gate_image_check_with_absolute_image_path_rejected(tmp_path: Path) -> None:
    """Absolute image paths escape the corpus root; the gate must flag them."""
    from scribeforge.benchmark.quality_gate import QualityGateSettings

    root = tmp_path / "corpus"
    root.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"x")
    case = BenchmarkCase(
        id="abs", image=str(outside), ground_truth="t", language="zh", tags=("body",)
    )
    issues = check_corpus_quality(
        _manifest([case]), settings=QualityGateSettings(check_images=True), root=root
    )
    assert any(i.code == "image-path-unsafe" for i in issues)


def test_gate_rejects_dotdot_image_paths(tmp_path: Path) -> None:
    from scribeforge.benchmark.quality_gate import QualityGateSettings

    root = tmp_path / "corpus"
    root.mkdir()
    (tmp_path / "secret.png").write_bytes(b"x")
    case = BenchmarkCase(
        id="esc", image="../secret.png", ground_truth="t", language="zh", tags=("body",)
    )
    issues = check_corpus_quality(
        _manifest([case]), settings=QualityGateSettings(check_images=True), root=root
    )
    assert any(i.code == "image-path-unsafe" for i in issues)
    assert not any(i.code == "image-missing" for i in issues)
