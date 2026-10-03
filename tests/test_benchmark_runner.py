from pathlib import Path

from scribeforge.benchmark.manifest import BenchmarkCase, BenchmarkManifest
from scribeforge.benchmark.runner import run_benchmark
from scribeforge.domain.ocr import BoundingBox, OCRLine, PageOCRResult


class FakeEngine:
    name = "fake"

    def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
        assert Path(image_path).name == "page.png"
        return PageOCRResult(
            page_index=page_index,
            engine=self.name,
            engine_version="1",
            lines=(OCRLine("你好,世界。", BoundingBox(0, 0, 1, 1)),),
        )


def test_runner_scores_case_and_preserves_identity(tmp_path) -> None:
    (tmp_path / "page.png").write_bytes(b"fixture")
    (tmp_path / "truth.txt").write_text("你好，世界。", encoding="utf-8")
    manifest = BenchmarkManifest(
        1,
        (BenchmarkCase("case-1", "page.png", "truth.txt", "zh", ("body",)),),
    )

    report = run_benchmark(manifest, tmp_path, FakeEngine())

    assert report.engine == "fake"
    assert report.cases[0].case_id == "case-1"
    assert report.cases[0].cer > 0
    assert report.cases[0].punctuation_accuracy == 0.5
    assert report.aggregate.case_count == 1
