from scribeforge.workers.mineru_worker import build_command as mineru_command
from scribeforge.workers.paddle_worker import build_command as paddle_command


def test_mineru_command_requests_middle_json_in_an_output_directory(tmp_path) -> None:
    command = mineru_command("page.png", tmp_path)
    assert command[:3] == ["mineru-kit", "parse", "page.png"]
    assert command[command.index("-o") + 1] == str(tmp_path)
    assert command[command.index("-f") + 1] == "middle_json"
    assert command[command.index("--tier") + 1] == "basic"
    assert "--json" not in command


def test_paddle_command_selects_word_box_capable_pp_ocr_v4() -> None:
    command = paddle_command("page.png")
    assert command[:3] == ["paddleocr", "ocr", "--input"]
    assert command[command.index("--ocr_version") + 1] == "PP-OCRv4"
    assert command[command.index("--return_word_box") + 1] == "true"
    assert "--model_name" not in command
    assert "api" not in command


def test_paddle_command_can_use_only_preverified_local_models(tmp_path) -> None:
    detection = tmp_path / "det"
    recognition = tmp_path / "rec"

    command = paddle_command(
        "page.png",
        model_directories={
            "PP-OCRv4_mobile_det": detection,
            "PP-OCRv4_mobile_rec": recognition,
        },
    )

    assert command[command.index("--text_detection_model_name") + 1] == "PP-OCRv4_mobile_det"
    assert command[command.index("--text_detection_model_dir") + 1] == str(detection)
    assert command[command.index("--text_recognition_model_name") + 1] == "PP-OCRv4_mobile_rec"
    assert command[command.index("--text_recognition_model_dir") + 1] == str(recognition)
