"""Read-only inspection of OCR frame and model-crop provenance."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable
from pathlib import Path

from PIL import Image

# Kept independent of the report type so this module cannot mutate the store.
Emit = Callable[[str, str, str, str | None], None]


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def _inspect_image(path: Path) -> tuple[str, tuple[int, int] | None]:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    try:
        with Image.open(path) as image:
            size = image.size
    except (OSError, Image.DecompressionBombError, ValueError):
        size = None
    return digest.hexdigest(), size


def _audit_legacy_vlm(connection: sqlite3.Connection, emit: Emit, has_crop_table: bool) -> None:
    join = "LEFT JOIN crop_input_evidence c ON c.vlm_review_id=v.id" if has_crop_table else ""
    where = "WHERE c.id IS NULL" if has_crop_table else ""
    rows = connection.execute(
        f"SELECT v.id, v.crop_path, v.crop_sha256 FROM vlm_reviews v {join} {where} ORDER BY v.id"
    ).fetchall()
    for row in rows:
        path = Path(str(row["crop_path"])).expanduser()
        try:
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError as exc:
            emit(
                "legacy_vlm_crop_missing",
                "error",
                f"legacy VLM review {row['id']}: recorded crop is missing or unreadable: {exc}",
                str(path),
            )
            continue
        if digest.hexdigest() != row["crop_sha256"]:
            emit(
                "legacy_vlm_crop_hash_mismatch",
                "error",
                f"legacy VLM review {row['id']}: crop differs from its recorded SHA-256; geometry remains unknown.",
                str(path),
            )


def audit_model_evidence(connection: sqlite3.Connection, emit: Emit) -> None:
    tables = _tables(connection)
    if "engine_runs" not in tables:
        return
    cache: dict[Path, tuple[str, tuple[int, int] | None] | OSError] = {}
    if "page_preprocess" in tables:
        preprocessed = connection.execute("""SELECT pp.*, p.project_id, p.page_index
            FROM page_preprocess pp JOIN pages p ON p.id=pp.page_id ORDER BY pp.id""").fetchall()
        for row in preprocessed:
            path = Path(str(row["output_image"])).expanduser()
            label = f"preprocessed image for project {row['project_id']} page {int(row['page_index']) + 1}"
            if path not in cache:
                try:
                    cache[path] = _inspect_image(path)
                except OSError as exc:
                    cache[path] = exc
            actual = cache[path]
            if isinstance(actual, OSError):
                emit(
                    "preprocess_missing",
                    "error",
                    f"{label} is missing or unreadable: {actual}",
                    str(path),
                )
                continue
            digest, size = actual
            if digest != row["output_sha256"]:
                emit(
                    "preprocess_hash_mismatch",
                    "error",
                    f"{label}: SHA-256 differs from the recorded output.",
                    str(path),
                )
            if size != (row["output_width"], row["output_height"]):
                emit(
                    "preprocess_geometry_mismatch",
                    "error",
                    f"{label}: dimensions differ from recorded geometry or image is unreadable.",
                    str(path),
                )
    columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(engine_runs)")}
    scope = "r.scope" if "scope" in columns else "NULL"
    if "ocr_input_frames" not in tables:
        count = connection.execute(
            f"SELECT count(*) FROM engine_runs r WHERE {scope} IS NULL"
        ).fetchone()[0]
        if count:
            emit(
                "ocr_frame_unknown",
                "warning",
                f"{count} legacy whole-page OCR run(s) have no recorded input frame; do not infer provenance.",
                None,
            )
    else:
        preprocess_join = (
            "LEFT JOIN page_preprocess pp ON pp.page_id=p.id" if "page_preprocess" in tables else ""
        )
        prep_fields = (
            "pp.output_image, pp.output_sha256"
            if preprocess_join
            else "NULL AS output_image, NULL AS output_sha256"
        )
        rows = connection.execute(f"""SELECT r.id, p.project_id, p.page_index, p.source_image,
                   f.kind, f.sha256, f.width, f.height, {prep_fields}
            FROM engine_runs r JOIN pages p ON p.id=r.page_id
            LEFT JOIN ocr_input_frames f ON f.run_id=r.id {preprocess_join}
            WHERE {scope} IS NULL ORDER BY r.id""").fetchall()
        for row in rows:
            label = f"OCR run {row['id']} (project {row['project_id']} page {int(row['page_index']) + 1})"
            if row["kind"] is None:
                emit(
                    "ocr_frame_unknown",
                    "warning",
                    f"{label} has no recorded input frame; do not infer provenance.",
                    None,
                )
                continue
            if row["kind"] == "original":
                path = Path(str(row["source_image"])).expanduser()
            elif row["kind"] == "preprocessed" and row["output_image"] is not None:
                path = Path(str(row["output_image"])).expanduser()
                if row["output_sha256"] != row["sha256"]:
                    emit(
                        "ocr_frame_binding_mismatch",
                        "error",
                        f"{label}: preprocessing record does not match the OCR frame.",
                        str(path),
                    )
            else:
                emit(
                    "ocr_frame_binding_mismatch",
                    "error",
                    f"{label}: recorded frame cannot be resolved.",
                    None,
                )
                continue
            if path not in cache:
                try:
                    cache[path] = _inspect_image(path)
                except OSError as exc:
                    cache[path] = exc
            actual = cache[path]
            if isinstance(actual, OSError):
                emit(
                    "ocr_frame_missing",
                    "error",
                    f"{label}: frame is missing or unreadable: {actual}",
                    str(path),
                )
                continue
            digest, size = actual
            if digest != row["sha256"]:
                emit(
                    "ocr_frame_hash_mismatch",
                    "error",
                    f"{label}: frame SHA-256 differs from the recorded input.",
                    str(path),
                )
            if size != (row["width"], row["height"]):
                emit(
                    "ocr_frame_geometry_mismatch",
                    "error",
                    f"{label}: frame dimensions differ from recorded geometry or image is unreadable.",
                    str(path),
                )

    if "verification_runs" in tables and "ocr_input_frames" in tables:
        verifications = connection.execute("""SELECT v.id, v.page_id,
            a.page_id AS primary_page, b.page_id AS secondary_page,
            f.kind, f.sha256, f.width, f.height,
            f2.kind AS secondary_kind, f2.sha256 AS secondary_sha,
            f2.width AS secondary_width, f2.height AS secondary_height
            FROM verification_runs v
            LEFT JOIN engine_runs a ON a.id=v.primary_run_id
            LEFT JOIN engine_runs b ON b.id=v.secondary_run_id
            LEFT JOIN ocr_input_frames f ON f.run_id=v.primary_run_id
            LEFT JOIN ocr_input_frames f2 ON f2.run_id=v.secondary_run_id
            ORDER BY v.id""").fetchall()
        for row in verifications:
            different_pages = (
                row["page_id"] != row["primary_page"] or row["page_id"] != row["secondary_page"]
            )
            different_frames = (
                row["sha256"] is not None
                and row["secondary_sha"] is not None
                and (row["kind"], row["sha256"], row["width"], row["height"])
                != (
                    row["secondary_kind"],
                    row["secondary_sha"],
                    row["secondary_width"],
                    row["secondary_height"],
                )
            )
            if different_pages or different_frames:
                emit(
                    "verification_frame_mismatch",
                    "error",
                    f"verification {row['id']}: OCR runs belong to different pages or input frames.",
                    None,
                )

    if "vlm_reviews" in tables:
        _audit_legacy_vlm(connection, emit, "crop_input_evidence" in tables)

    if "crop_input_evidence" not in tables:
        scoped = connection.execute(
            f"SELECT count(*) FROM engine_runs r WHERE {scope} LIKE 'pair:%'"
        ).fetchone()[0]
        vlms = (
            connection.execute("SELECT count(*) FROM vlm_reviews").fetchone()[0]
            if "vlm_reviews" in tables
            else 0
        )
        if scoped or vlms:
            emit(
                "crop_provenance_unknown",
                "warning",
                f"{scoped} region OCR run(s) and {vlms} VLM review(s) have no recorded crop geometry; do not infer provenance.",
                None,
            )
        return

    missing_ocr = connection.execute(f"""SELECT count(*) FROM engine_runs r
        LEFT JOIN crop_input_evidence c ON c.ocr_run_id=r.id
        WHERE {scope} LIKE 'pair:%' AND c.id IS NULL""").fetchone()[0]
    missing_vlm = connection.execute("""SELECT count(*) FROM vlm_reviews v
        LEFT JOIN crop_input_evidence c ON c.vlm_review_id=v.id WHERE c.id IS NULL""").fetchone()[0]
    if missing_ocr or missing_vlm:
        emit(
            "crop_provenance_unknown",
            "warning",
            f"{missing_ocr} region OCR run(s) and {missing_vlm} VLM review(s) have no recorded crop geometry; do not infer provenance.",
            None,
        )
    rows = connection.execute("""SELECT c.*, v.page_id, v.primary_run_id, v.secondary_run_id,
        f.kind, f.sha256 AS primary_sha, f.width AS frame_width, f.height AS frame_height,
        f2.kind AS secondary_kind, f2.sha256 AS secondary_sha,
        f2.width AS secondary_width, f2.height AS secondary_height,
        r.page_id AS run_page, r.scope AS run_scope,
        vr.crop_path AS review_path, vr.crop_sha256 AS review_sha,
        a.verification_run_id AS review_verification, a.pair_index AS review_pair
        FROM crop_input_evidence c
        LEFT JOIN verification_runs v ON v.id=c.verification_id
        LEFT JOIN ocr_input_frames f ON f.run_id=v.primary_run_id
        LEFT JOIN ocr_input_frames f2 ON f2.run_id=v.secondary_run_id
        LEFT JOIN engine_runs r ON r.id=c.ocr_run_id
        LEFT JOIN vlm_reviews vr ON vr.id=c.vlm_review_id
        LEFT JOIN review_candidates rc ON rc.id=vr.review_candidate_id
        LEFT JOIN alignments a ON a.id=rc.alignment_id ORDER BY c.id""").fetchall()
    cache_crop: dict[Path, tuple[str, tuple[int, int] | None] | OSError] = {}
    for row in rows:
        label = f"crop evidence {row['id']}"
        path = Path(str(row["crop_path"])).expanduser()
        if row["primary_sha"] is None or row["secondary_sha"] is None:
            emit(
                "crop_parent_unknown",
                "warning",
                f"{label}: parent OCR frame is unknown; do not infer provenance.",
                str(path),
            )
        elif (row["primary_sha"], row["frame_width"], row["frame_height"], row["kind"]) != (
            row["secondary_sha"],
            row["secondary_width"],
            row["secondary_height"],
            row["secondary_kind"],
        ) or row["frame_sha256"] != row["primary_sha"]:
            emit(
                "crop_binding_mismatch",
                "error",
                f"{label}: parent OCR frame binding does not match verification.",
                str(path),
            )
        if row["ocr_run_id"] is not None and (
            row["run_page"] != row["page_id"] or row["run_scope"] != f"pair:{row['pair_index']}"
        ):
            emit(
                "crop_binding_mismatch",
                "error",
                f"{label}: OCR run belongs to another page or pair.",
                str(path),
            )
        if row["vlm_review_id"] is not None and (
            row["review_verification"] != row["verification_id"]
            or row["review_pair"] != row["pair_index"]
            or row["review_sha"] != row["crop_sha256"]
            or row["review_path"] != row["crop_path"]
        ):
            emit(
                "crop_binding_mismatch",
                "error",
                f"{label}: VLM review does not match the recorded crop.",
                str(path),
            )
        if row["frame_width"] is not None and (
            row["x"] + row["width"] > row["frame_width"]
            or row["y"] + row["height"] > row["frame_height"]
        ):
            emit(
                "crop_geometry_invalid",
                "error",
                f"{label}: crop geometry exceeds its parent frame.",
                str(path),
            )
        if path not in cache_crop:
            try:
                cache_crop[path] = _inspect_image(path)
            except OSError as exc:
                cache_crop[path] = exc
        actual = cache_crop[path]
        if isinstance(actual, OSError):
            emit(
                "crop_missing",
                "error",
                f"{label}: crop is missing or unreadable: {actual}",
                str(path),
            )
            continue
        digest, size = actual
        if digest != row["crop_sha256"]:
            emit(
                "crop_hash_mismatch",
                "error",
                f"{label}: crop SHA-256 differs from the model input.",
                str(path),
            )
        if size != (row["width"] * row["scale"], row["height"] * row["scale"]):
            emit(
                "crop_geometry_invalid",
                "error",
                f"{label}: crop image dimensions differ from recorded geometry.",
                str(path),
            )
