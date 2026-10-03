from scribeforge.workers.mineru_worker import build_command as mineru_command
from scribeforge.workers.paddle_worker import build_command as paddle_command


def test_mineru_command_is_stateless_json_parse() -> None:
    command = mineru_command("page.png")
    assert command[:2] == ["mineru-kit", "parse"]
    assert "--json" in command


def test_paddle_command_selects_local_pp_ocr_v6() -> None:
    command = paddle_command("page.png")
    assert command[0] == "paddleocr"
    assert "PP-OCRv6" in command
    assert "api" not in command
