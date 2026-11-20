"""Local export receipts and independent, read-only byte verification.

A receipt is not a signature: an actor that can replace all adjacent files can
make them internally consistent. No format document is parsed by this check.
"""

from __future__ import annotations

import hashlib
import json
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Literal

FORMAT_SUFFIXES = (".epub", ".docx")
_DIGEST = re.compile(r"[a-f0-9]{64}\Z")
_CHUNK_SIZE = 1024 * 1024
_RECEIPT_LIMIT = 128 * 1024
_NORMALIZATION_LIMIT = 64 * 1024 * 1024
FindingLevel = Literal["error", "warning", "info"]


@dataclass(frozen=True, slots=True)
class FileFingerprint:
    size: int
    sha256: str

    def as_json(self) -> dict[str, object]:
        return {"size": self.size, "sha256": self.sha256}

    @classmethod
    def from_bytes(cls, data: bytes) -> FileFingerprint:
        return cls(len(data), hashlib.sha256(data).hexdigest())


def _identity(file: BinaryIO) -> tuple[int, int, int, int, int]:
    import os

    info = os.fstat(file.fileno())
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("not a regular file")
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _ensure_regular(path: Path) -> None:
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError(f"not a regular file: {path.name}")


def fingerprint(path: Path) -> FileFingerprint:
    """Stream bytes without loading the entire EPUB/DOCX into memory."""
    _ensure_regular(path)
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as file:
        before = _identity(file)
        while chunk := file.read(_CHUNK_SIZE):
            digest.update(chunk)
            size += len(chunk)
        if before != _identity(file) or size != before[2]:
            raise ValueError(f"file changed while reading: {path.name}")
        after = path.stat()
        if (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ) != before:
            raise ValueError(f"file was replaced while reading: {path.name}")
    return FileFingerprint(size, digest.hexdigest())


def receipt_path(path: Path) -> Path:
    return Path(f"{path}.receipt.json")


def normalization_path(path: Path) -> Path:
    return Path(f"{path}.normalization.json")


