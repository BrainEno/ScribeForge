from scribeforge.workers.mineru_worker import build_command as mineru_command
from scribeforge.workers.paddle_worker import build_command as paddle_command


def test_mineru_command_requests_middle_json_in_an_output_directory(tmp_path) -> None:
    command = mineru_command("page.png", tmp_path)
    assert command[:3] == ["mineru-kit", "parse", "page.png"]
    assert command[command.index("-o") + 1] == str(tmp_path)
    assert command[command.index("-f") + 1] == "middle_json"
    assert command[command.index("--tier") + 1] == "basic"
    assert "--json" not in command


def test_paddle_command_selects_local_pp_ocr_v6() -> None:
    command = paddle_command("page.png")
    assert command[:3] == ["paddleocr", "ocr", "--input"]
    assert command[command.index("--ocr_version") + 1] == "PP-OCRv6"
    assert "--model_name" not in command
    assert "api" not in command
