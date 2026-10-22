"""OpenAI-compatible HTTP adapter for local or explicitly opted-in VLM endpoints.

The adapter speaks the chat-completions wire format and sends only the
materialized review crop, never the whole source page.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import math
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from PIL import Image

from scribeforge.domain.vlm import LITERAL_TRANSCRIPTION_POLICY, VLMReading

_MAX_IMAGE_BYTES = 20 * 1024 * 1024
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_ERROR_BODY_BYTES = 4096


class _RejectRedirectHandler(HTTPRedirectHandler):
    """Prevent a configured endpoint from redirecting private crop bytes elsewhere."""

    def redirect_request(
        self,
        req: Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        return None


# Do not let user proxy settings route local review crops through an intermediary.
_HTTP_OPENER = build_opener(ProxyHandler({}), _RejectRedirectHandler())


class VLMAdapterError(RuntimeError):
    """A transport, protocol, or response-validation failure from a VLM endpoint."""


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _completion_url(endpoint: str, *, allow_remote: bool) -> str:
    try:
        parsed = urlsplit(endpoint.strip())
        # Accessing .port validates malformed port values as well.
        _ = parsed.port
    except ValueError as exc:
        raise ValueError(f"invalid VLM endpoint URL: {exc}") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or not parsed.netloc:
        raise ValueError("VLM endpoint must be an absolute http:// or https:// URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("VLM endpoint URL must not embed credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("VLM endpoint URL must not include a query or fragment")
    local_endpoint = _is_loopback(parsed.hostname)
    if not local_endpoint and not allow_remote:
        raise ValueError(
            "non-loopback VLM endpoints are disabled by default; "
            "set allow_remote=True only when you explicitly intend to send review crops remotely"
        )
    if not local_endpoint and parsed.scheme != "https":
        raise ValueError("remote VLM endpoints must use HTTPS")

    path = parsed.path.rstrip("/")
    if not path.endswith("/chat/completions"):
        path += "/chat/completions"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _image_data_url(path: Path) -> str:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise VLMAdapterError(f"cannot read VLM review crop: {exc}") from exc
    if not data:
        raise VLMAdapterError("VLM review crop is empty")
    if len(data) > _MAX_IMAGE_BYTES:
        raise VLMAdapterError(
            f"VLM review crop exceeds the {_MAX_IMAGE_BYTES // (1024 * 1024)} MiB limit"
        )

    try:
        with Image.open(BytesIO(data)) as image:
            image_format = image.format
            image.verify()
    except Exception as exc:
        raise VLMAdapterError(f"VLM review crop is not a valid image: {exc}") from exc

    mime_types = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}
    mime_type = mime_types.get(image_format or "")
    if mime_type is None:
        raise VLMAdapterError(
            f"unsupported VLM review crop format: {image_format or 'unknown'}"
        )
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


class OpenAICompatibleVLMReviewer:
    """Transcribe a review crop through an OpenAI-compatible vision chat endpoint.

    Endpoint is a base URL such as http://127.0.0.1:1234/v1 or a complete
    /chat/completions URL. Remote hosts require explicit allow_remote=True.
    """

    def __init__(
        self,
        *,
        endpoint: str,
        model: str,
        api_key: str | None = None,
        timeout_seconds: float = 120.0,
        allow_remote: bool = False,
    ) -> None:
        if not model.strip():
            raise ValueError("VLM model name must not be empty")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("VLM timeout must be a finite number greater than zero")
        self._url = _completion_url(endpoint, allow_remote=allow_remote)
        self._model = model.strip()
        self._api_key = api_key.strip() if api_key and api_key.strip() else None
        self._timeout_seconds = timeout_seconds

    def transcribe_crop(self, image_path: str, page_index: int, pair_index: int) -> VLMReading:
        if page_index < 0 or pair_index < 0:
            raise ValueError("VLM reading indexes must be non-negative")
        data_url = _image_data_url(Path(image_path))
        system_prompt = (
            f"{LITERAL_TRANSCRIPTION_POLICY}\n\n"
            "Return exactly one valid JSON object with exactly two fields: "
            '"text" (a string containing only the literal visible transcription) and '
            '"uncertain" (a boolean). Do not wrap the JSON in Markdown. '
            "Set uncertain to true if any relevant glyph or mark cannot be read confidently. "
            "Never infer, normalize, correct, or complete text."
        )
        payload = {
            "model": self._model,
            "temperature": 0,
            "max_tokens": 1024,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Transcribe only the text visible in this review crop. "
                                "Preserve punctuation, spelling, and visible line breaks. "
                                "Return the required JSON object."
                            ),
                        },
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
        }
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._api_key is not None:
            headers["Authorization"] = f"Bearer {self._api_key}"
        request = Request(
            self._url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with _HTTP_OPENER.open(request, timeout=self._timeout_seconds) as response:
                raw_response = response.read(_MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            detail = exc.read(_ERROR_BODY_BYTES).decode("utf-8", errors="replace").strip()
            suffix = f": {detail}" if detail else ""
            raise VLMAdapterError(f"VLM endpoint returned HTTP {exc.code}{suffix}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise VLMAdapterError(
                f"VLM request failed ({type(exc).__name__}): {exc}"
            ) from exc

        if len(raw_response) > _MAX_RESPONSE_BYTES:
            raise VLMAdapterError("VLM endpoint response exceeds the 2 MiB limit")
        try:
            response_json = json.loads(raw_response.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise VLMAdapterError("VLM endpoint returned invalid JSON") from exc
        if not isinstance(response_json, dict):
            raise VLMAdapterError("VLM endpoint response must be a JSON object")
        choices = response_json.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise VLMAdapterError("VLM endpoint response has no completion choice")
        choice = choices[0]
        finish_reason = choice.get("finish_reason")
        if finish_reason == "length":
            raise VLMAdapterError("VLM completion was truncated by the token limit")
        if finish_reason is not None and not isinstance(finish_reason, str):
            raise VLMAdapterError("VLM completion has an invalid finish_reason")
        if finish_reason not in {None, "stop", "end_turn"}:
            raise VLMAdapterError(
                f"VLM completion did not finish normally (finish_reason={finish_reason})"
            )
        message = choice.get("message")
        if not isinstance(message, dict):
            raise VLMAdapterError("VLM completion choice has no message")
        content = message.get("content")
        if isinstance(content, list):
            fragments: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    fragment = item.get("text")
                    if isinstance(fragment, str):
                        fragments.append(fragment)
            content = "".join(fragments) if fragments else None
        if not isinstance(content, str):
            raise VLMAdapterError("VLM completion message has no text content")
        try:
            reading_json = json.loads(content)
        except json.JSONDecodeError as exc:
            raise VLMAdapterError("VLM completion content is not valid JSON") from exc
        if not isinstance(reading_json, dict) or set(reading_json) != {"text", "uncertain"}:
            raise VLMAdapterError(
                'VLM completion JSON must contain exactly "text" and "uncertain" fields'
            )
        reading_text = reading_json["text"]
        uncertain = reading_json["uncertain"]
        if not isinstance(reading_text, str) or not isinstance(uncertain, bool):
            raise VLMAdapterError('"text" must be a string and "uncertain" must be a boolean')

        response_model = response_json.get("model")
        model_version = (
            response_model.strip()
            if isinstance(response_model, str) and response_model.strip()
            else self._model
        )
        return VLMReading(
            page_index=page_index,
            pair_index=pair_index,
            model=self._model,
            model_version=model_version,
            text=reading_text,
            uncertain=uncertain,
        )
