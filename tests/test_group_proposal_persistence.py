from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scribeforge.domain.alignment import align_pages
from scribeforge.domain.ocr import BoundingBox, OCRLine, PageDirection, PageOCRResult
from scribeforge.domain.risk import assess_page
from scribeforge.storage.sqlite import SQLiteStore


def _prepare(tmp_path: Path) -> tuple[SQLiteStore, int, int, int]:
    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    project = store.create_project("Book", "source.pdf", "hash")
    store.add_page(project, 0, "page.png", 100, 100)
    a = PageOCRResult(0, "a", "1", (OCRLine("春眠不觉晓", BoundingBox(0.1, 0.2, 0.6, 0.05)),))
    b = PageOCRResult(0, "b", "1", (OCRLine("春眠", BoundingBox(0.1, 0.2, 0.24, 0.05)),
                                   OCRLine("不觉晓", BoundingBox(0.34, 0.2, 0.36, 0.05))))
    return store, project, store.record_ocr(project, a), store.record_ocr(project, b)


def test_stored_proposal_roundtrip_preserves_source_indexes_and_is_separate(tmp_path: Path) -> None:
    store, project, a, b = _prepare(tmp_path)
    proposal_id = store.record_group_proposals(project, a, b, direction=PageDirection.HORIZONTAL_LR)
    saved = store.load_group_proposals(project, proposal_id)
    assert (saved.primary_run_id, saved.secondary_run_id) == (a, b)
    assert saved.proposals.groups[0].primary_indexes == (0,)
    assert saved.proposals.groups[0].secondary_indexes == (0, 1)
    assert saved.proposals.groups[0].status == "proposed"
    with pytest.raises(KeyError):
        store.latest_verification_id(project, 0)
    assert store.latest_decision(project, 0, 0) is None
    second = store.record_group_proposals(project, a, b, direction=PageDirection.HORIZONTAL_LR)
    assert second != proposal_id
    assert store.load_group_proposals(project, proposal_id).proposals == saved.proposals


def test_v14_upgrade_preserves_alignment_and_human_decision(tmp_path: Path) -> None:
    store, project, a, b = _prepare(tmp_path)
    alignment = align_pages(store.load_ocr_run(a), store.load_ocr_run(b))
    risks = assess_page(alignment, store.load_ocr_run(a), store.load_ocr_run(b))
    verification = store.record_verification(a, b, alignment, risks, ())
    store.append_decision(project_id=project, page_index=0, pair_index=0, selected_text="旧决定",
                          actor="human", evidence_refs=(f"verification_run:{verification}",))
    before_alignment = store.load_verification(verification)
    before_decision = store.list_decisions(project, 0, 0)
    with sqlite3.connect(tmp_path / "project.sqlite3") as db:
        db.execute("DROP TABLE IF EXISTS group_proposal_runs")
        db.execute("PRAGMA user_version=14")
    store.initialize()
    with sqlite3.connect(tmp_path / "project.sqlite3") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 15
    assert store.load_verification(verification) == before_alignment
    assert store.list_decisions(project, 0, 0) == before_decision
    proposal_id = store.record_group_proposals(project, a, b, direction=PageDirection.HORIZONTAL_LR)
    assert store.load_group_proposals(project, proposal_id).proposals.groups[0].secondary_indexes == (0, 1)
    assert store.list_decisions(project, 0, 0) == before_decision


def test_group_proposal_rejects_cross_page_or_project(tmp_path: Path) -> None:
    store, project, a, b = _prepare(tmp_path)
    store.add_page(project, 1, "page2.png", 100, 100)
    other = store.record_ocr(project, PageOCRResult(1, "b", "1", ()))
    with pytest.raises(ValueError, match="same.*page"):
        store.record_group_proposals(project, a, other, direction=PageDirection.HORIZONTAL_LR)
    another = store.create_project("Other", "other.pdf", "other")
    with pytest.raises(ValueError, match="project"):
        store.record_group_proposals(another, a, b, direction=PageDirection.HORIZONTAL_LR)
    proposal_id = store.record_group_proposals(project, a, b, direction=PageDirection.HORIZONTAL_LR)
    with pytest.raises((KeyError, ValueError)):
        store.load_group_proposals(another, proposal_id)


@pytest.mark.parametrize("payload", ["{}", "not json", "[]", '[{"status":"proposed","primary":[0],"secondary":[999],"score":1.0}]'])
def test_corrupted_group_proposal_is_rejected_without_repair(tmp_path: Path, payload: str) -> None:
    store, project, a, b = _prepare(tmp_path)
    proposal_id = store.record_group_proposals(project, a, b, direction=PageDirection.HORIZONTAL_LR)
    with sqlite3.connect(tmp_path / "project.sqlite3") as db:
        db.execute("UPDATE group_proposal_runs SET groups_json=? WHERE id=?", (payload, proposal_id))
    with pytest.raises(ValueError, match="(?i)(proposal|group)"):
        store.load_group_proposals(project, proposal_id)
    with sqlite3.connect(tmp_path / "project.sqlite3") as db:
        assert db.execute("SELECT groups_json FROM group_proposal_runs WHERE id=?", (proposal_id,)).fetchone()[0] == payload


