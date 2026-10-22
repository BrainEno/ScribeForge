from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from scribeforge.benchmark.manifest import BenchmarkCase, BenchmarkManifest
from scribeforge.benchmark.pipeline_runner import run_pipeline_benchmark
from scribeforge.domain.ocr import BoundingBox, OCRLine, PageOCRResult
from scribeforge.domain.vlm import VLMReading


class FakeEngine:
    def __init__(self, name: str, text: str) -> None:
        self.name = name
        self.text = text

    def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
        box = BoundingBox(0.1, 0.1, 0.8, 0.1)
        return PageOCRResult(
            page_index,
            self.name,
            "test-1",
            (OCRLine(self.text, box),),
        )


class FakeReviewer:
    def transcribe_crop(
        self, image_path: str, page_index: int, pair_index: int
    ) -> VLMReading:
        assert Path(image_path).is_file()
        return VLMReading(page_index, pair_index, "fake-vlm", "1", "checked", False)


def _corpus(tmp_path: Path, ground_truth: str) -> tuple[Path, BenchmarkManifest]:
    Image.new("RGB", (100, 120), "white").save(tmp_path / "page.png")
    (tmp_path / "truth.txt").write_text(ground_truth, encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "cases": [
                    {
                        "id": "case-1",
                        "image": "page.png",
                        "ground_truth": "truth.txt",
                        "language": "en",
                        "tags": ["body"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return tmp_path, BenchmarkManifest(
        1,
        (BenchmarkCase("case-1", "page.png", "truth.txt", "en", ("body",)),),
    )


def test_pipeline_benchmark_scores_only_agreed_decisions_and_reports_unresolved(
    tmp_path: Path,
) -> None:
    root, manifest = _corpus(tmp_path, "right")
    report = run_pipeline_benchmark(
        manifest,
        root,
        FakeEngine("mineru", "left"),
        FakeEngine("paddleocr", "right"),
    )

    assert report.engine == "dual-engine"
    assert report.aggregate.case_count == 1
    case = report.cases[0]
    assert case.accepted_pairs == 0
    assert case.unresolved_pairs == 1
    assert case.missing_lines == 1
    assert case.extra_lines == 0
    assert case.cer == 1.0
    assert report.aggregate.accepted_pairs == 0
    assert report.aggregate.unresolved_pairs == 1


def test_pipeline_benchmark_accepts_independent_engine_agreement(tmp_path: Path) -> None:
    root, manifest = _corpus(tmp_path, "same")
    report = run_pipeline_benchmark(
        manifest,
        root,
        FakeEngine("mineru", "same"),
        FakeEngine("paddleocr", "same"),
    )

    assert report.cases[0].accepted_pairs == 1
    assert report.cases[0].unresolved_pairs == 0
    assert report.aggregate.unresolved_pairs == 0


def test_pipeline_benchmark_can_score_vlm_adjudicated_conflicts(tmp_path: Path) -> None:
    root, manifest = _corpus(tmp_path, "checked")
    report = run_pipeline_benchmark(
        manifest,
        root,
        FakeEngine("mineru", "left"),
        FakeEngine("paddleocr", "right"),
        reviewer=FakeReviewer(),
    )

    assert report.cases[0].accepted_pairs == 1
    assert report.cases[0].unresolved_pairs == 0
    assert report.cases[0].cer == 0.0


def test_pipeline_benchmark_fails_closed_when_a_page_pipeline_fails(
    tmp_path: Path,
) -> None:
    import pytest

    class ExplodingEngine(FakeEngine):
        def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
            raise RuntimeError("synthetic OCR failure")

    root, manifest = _corpus(tmp_path, "expected")
    with pytest.raises(RuntimeError, match="case case-1 pipeline failed"):
        run_pipeline_benchmark(
            manifest,
            root,
            ExplodingEngine("mineru", "left"),
            FakeEngine("paddleocr", "right"),
        )
