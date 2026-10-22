from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from scribeforge.adapters.openai_compatible_vlm import (
    OpenAICompatibleVLMReviewer,
    VLMAdapterError,
)
from scribeforge.domain.vlm import LITERAL_TRANSCRIPTION_POLICY


class _ResponseHandler(BaseHTTPRequestHandler):
    response_content = '{"text":"one\\ntwo","uncertain":false}'
    request_json: dict[str, Any] | None = None
    authorization: str | None = None
    request_path: str | None = None
    finish_reason = "stop"
    response_status = 200
    redirect_url: str | None = None

    def do_POST(self) -> None:
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        type(self).request_json = json.loads(raw)
        type(self).authorization = self.headers.get("Authorization")
        type(self).request_path = self.path
        response = json.dumps(
            {
                "model": "fixture-vision-1",
                "choices": [
                    {
                        "finish_reason": type(self).finish_reason,
                        "message": {"role": "assistant", "content": type(self).response_content},
                    }
                ],
            }
        ).encode("utf-8")
        self.send_response(type(self).response_status)
        if type(self).redirect_url is not None:
            self.send_header("Location", type(self).redirect_url)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture
def vlm_server():
    _ResponseHandler.request_json = None
    _ResponseHandler.authorization = None
    _ResponseHandler.request_path = None
    _ResponseHandler.finish_reason = "stop"
    _ResponseHandler.response_status = 200
    _ResponseHandler.redirect_url = None
    _ResponseHandler.response_content = '{"text":"one\\ntwo","uncertain":false}'
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ResponseHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", _ResponseHandler
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _crop(tmp_path: Path) -> Path:
    path = tmp_path / "crop.png"
    Image.new("RGB", (3, 3), "white").save(path)
    return path


def test_openai_compatible_reviewer_sends_crop_and_preserves_literal_text(
    tmp_path: Path, vlm_server
) -> None:
    endpoint, handler = vlm_server
    crop = _crop(tmp_path)
    reviewer = OpenAICompatibleVLMReviewer(
        endpoint=endpoint,
        model="fixture-vision",
        api_key="test-secret",
        timeout_seconds=3,
    )

    reading = reviewer.transcribe_crop(str(crop), page_index=4, pair_index=2)

    assert reading.page_index == 4
    assert reading.pair_index == 2
    assert reading.model == "fixture-vision"
    assert reading.model_version == "fixture-vision-1"
    assert reading.text == "one\ntwo"
    assert reading.uncertain is False
    assert handler.request_path == "/v1/chat/completions"
    assert handler.authorization == "Bearer test-secret"
    request = handler.request_json
    assert request is not None
    assert request["model"] == "fixture-vision"
    assert request["temperature"] == 0
    assert LITERAL_TRANSCRIPTION_POLICY in request["messages"][0]["content"]
    image_part = request["messages"][1]["content"][1]
    assert image_part["type"] == "image_url"
    assert image_part["image_url"]["url"].startswith("data:image/png;base64,")
    encoded = image_part["image_url"]["url"].split(",", 1)[1]
    assert base64.b64decode(encoded) == crop.read_bytes()


def test_reviewer_rejects_remote_endpoint_without_explicit_opt_in() -> None:
    with pytest.raises(ValueError, match="allow_remote"):
        OpenAICompatibleVLMReviewer(
            endpoint="https://vlm.example.test/v1",
            model="vision-model",
        )


def test_reviewer_rejects_malformed_model_output(tmp_path: Path, vlm_server) -> None:
    endpoint, handler = vlm_server
    handler.response_content = "The answer is: one"
    reviewer = OpenAICompatibleVLMReviewer(endpoint=endpoint, model="fixture-vision")

    with pytest.raises(VLMAdapterError, match="valid JSON"):
        reviewer.transcribe_crop(str(_crop(tmp_path)), page_index=0, pair_index=0)


def test_reviewer_rejects_truncated_model_output(tmp_path: Path, vlm_server) -> None:
    endpoint, handler = vlm_server
    handler.response_content = '{"text":"partial","uncertain":false}'
    handler.finish_reason = "length"
    reviewer = OpenAICompatibleVLMReviewer(endpoint=endpoint, model="fixture-vision")

    with pytest.raises(VLMAdapterError, match="truncated"):
        reviewer.transcribe_crop(str(_crop(tmp_path)), page_index=0, pair_index=0)



