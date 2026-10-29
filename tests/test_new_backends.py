"""Tests for the two technology-route backends (PaddleOCR-VL, Surya)."""

from pathlib import Path

import pytest

from scribeforge.runtime.manager import RuntimeManager
from scribeforge.runtime.models import HardwareProfile, RuntimeLayout
from scribeforge.runtime.planner import build_runtime_plan
from scribeforge.workers import paddle_vl_worker, surya_worker


def _profile() -> HardwareProfile:
    return HardwareProfile(
        os_name="linux",
        architecture="x86_64",
        gpu_name=None,
        nvidia_driver=None,
        vram_mb=None,
        ram_mb=16384,
    )


def _vl_payload(*regions: dict[str, object]) -> dict[str, object]:
    return {"engine": "PaddleOCRVL", "parsing_res_list": list(regions)}


def test_paddle_vl_maps_regions_to_lines_and_blocks() -> None:
    payload = _vl_payload(
        {
            "block_label": "text",
            "block_content": "第一列",
            "block_bbox": [700, 100, 740, 900],
        },
        {
            "block_label": "text",
            "block_content": "第二列",
            "block_bbox": [500, 100, 540, 900],
        },
        {
            "block_label": "picture",
            "block_content": "",
            "block_bbox": [100, 100, 300, 400],
        },
    )

    lines, blocks, direction = paddle_vl_worker.extract_page(
        payload, width=1000, height=1000
    )

    assert [line["text"] for line in lines] == ["第一列", "第二列"]
    # Reading order is the model's own region order, not re-sorted geometry.
    assert [line["box"][0] for line in lines] == [0.7, 0.5]
    assert [block["type"] for block in blocks] == ["text", "text", "figure"]
    assert (blocks[0]["line_start"], blocks[0]["line_end"]) == (0, 1)
    assert (blocks[1]["line_start"], blocks[1]["line_end"]) == (1, 2)
    # A figure carries block evidence but no line, so it covers no line range.
    assert (blocks[2]["line_start"], blocks[2]["line_end"]) == (0, 0)
    assert direction is None


def test_paddle_vl_reports_the_direction_the_model_asserts() -> None:
    payload = _vl_payload(
        {
            "block_label": "text",
            "block_content": "子曰",
            "block_bbox": [700, 100, 740, 900],
        }
    )
    payload["page_direction"] = "vertical-rl"

    _, _, direction = paddle_vl_worker.extract_page(payload, width=1000, height=1000)

    assert direction == "vertical-rl"


def test_paddle_vl_accepts_a_point_polygon_and_normalized_boxes() -> None:
    payload = _vl_payload(
        {
            "block_label": "text",
            "block_content": "甲",
            "rec_poly": [[0.1, 0.2], [0.3, 0.2], [0.3, 0.4], [0.1, 0.4]],
        }
    )

    lines, _, _ = paddle_vl_worker.extract_page(payload, width=1000, height=1000)

    assert lines[0]["box"] == pytest.approx([0.1, 0.2, 0.2, 0.2])


def test_paddle_vl_clamps_a_region_that_spills_past_the_page() -> None:
    payload = _vl_payload(
        {
            "block_label": "text",
            "block_content": "边缘",
            "block_bbox": [-20, 980, 2000, 1400],
        }
    )

    lines, _, _ = paddle_vl_worker.extract_page(payload, width=1000, height=1000)

    x, y, width, height = lines[0]["box"]
    assert x == 0.0 and y == 0.98
    assert x + width <= 1.0 and y + height <= 1.0


def test_paddle_vl_drops_regions_without_geometry() -> None:
    payload = _vl_payload({"block_label": "text", "block_content": "无框"})

    lines, blocks, _ = paddle_vl_worker.extract_page(payload, width=1000, height=1000)

    # Text that cannot be placed on the page cannot be aligned; reporting it as
    # a zero-area box would create a phantom line at the top-left corner.
    assert lines == []
    assert blocks == []


def test_surya_orders_blocks_by_the_model_position() -> None:
    document = {
        "width": 1000,
        "height": 1000,
        "boxes": [
            {"label": "PageFooter", "bbox": [0, 950, 1000, 1000], "position": 9},
            {"label": "Text", "bbox": [100, 100, 900, 200], "position": 1},
            {"label": "Picture", "bbox": [100, 300, 900, 600], "position": 2},
        ],
    }

    blocks = surya_worker.extract_blocks(document)

    assert [block["type"] for block in blocks] == ["text", "figure", "footer"]
    assert blocks[0]["box"] == [0.1, 0.1, 0.8, 0.1]


def test_surya_maps_section_headers_and_list_items() -> None:
    document = {
        "width": 100,
        "height": 100,
        "boxes": [
            {"label": "SectionHeader", "bbox": [0, 0, 50, 10]},
            {"label": "ListItem", "bbox": [0, 20, 50, 30]},
        ],
    }

    blocks = surya_worker.extract_blocks(document)

    assert [block["type"] for block in blocks] == ["title", "text"]


def test_surya_skips_pixel_boxes_without_page_dimensions() -> None:
    document = {"boxes": [{"label": "Text", "bbox": [10, 20, 30, 40]}]}

    assert surya_worker.extract_blocks(document) == []


def test_surya_declares_no_direction() -> None:
    """Surya does not classify direction; the pipeline's geometry must decide."""

    document = {
        "width": 100,
        "height": 100,
        "boxes": [{"label": "Text", "bbox": [70, 10, 80, 90]}],
    }
    blocks = surya_worker.extract_blocks(document)

    assert surya_worker.reading_direction(blocks) is None


