from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from scribeforge.pipeline.vlm_review import ReviewEvidence
from scribeforge.storage.sqlite import SQLiteStore


class SQLiteVLMReviewState:
    def __init__(self, store: SQLiteStore, verification_id: int) -> None:
        self._store = store
        self._verification_id = verification_id

    def completed_pairs(self, page_index: int) -> frozenset[int]:
        persisted_page = self._store.verification_page_index(self._verification_id)
        if persisted_page != page_index:
            raise ValueError("VLM review state page does not match requested page")
        return self._store.reviewed_pair_indexes(self._verification_id)

    def append(self, evidence: ReviewEvidence) -> None:
        crop_path = Path(evidence.crop_path)
        crop_sha256 = sha256(crop_path.read_bytes()).hexdigest()
        self._store.record_vlm_review(
            self._verification_id,
            evidence.reading,
            crop_path=evidence.crop_path,
            crop_sha256=crop_sha256,
        )