@pytest.mark.parametrize(
    "endpoint",
    [
        "ftp://127.0.0.1/v1",
        "http://user:secret@127.0.0.1/v1",
        "http://127.0.0.1/v1?token=secret",
        "http://127.0.0.1:invalid/v1",
    ],
)
def test_reviewer_rejects_unsafe_or_invalid_endpoint_urls(endpoint: str) -> None:
    with pytest.raises(ValueError, match="VLM endpoint"):
        OpenAICompatibleVLMReviewer(endpoint=endpoint, model="vision-model")


def test_reviewer_rejects_missing_or_invalid_crop(tmp_path: Path, vlm_server) -> None:
    endpoint, _handler = vlm_server
    reviewer = OpenAICompatibleVLMReviewer(endpoint=endpoint, model="fixture-vision")

    with pytest.raises(VLMAdapterError, match="cannot read"):
        reviewer.transcribe_crop(str(tmp_path / "missing.png"), page_index=0, pair_index=0)

    bad_image = tmp_path / "bad.png"
    bad_image.write_bytes(b"not an image")
    with pytest.raises(VLMAdapterError, match="valid image"):
        reviewer.transcribe_crop(str(bad_image), page_index=0, pair_index=0)


def test_reviewer_reports_http_failures_without_retrying_elsewhere(
    tmp_path: Path, vlm_server
) -> None:
    endpoint, handler = vlm_server
    handler.response_status = 503
    reviewer = OpenAICompatibleVLMReviewer(endpoint=endpoint, model="fixture-vision")

    with pytest.raises(VLMAdapterError, match="HTTP 503"):
        reviewer.transcribe_crop(str(_crop(tmp_path)), page_index=0, pair_index=0)


def test_reviewer_does_not_follow_endpoint_redirects(
    tmp_path: Path, vlm_server
) -> None:
    endpoint, handler = vlm_server
    handler.response_status = 307
    handler.redirect_url = "http://example.invalid/exfiltrate"
    reviewer = OpenAICompatibleVLMReviewer(endpoint=endpoint, model="fixture-vision")

    with pytest.raises(VLMAdapterError, match="HTTP 307"):
        reviewer.transcribe_crop(str(_crop(tmp_path)), page_index=0, pair_index=0)


def test_reviewer_requires_boolean_uncertainty(tmp_path: Path, vlm_server) -> None:
    endpoint, handler = vlm_server
    handler.response_content = '{"text":"reading","uncertain":"false"}'
    reviewer = OpenAICompatibleVLMReviewer(endpoint=endpoint, model="fixture-vision")

    with pytest.raises(VLMAdapterError, match="boolean"):
        reviewer.transcribe_crop(str(_crop(tmp_path)), page_index=0, pair_index=0)


def test_reviewer_validates_timeout_and_model_identity() -> None:
    with pytest.raises(ValueError, match="timeout"):
        OpenAICompatibleVLMReviewer(
            endpoint="http://127.0.0.1:1234/v1",
            model="vision-model",
            timeout_seconds=float("nan"),
        )
    with pytest.raises(ValueError, match="model name"):
        OpenAICompatibleVLMReviewer(
            endpoint="http://127.0.0.1:1234/v1",
            model="  ",
        )



def test_remote_endpoint_requires_https_even_when_opted_in() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        OpenAICompatibleVLMReviewer(
            endpoint="http://vision.example.test/v1",
            model="vision-model",
            allow_remote=True,
        )


def test_reviewer_accepts_a_complete_chat_completions_url(tmp_path: Path, vlm_server) -> None:
    base, handler = vlm_server
    endpoint = base + "/chat/completions"
    reviewer = OpenAICompatibleVLMReviewer(endpoint=endpoint, model="fixture-vision")

    reading = reviewer.transcribe_crop(str(_crop(tmp_path)), page_index=0, pair_index=0)

    assert reading.text == "one\ntwo"
    assert handler.request_path == "/v1/chat/completions"
