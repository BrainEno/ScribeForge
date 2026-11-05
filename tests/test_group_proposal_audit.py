from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile
from contextlib import closing
from pathlib import Path

import pytest

from scribeforge.domain.ocr import BoundingBox, OCRLine, PageDirection, PageOCRResult
from scribeforge.project_audit import audit_project_store
from scribeforge.project_backup import (
    ProjectBackupError,
    create_project_backup,
    restore_project_backup,
    verify_project_backup,
)
from scribeforge.storage.sqlite import SQLiteStore


def _store(tmp_path: Path) -> tuple[Path, SQLiteStore, int, int, int]:
    root = tmp_path / 'book.scribeforge'
    assets = root / 'assets'
    assets.mkdir(parents=True)
    page = assets / 'page.png'
    page.write_bytes(b'page')
    source = tmp_path / 'source.pdf'
    source.write_bytes(b'source')
    store = SQLiteStore(root / 'project.sqlite3')
    store.initialize()
    project = store.create_project('book', str(source), hashlib.sha256(source.read_bytes()).hexdigest())
    store.add_page(project, 0, str(page), 100, 100)
    lines = (OCRLine('甲', BoundingBox(.1, .1, .2, .05)), OCRLine('乙', BoundingBox(.3, .1, .2, .05)))
    a = store.record_ocr(project, PageOCRResult(0, 'a', '1', lines))
    b = store.record_ocr(project, PageOCRResult(0, 'b', '1', lines))
    proposal = store.record_group_proposals(project, a, b, direction=PageDirection.HORIZONTAL_LR)
    return root, store, project, a, proposal


def test_audit_detects_corrupt_proposal_and_backup_refuses_without_repair(tmp_path: Path) -> None:
    root, store, project, _, proposal = _store(tmp_path)
    assert not any(f.code.startswith('group_proposal') for f in audit_project_store(root).findings)
    with closing(sqlite3.connect(root / 'project.sqlite3')) as db:
        db.execute("UPDATE group_proposal_runs SET groups_json='[]' WHERE id=?", (proposal,))
        db.commit()
    findings = audit_project_store(root).findings
    finding = next(f for f in findings if f.code == 'group_proposal_invalid')
    assert finding.severity == 'error' and str(proposal) in finding.message
    with pytest.raises(ProjectBackupError, match='group_proposal_invalid'):
        create_project_backup(root, tmp_path / 'bad.zip')
    assert not (tmp_path / 'bad.zip').exists()
    with pytest.raises(ValueError):
        store.load_group_proposals(project, proposal)
    with closing(sqlite3.connect(root / 'project.sqlite3')) as db:
        assert db.execute('SELECT groups_json FROM group_proposal_runs WHERE id=?', (proposal,)).fetchone()[0] == '[]'


def test_source_line_index_corruption_cannot_silently_rebind_indexes(tmp_path: Path) -> None:
    root, store, project, a, proposal = _store(tmp_path)
    with closing(sqlite3.connect(root / 'project.sqlite3')) as db:
        db.execute('UPDATE ocr_lines SET line_index=7 WHERE run_id=? AND line_index=1', (a,))
        db.commit()
    with pytest.raises(ValueError, match='(?i)(group|index)'):
        store.load_group_proposals(project, proposal)
    assert any(f.code == 'group_proposal_invalid' for f in audit_project_store(root).findings)


def test_audit_distinguishes_v14_from_missing_v15_ledger(tmp_path: Path) -> None:
    root, _, _, _, _ = _store(tmp_path)
    with closing(sqlite3.connect(root / 'project.sqlite3')) as db:
        db.execute('DROP TABLE group_proposal_runs')
        db.commit()
    assert any(f.code == 'group_proposal_schema_missing' for f in audit_project_store(root).findings)
    with closing(sqlite3.connect(root / 'project.sqlite3')) as db:
        db.execute('PRAGMA user_version=14')
        db.commit()
    assert not any(f.code.startswith('group_proposal') for f in audit_project_store(root).findings)