def test_reordered_valid_indexes_or_changed_ocr_text_fail_closed(tmp_path: Path) -> None:
    store, project, a, b = _prepare(tmp_path)
    proposal_id = store.record_group_proposals(project, a, b, direction=PageDirection.HORIZONTAL_LR)
    with sqlite3.connect(tmp_path / "project.sqlite3") as db:
        original = db.execute("SELECT groups_json FROM group_proposal_runs WHERE id=?", (proposal_id,)).fetchone()[0]
        db.execute("UPDATE group_proposal_runs SET groups_json=? WHERE id=?", (original.replace("[0, 1]", "[1, 0]"), proposal_id))
    with pytest.raises(ValueError, match="(?i)group proposal"):
        store.load_group_proposals(project, proposal_id)
    with sqlite3.connect(tmp_path / "project.sqlite3") as db:
        db.execute("UPDATE group_proposal_runs SET groups_json=? WHERE id=?", (original, proposal_id))
        db.execute("UPDATE ocr_lines SET text='被替换' WHERE run_id=? AND line_index=0", (a,))
    with pytest.raises(ValueError, match="(?i)group proposal"):
        store.load_group_proposals(project, proposal_id)


def test_rejects_scoped_or_identical_runs_and_failed_computation_is_atomic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scribeforge.storage import sqlite as storage

    store, project, a, b = _prepare(tmp_path)
    scoped = store.record_ocr(project, PageOCRResult(0, "a", "1", ()), scope="pair:0")
    with pytest.raises(ValueError, match="whole-page"):
        store.record_group_proposals(project, a, scoped, direction=PageDirection.HORIZONTAL_LR)
    with pytest.raises(ValueError, match="distinct"):
        store.record_group_proposals(project, a, a, direction=PageDirection.HORIZONTAL_LR)
    def fail(*args, **kwargs):
        raise RuntimeError("computation failed")
    monkeypatch.setattr(storage, "propose_group_alignment", fail)
    with pytest.raises(RuntimeError, match="computation failed"):
        store.record_group_proposals(project, a, b, direction=PageDirection.HORIZONTAL_LR)
    with sqlite3.connect(tmp_path / "project.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM group_proposal_runs").fetchone()[0] == 0


def test_write_transaction_excludes_concurrent_ocr_mutation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scribeforge.storage import sqlite as storage

    store, project, a, b = _prepare(tmp_path)
    original = storage.propose_group_alignment
    locked: list[bool] = []
    def race(*args, **kwargs):
        from contextlib import closing
        with closing(sqlite3.connect(tmp_path / "project.sqlite3", timeout=0)) as other, \
                pytest.raises(sqlite3.OperationalError, match="locked"):
            other.execute("UPDATE ocr_lines SET text='mutated' WHERE run_id=?", (a,))
        locked.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(storage, "propose_group_alignment", race)
    proposal_id = store.record_group_proposals(project, a, b, direction=PageDirection.HORIZONTAL_LR)
    assert locked == [True]
    assert store.load_group_proposals(project, proposal_id).proposals.groups[0].secondary_indexes == (0, 1)
    assert store.load_ocr_run(a).lines[0].text == "春眠不觉晓"


def test_backup_restore_preserves_research_proposal_and_its_ocr_binding(tmp_path: Path) -> None:
    import hashlib

    from scribeforge.project_backup import create_project_backup, restore_project_backup

    root = tmp_path / "original.scribeforge"
    root.mkdir()
    source = tmp_path / "source.pdf"
    source.write_bytes(b"stable source")
    (root / "assets").mkdir()
    image = root / "assets" / "page.png"
    image.write_bytes(b"legacy page")
    store = SQLiteStore(root / "project.sqlite3")
    store.initialize()
    project = store.create_project("Book", str(source), hashlib.sha256(source.read_bytes()).hexdigest())
    store.add_page(project, 0, str(image), 100, 100)
    a = store.record_ocr(project, PageOCRResult(0, "a", "1", (OCRLine("甲乙", BoundingBox(0.1, 0.2, 0.4, 0.05)),)))
    b = store.record_ocr(project, PageOCRResult(0, "b", "1", (OCRLine("甲", BoundingBox(0.1, 0.2, 0.2, 0.05)),
                                                                   OCRLine("乙", BoundingBox(0.3, 0.2, 0.2, 0.05)))))
    proposal = store.record_group_proposals(project, a, b, direction=PageDirection.HORIZONTAL_LR)
    before = store.load_group_proposals(project, proposal)
    archive, target = tmp_path / "book.zip", tmp_path / "restored.scribeforge"
    create_project_backup(root, archive)
    restore_project_backup(archive, target)
    restored = SQLiteStore(target / "project.sqlite3")
    assert restored.load_group_proposals(project, proposal) == before
    assert restored.latest_decision(project, 0, 0) is None