def test_new_backends_are_planned_as_optional_runtimes(tmp_path: Path) -> None:
    plan = build_runtime_plan(_profile(), RuntimeLayout(tmp_path))

    assert plan.backend("paddleocr-vl").optional is True
    assert plan.backend("surya").optional is True
    assert plan.backend("paddleocr-vl").acceleration == "cpu"
    assert plan.backend("mineru").optional is False


def test_plain_install_skips_optional_backends(tmp_path: Path) -> None:
    class RecordingExecutor:
        def __init__(self) -> None:
            self.calls: list[tuple[str, ...]] = []

        def run(self, argv: tuple[str, ...], env: dict[str, str] | None = None) -> None:
            self.calls.append(argv)

    plan = build_runtime_plan(_profile(), RuntimeLayout(tmp_path))
    executor = RecordingExecutor()

    RuntimeManager(tmp_path, executor).install(plan)

    # Only the two default engines were installed: the optional model stacks are
    # multiple gigabytes and must be requested explicitly via `repair`.
    assert not any("paddleocr-vl" in " ".join(call) for call in executor.calls)
    assert not any("surya" in " ".join(call) for call in executor.calls)


def test_optional_backend_can_be_installed_by_name(tmp_path: Path) -> None:
    class RecordingExecutor:
        def __init__(self) -> None:
            self.calls: list[tuple[str, ...]] = []

        def run(self, argv: tuple[str, ...], env: dict[str, str] | None = None) -> None:
            self.calls.append(argv)

    plan = build_runtime_plan(_profile(), RuntimeLayout(tmp_path))
    executor = RecordingExecutor()

    RuntimeManager(tmp_path, executor).repair(plan, "surya")

    assert any("surya-ocr" in argument for call in executor.calls for argument in call)


def test_benchmark_registry_points_at_existing_worker_scripts() -> None:
    from scribeforge.cli import _BENCHMARK_BACKENDS, _worker_script

    for name in ("paddleocr-vl", "surya"):
        assert _BENCHMARK_BACKENDS[name] in ("paddle_vl_worker.py", "surya_worker.py")
        assert _worker_script(_BENCHMARK_BACKENDS[name]).is_file()


def test_surya_document_reaches_the_domain_as_block_only_evidence() -> None:
    """A structure-only engine must never be read as "the page has no text"."""

    from scribeforge.adapters.subprocess_ocr import SubprocessOCREngine
    from scribeforge.domain.ocr import BlockType, PageDirection

    engine = SubprocessOCREngine("surya", ("worker",))
    document = {
        "schema_version": 2,
        "engine": "surya",
        "engine_version": "0.14",
        "page_index": 0,
        "lines": [],
        "blocks": [
            {"type": "figure", "box": [0.1, 0.2, 0.3, 0.4], "line_start": 0, "line_end": 0}
        ],
    }

    result = engine.parse_document(document, 0)

    assert [block.block_type for block in result.blocks] == [BlockType.FIGURE]
    assert result.lines == ()
    assert result.direction is PageDirection.HORIZONTAL_LR
    # No direction asserted by the engine: geometry detection stays in charge.
    assert result.direction_declared is False


def _tiny_page(tmp_path: Path) -> Path:
    from PIL import Image

    page = tmp_path / "page.png"
    Image.new("RGB", (100, 200), "white").save(page)
    return page


def test_paddle_vl_worker_main_emits_a_contract_v2_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsysbinary
) -> None:
    """The worker's own entry point must produce a parseable v2 document."""

    import json

    from scribeforge.adapters.subprocess_ocr import SubprocessOCREngine

    page = _tiny_page(tmp_path)

    def fake_run(argv: list[str], **_: object) -> None:
        output_dir = Path(argv[4])
        (output_dir / "result.json").write_text(
            json.dumps(
                {
                    "parsing_res_list": [
                        {
                            "block_label": "text",
                            "block_content": "子曰",
                            "block_bbox": [70, 20, 80, 180],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

    monkeypatch.setattr(paddle_vl_worker.subprocess, "run", fake_run)
    monkeypatch.setattr(
        "sys.argv", ["paddle_vl_worker.py", "--image", str(page), "--page-index", "0"]
    )

    paddle_vl_worker.main()

    document = json.loads(capsysbinary.readouterr().out.decode("utf-8"))
    result = SubprocessOCREngine("paddleocr-vl", ("worker",)).parse_document(document, 0)
    assert result.lines[0].text == "子曰"
    assert result.direction_declared is False


def test_surya_worker_main_emits_block_only_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsysbinary
) -> None:
    import json

    from scribeforge.adapters.subprocess_ocr import SubprocessOCREngine

    page = _tiny_page(tmp_path)

    def fake_run(argv: list[str], **_: object) -> None:
        Path(argv[4]).write_text(
            json.dumps(
                {
                    "width": 100,
                    "height": 200,
                    "boxes": [{"label": "Picture", "bbox": [0, 0, 100, 100]}],
                }
            ),
            encoding="utf-8",
        )

    monkeypatch.setattr(surya_worker.subprocess, "run", fake_run)
    monkeypatch.setattr(
        "sys.argv", ["surya_worker.py", "--image", str(page), "--page-index", "0"]
    )

    surya_worker.main()

    document = json.loads(capsysbinary.readouterr().out.decode("utf-8"))
    result = SubprocessOCREngine("surya", ("worker",)).parse_document(document, 0)
    assert result.lines == ()
    assert [block.block_type.value for block in result.blocks] == ["figure"]