def test_backup_verify_and_restore_reject_semantically_corrupt_database(tmp_path: Path) -> None:
    root, _, _, _, proposal = _store(tmp_path)
    good = tmp_path / 'good.zip'
    create_project_backup(root, good)
    with zipfile.ZipFile(good) as z:
        members = {name: z.read(name) for name in z.namelist()}
    extracted = tmp_path / 'edited.sqlite3'
    extracted.write_bytes(members['project.sqlite3'])
    with closing(sqlite3.connect(extracted)) as db:
        db.execute("UPDATE group_proposal_runs SET groups_json='[]' WHERE id=?", (proposal,))
        db.commit()
    members['project.sqlite3'] = extracted.read_bytes()
    manifest = json.loads(members['manifest.json'])
    record = next(item for item in manifest['files'] if item['path'] == 'project.sqlite3')
    record['size'] = len(members['project.sqlite3'])
    record['sha256'] = hashlib.sha256(members['project.sqlite3']).hexdigest()
    members['manifest.json'] = json.dumps(manifest).encode()
    bad = tmp_path / 'bad.zip'
    with zipfile.ZipFile(bad, 'w') as z:
        for name, content in members.items():
            z.writestr(name, content)
    with pytest.raises(ProjectBackupError, match='(?i)(audit|integrity|group)'):
        verify_project_backup(bad)
    target = tmp_path / 'restore'
    with pytest.raises(ProjectBackupError, match='(?i)(audit|integrity|group)'):
        restore_project_backup(bad, target)
    assert not target.exists()


def test_audit_uses_one_snapshot_when_ocr_changes_concurrently(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _, _, run_id, proposal = _store(tmp_path)
    database = root / 'project.sqlite3'
    with closing(sqlite3.connect(database)) as db:
        assert db.execute('PRAGMA journal_mode=WAL').fetchone()[0].lower() == 'wal'
    original = SQLiteStore._load_ocr_run
    changed = []
    def race(connection, current):
        if not changed:
            with closing(sqlite3.connect(database)) as writer:
                writer.execute("UPDATE ocr_lines SET text='concurrent' WHERE run_id=? AND line_index=0", (run_id,))
                writer.commit()
            changed.append(True)
        return original(connection, current)
    monkeypatch.setattr(SQLiteStore, '_load_ocr_run', staticmethod(race))
    report = audit_project_store(root)
    assert changed == [True]
    assert not any(f.code == 'group_proposal_invalid' for f in report.findings)
    assert any(f.code == 'group_proposal_invalid' and str(proposal) in f.message
               for f in audit_project_store(root).findings)


@pytest.mark.parametrize('payload', [
    '[]',
    '[{"primary":[0,0],"secondary":[0],"status":"proposed","score":0.8}]',
    '[{"primary":[0],"secondary":[4],"status":"proposed","score":0.8}]',
    '[{"primary":[0],"secondary":[0],"status":"proposed","score":true}]',
])
def test_rehashed_malformed_groups_still_fail_structure_validation(tmp_path: Path, payload: str) -> None:
    from scribeforge.storage.sqlite import _group_evidence_digest

    root, store, project, _, proposal = _store(tmp_path)
    with closing(sqlite3.connect(root / 'project.sqlite3')) as db:
        db.row_factory = sqlite3.Row
        row = db.execute('SELECT * FROM group_proposal_runs WHERE id=?', (proposal,)).fetchone()
        first, second = row['primary_run_id'], row['secondary_run_id']
        digest = _group_evidence_digest(project, row['page_id'], first, second, row['direction'],
                                        row['algorithm_version'], payload,
                                        store.load_ocr_run(first), store.load_ocr_run(second))
        db.execute('UPDATE group_proposal_runs SET groups_json=?, evidence_sha256=? WHERE id=?',
                   (payload, digest, proposal))
        db.commit()
    with pytest.raises(ValueError, match='(?i)group proposal'):
        store.load_group_proposals(project, proposal)
    assert any(f.code == 'group_proposal_invalid' for f in audit_project_store(root).findings)