def make_export_receipt(
    path: Path,
    *,
    artifact: FileFingerprint,
    normalization: FileFingerprint,
    source_sha256: str,
    project_id: int,
    title: str,
    timestamp: str,
    strict_acceptance: bool,
    include_page_furniture: bool,
    policy: str,
    pages: int,
    accepted_pairs: int,
) -> bytes:
    return (
        json.dumps(
            {
                "schema_version": 1,
                "kind": "scribeforge_trusted_export",
                "artifact": {
                    "file": path.name,
                    "format": path.suffix.lower()[1:],
                    **artifact.as_json(),
                },
                "normalization": {**normalization.as_json(), "policy": policy},
                "project": {
                    "id": project_id,
                    "title": title[:2048] or path.stem,
                    "source_sha256": source_sha256 if _DIGEST.fullmatch(source_sha256) else None,
                },
                "export": {
                    "modified": timestamp,
                    "strict_acceptance": strict_acceptance,
                    "include_page_furniture": include_page_furniture,
                    "pages": pages,
                    "accepted_pairs": accepted_pairs,
                },
            },
            ensure_ascii=True,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


def _object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError(f"{label} is not a JSON object")
    return value


def _text(value: object, label: str, limit: int = 2048) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"invalid {label}")
    return value


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ValueError(f"invalid {label}")
    return value


def _count(value: object, label: str, *, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value < 2**63:
        raise ValueError(f"invalid {label}")
    return value


def _flag(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"invalid {label}")
    return value


def _read_sidecar(path: Path, limit: int) -> tuple[dict[str, object], FileFingerprint]:
    _ensure_regular(path)
    with path.open("rb") as file:
        identity = _identity(file)
        content = file.read(limit + 1)
        if identity != _identity(file):
            raise ValueError(f"sidecar changed during read: {path.name}")
    if len(content) > limit:
        raise ValueError(f"sidecar is larger than the verification limit: {path.name}")
    try:
        return _object(json.loads(content), path.name), FileFingerprint.from_bytes(content)
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise ValueError(f"could not parse sidecar: {path.name}") from exc


@dataclass(frozen=True, slots=True)
class ReceiptMetadata:
    file: str
    format: str
    artifact: FileFingerprint
    normalization: FileFingerprint
    policy: str
    project_id: int
    title: str
    source_sha256: str | None
    modified: str
    strict: bool
    furniture: bool
    pages: int
    accepted_pairs: int

    @classmethod
    def parse(cls, fields: dict[str, object]) -> ReceiptMetadata:
        if type(fields.get("schema_version")) is not int or fields["schema_version"] != 1:
            raise ValueError("unsupported export receipt schema")
        if fields.get("kind") != "scribeforge_trusted_export":
            raise ValueError("receipt is not a ScribeForge trusted export receipt")
        artifact = _object(fields.get("artifact"), "receipt artifact")
        normalization = _object(fields.get("normalization"), "receipt normalization")
        project = _object(fields.get("project"), "receipt project")
        settings = _object(fields.get("export"), "receipt export settings")
        return cls(
            file=_text(artifact.get("file"), "receipt artifact filename"),
            format=_text(artifact.get("format"), "receipt format", 12),
            artifact=FileFingerprint(
                _count(artifact.get("size"), "artifact size", minimum=1),
                _digest(artifact.get("sha256"), "artifact SHA-256"),
            ),
            normalization=FileFingerprint(
                _count(normalization.get("size"), "normalization sidecar size", minimum=1),
                _digest(normalization.get("sha256"), "normalization sidecar SHA-256"),
            ),
            policy=_text(normalization.get("policy"), "normalization policy", 80),
            project_id=_count(project.get("id"), "project ID", minimum=1),
            title=_text(project.get("title"), "project title"),
            source_sha256=(
                None
                if project.get("source_sha256") is None
                else _digest(project["source_sha256"], "project source SHA-256")
            ),
            modified=_text(settings.get("modified"), "modified timestamp", 80),
            strict=_flag(settings.get("strict_acceptance"), "strict acceptance flag"),
            furniture=_flag(settings.get("include_page_furniture"), "furniture flag"),
            pages=_count(settings.get("pages"), "physical page count", minimum=1),
            accepted_pairs=_count(settings.get("accepted_pairs"), "accepted pair count"),
        )

    def as_json(self) -> dict[str, object]:
        return {
            "file": self.file,
            "format": self.format,
            "artifact": self.artifact.as_json(),
            "normalization": self.normalization.as_json(),
            "policy": self.policy,
            "project_id": self.project_id,
            "title": self.title,
            "source_sha256": self.source_sha256,
            "modified": self.modified,
            "strict_acceptance": self.strict,
            "include_page_furniture": self.furniture,
            "pages": self.pages,
            "accepted_pairs": self.accepted_pairs,
        }


@dataclass(slots=True)
class _Verification:
    findings: list[dict[str, str]] = field(default_factory=list)

    def add(self, code: str, level: FindingLevel, message: str) -> None:
        self.findings.append({"code": code, "severity": level, "message": message[:1200]})

    @property
    def has_errors(self) -> bool:
        return any(item["severity"] == "error" for item in self.findings)


def _read_receipt(path: Path, result: _Verification) -> ReceiptMetadata | None:
    try:
        fields, _ = _read_sidecar(path, _RECEIPT_LIMIT)
        return ReceiptMetadata.parse(fields)
    except FileNotFoundError:
        result.add("receipt_missing", "warning", "No adjacent export receipt was found.")
    except (OSError, ValueError, TypeError) as exc:
        result.add("receipt_invalid", "error", str(exc))
    return None


def _check_normalization(
    path: Path, artifact: FileFingerprint, receipt: ReceiptMetadata | None, result: _Verification
) -> bool:
    try:
        fields, measured = _read_sidecar(normalization_path(path), _NORMALIZATION_LIMIT)
        output = _object(fields.get("output"), "normalization output")
        declared = _digest(output.get("sha256"), "normalization output SHA-256")
        file = _text(output.get("file"), "normalization output filename")
    except FileNotFoundError:
        result.add(
            "normalization_missing",
            "error" if receipt else "warning",
            "No adjacent normalization report was found.",
        )
        return False
    except (OSError, ValueError, TypeError) as exc:
        result.add("normalization_invalid", "error", str(exc))
        return False
    if file != path.name:
        result.add(
            "normalization_filename_differs",
            "warning",
            "Normalization report refers to a different filename.",
        )
    if declared != artifact.sha256:
        result.add(
            "normalization_artifact_mismatch",
            "error",
            "The normalization report refers to different book bytes.",
        )
    if receipt is not None:
        if measured != receipt.normalization:
            result.add(
                "normalization_digest_mismatch",
                "error",
                "The normalization report bytes differ from the receipt.",
            )
        if fields.get("policy") != receipt.policy:
            result.add(
                "normalization_policy_mismatch",
                "error",
                "The normalization policy differs from the receipt.",
            )
    return declared == artifact.sha256


def verify_export_artifact(path: Path, *, expected_source: str | None = None) -> dict[str, object]:
    suffix = path.suffix.lower()
    if suffix not in FORMAT_SUFFIXES:
        raise ValueError("choose an EPUB or DOCX file")
    if expected_source is not None:
        _digest(expected_source, "expected project source SHA-256")
    measured = fingerprint(path)
    result = _Verification()
    receipt = _read_receipt(receipt_path(path), result)
    project_match = None
    if receipt is not None:
        if receipt.artifact != measured:
            result.add(
                "artifact_digest_mismatch",
                "error",
                "The book byte size or SHA-256 differs from the receipt.",
            )
        if receipt.format != suffix[1:]:
            result.add(
                "format_mismatch", "error", "The file extension does not match the receipt format."
            )
        if receipt.file != path.name:
            result.add(
                "filename_differs", "warning", "The filename differs from the name saved at export."
            )
        if expected_source is not None and receipt.source_sha256 is None:
            result.add(
                "project_source_unknown",
                "warning",
                "The receipt has no valid project source digest.",
            )
        elif expected_source is not None:
            project_match = expected_source == receipt.source_sha256
            if not project_match:
                result.add(
                    "project_source_mismatch",
                    "error",
                    "The receipt cites a different source document.",
                )
    elif expected_source is not None:
        result.add(
            "project_source_unknown",
            "warning",
            "The project source cannot be compared without a valid receipt.",
        )
    normalization_match = _check_normalization(path, measured, receipt, result)
    if result.has_errors:
        status = "mismatch"
    elif receipt is not None:
        status = "consistent"
    elif normalization_match:
        status = "legacy_match"
    else:
        status = "no_receipt"
    return {
        "schema_version": 1,
        "file": path.name,
        "format": suffix[1:],
        "status": status,
        "artifact": measured.as_json(),
        "normalization_matches_artifact": normalization_match,
        "project_source_matches": project_match,
        "receipt": None if receipt is None else receipt.as_json(),
        "findings": result.findings,
    }
