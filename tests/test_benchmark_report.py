from __future__ import annotations

from scribeforge.benchmark.report import render_report
from scribeforge.benchmark.runner import AggregateScore, BenchmarkReport, CaseScore


def _case(
    case_id: str = "case-1",
    *,
    cer: float = 0.25,
    tags: tuple[str, ...] = ("body", "punctuation"),
    elapsed: float = 2.0,
) -> CaseScore:
    return CaseScore(
        case_id=case_id,
        language="zh",
        tags=tags,
        cer=cer,
        wer=0.5,
        punctuation_accuracy=0.75,
        missing_lines=0,
        extra_lines=1,
        elapsed_seconds=elapsed,
    )


def _report(
    engine: str, *, cer: float = 0.25, elapsed: float = 2.0, pages_per_minute: float = 30.0
) -> BenchmarkReport:
    case = _case(cer=cer, elapsed=elapsed)
    return BenchmarkReport(
        engine=engine,
        cases=(case,),
        aggregate=AggregateScore(
            case_count=1,
            mean_cer=cer,
            mean_wer=0.5,
            mean_punctuation_accuracy=0.75,
            missing_lines=0,
            extra_lines=1,
            elapsed_seconds=elapsed,
            pages_per_minute=pages_per_minute,
        ),
    )


def test_render_report_summarizes_variants_and_per_case_results() -> None:
    text = render_report(
        (_report("mineru"), _report("paddleocr", cer=0.5, elapsed=1.0, pages_per_minute=60.0)),
        corpus="corpus/manifest.json",
        manifest_sha256="abc123",
        generated_at="2026-10-20T00:00:00Z",
    )

    lines = text.splitlines()
    assert lines[0] == "# Benchmark Report"
    assert "- corpus: `corpus/manifest.json`" in lines
    assert "- manifest sha256: `abc123`" in lines
    assert "- generated: 2026-10-20T00:00:00Z" in lines
    assert "- variants: 2" in lines
    assert (
        "| mineru | 1 | 0.2500 | 0.5000 | 0.7500 | 0 | 1 | 2.000 | 30.00 |" in lines
    )
    assert (
        "| paddleocr | 1 | 0.5000 | 0.5000 | 0.7500 | 0 | 1 | 1.000 | 60.00 |" in lines
    )
    assert "| case-1 | zh | body,punctuation | 0.2500 | 0.5000 | 0.7500 | 0 | 1 | 2.000 |" in lines
    assert text.index("| mineru |") < text.index("| paddleocr |")
    assert text.count("### mineru") == 1
    assert text.count("### paddleocr") == 1


def test_render_report_states_when_no_variant_was_scored() -> None:
    text = render_report(
        (), corpus="corpus/manifest.json", manifest_sha256="abc123", generated_at="t"
    )

    assert "No variants were scored." in text.splitlines()


def test_render_report_marks_a_case_without_tags() -> None:
    report = _report("mineru")
    untagged = BenchmarkReport(
        engine="mineru",
        cases=(
            CaseScore(
                case_id="case-1",
                language="zh",
                tags=(),
                cer=0.0,
                wer=0.0,
                punctuation_accuracy=1.0,
                missing_lines=0,
                extra_lines=0,
                elapsed_seconds=0.5,
            ),
        ),
        aggregate=report.aggregate,
    )

    text = render_report(
        (untagged,), corpus="m.json", manifest_sha256="abc123", generated_at="t"
    )

    assert "| case-1 | zh | - | 0.0000 |" in text
