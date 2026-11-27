from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import sync_google_doc


class FakeRequest:
    def __init__(self, response):
        self.response = response

    def execute(self):
        return self.response


class FakeFiles:
    def __init__(self, existing=None):
        self.existing = list(existing or [])
        self.created = []
        self.last_query = None

    def list(self, **kwargs):
        self.last_query = kwargs["q"]
        return FakeRequest({"files": self.existing})

    def create(self, body, media_body, **kwargs):
        self.created.append(body)
        return FakeRequest({
            "id": "fake-doc-123", "name": body["name"],
            "mimeType": sync_google_doc.GOOGLE_DOC_MIME,
            "webViewLink": "https://docs.google.com/document/d/fake-doc-123/edit",
            "appProperties": body["appProperties"], "parents": body["parents"],
        })


class FakeDrive:
    def __init__(self, existing=None):
        self.file_api = FakeFiles(existing)

    def files(self):
        return self.file_api


class GoogleDocsPublisherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        run = root / "run"
        run.mkdir()
        self.draft = run / "draft.md"
        self.draft.write_text(
            "# 今日内容\n\n中文正文含 Emoji 😀。\n\n- **重点**\n- *斜体*\n\n```text\ncode sample\n```\n",
            encoding="utf-8",
        )
        self.research = run / "research.json"
        self.research.write_text('{"schema_version":"westcott-daily-research-v1","stories":[]}\n', encoding="utf-8")
        self.manifest = run / "manifest.json"
        self.manifest.write_text(json.dumps({
            "run_id": "daily-2026-11-23", "task_date": "2026-11-23",
            "status": "ready_for_docs", "ruleset_version": "2026.11.1",
            "validation": {"status": "passed"},
            "artifacts": {
                "draft": {
                    "path": "draft.md", "sha256": hashlib.sha256(self.draft.read_bytes()).hexdigest(),
                    "size_bytes": self.draft.stat().st_size,
                },
                "research": {
                    "path": "research.json", "sha256": hashlib.sha256(self.research.read_bytes()).hexdigest(),
                    "size_bytes": self.research.stat().st_size,
                },
            },
        }), encoding="utf-8")
        self.args = SimpleNamespace(
            input=str(self.draft), manifest=str(self.manifest), run_id="daily-2026-11-23",
            date="2026-11-23", title="Westcott Daily Rhythm 2026-11-23", folder_id="folder-123",
            approval=str(root / "approval.json"),
        )
        self.approval = Path(self.args.approval)
        self.approval.write_text(json.dumps({
            "action": "publish_google_doc",
            "decision": "approved",
            "run_id": self.args.run_id,
            "task_date": self.args.date,
            "ruleset_version": "2026.11.1",
            "folder_id": self.args.folder_id,
            "draft_sha256": hashlib.sha256(self.draft.read_bytes()).hexdigest(),
            "research_sha256": hashlib.sha256(self.research.read_bytes()).hexdigest(),
            "approved_by": "test-operator",
            "approved_at": "2026-11-23T15:30:00+02:00",
        }), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_docx_renderer_keeps_unicode_and_formats_paragraphs(self):
        path = Path(self.temp.name) / "rendered.docx"
        sync_google_doc.build_docx(self.draft.read_text(encoding="utf-8"), self.args.title, path)
        self.assertTrue(path.is_file())
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml").decode("utf-8")
            styles = archive.read("word/styles.xml").decode("utf-8")
        self.assertIn("中文正文含 Emoji 😀", xml)
        self.assertIn("今日内容", xml)
        self.assertIn("ListBullet", styles)
        self.assertIn("<w:b", xml)

    def test_publish_creates_converted_doc_with_run_id_properties(self):
        drive = FakeDrive()
        with patch.object(sync_google_doc, "_build_drive_service", return_value=drive):
            result = sync_google_doc.publish(self.args)
        self.assertEqual(result["status"], "saved")
        self.assertEqual(result["document_id"], "fake-doc-123")
        self.assertFalse(result["idempotent"])
        self.assertEqual(len(drive.file_api.created), 1)
        metadata = drive.file_api.created[0]
        self.assertEqual(metadata["mimeType"], sync_google_doc.GOOGLE_DOC_MIME)
        self.assertEqual(metadata["parents"], ["folder-123"])
        self.assertEqual(metadata["appProperties"]["westcott_run_id"], self.args.run_id)
        self.assertIn("appProperties has", drive.file_api.last_query)

    def test_same_run_and_same_hash_is_idempotent_noop(self):
        digest = __import__("hashlib").sha256(self.draft.read_bytes()).hexdigest()
        existing = [{
            "id": "existing-doc", "name": self.args.title,
            "mimeType": sync_google_doc.GOOGLE_DOC_MIME,
            "webViewLink": "https://docs.google.com/document/d/existing-doc/edit",
            "appProperties": {"westcott_content_sha256": digest},
        }]
        drive = FakeDrive(existing)
        with patch.object(sync_google_doc, "_build_drive_service", return_value=drive):
            result = sync_google_doc.publish(self.args)
        self.assertTrue(result["idempotent"])
        self.assertEqual(result["document_id"], "existing-doc")
        self.assertEqual(drive.file_api.created, [])

    def test_different_hash_fails_closed_without_overwrite(self):
        existing = [{
            "id": "existing-doc", "appProperties": {"westcott_content_sha256": "old-hash"},
        }]
        drive = FakeDrive(existing)
        with patch.object(sync_google_doc, "_build_drive_service", return_value=drive):
            with self.assertRaisesRegex(RuntimeError, "refusing to overwrite"):
                sync_google_doc.publish(self.args)
        self.assertEqual(drive.file_api.created, [])

    def test_manifest_without_passed_validation_is_rejected(self):
        self.manifest.write_text(json.dumps({
            "run_id": self.args.run_id, "task_date": self.args.date,
            "status": "ready_for_docs", "validation": {"status": "blocked"},
        }), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "validation status"):
            sync_google_doc._load_and_check(self.args)

    def test_manifest_without_artifact_hashes_is_rejected(self):
        self.manifest.write_text(json.dumps({
            "run_id": self.args.run_id, "task_date": self.args.date,
            "status": "ready_for_docs", "validation": {"status": "passed"},
        }), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "artifact integrity records"):
            sync_google_doc._load_and_check(self.args)

    def test_modified_draft_is_rejected_before_docs_upload(self):
        self.draft.write_text(self.draft.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "draft differs from its manifest"):
            sync_google_doc._load_and_check(self.args)

    def test_modified_research_record_is_rejected_before_docs_upload(self):
        self.research.write_text('{"stories":[{"fact":"tampered"}]}\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "research record differs from its manifest"):
            sync_google_doc._load_and_check(self.args)

    def test_missing_approval_stops_before_drive_access(self):
        self.approval.unlink()
        with patch.object(sync_google_doc, "_build_drive_service") as build_drive:
            with self.assertRaises(FileNotFoundError):
                sync_google_doc.publish(self.args)
        build_drive.assert_not_called()

    def test_approval_in_run_directory_stops_before_drive_access(self):
        self.args.approval = str(self.approval.rename(self.draft.parent / "approval.json"))
        with patch.object(sync_google_doc, "_build_drive_service") as build_drive:
            with self.assertRaisesRegex(ValueError, "outside the draft run directory"):
                sync_google_doc.publish(self.args)
        build_drive.assert_not_called()

    def test_old_approval_rejects_revised_draft_even_with_updated_manifest(self):
        self.draft.write_bytes(self.draft.read_bytes() + b"\nRevised draft.\n")
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        manifest["artifacts"]["draft"].update(
            sha256=hashlib.sha256(self.draft.read_bytes()).hexdigest(),
            size_bytes=self.draft.stat().st_size,
        )
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        with patch.object(sync_google_doc, "_build_drive_service") as build_drive:
            with self.assertRaisesRegex(ValueError, "human approval does not match"):
                sync_google_doc.publish(self.args)
        build_drive.assert_not_called()

    def test_approval_does_not_authorize_a_different_destination(self):
        self.args.folder_id = "other-folder"
        with patch.object(sync_google_doc, "_build_drive_service") as build_drive:
            with self.assertRaisesRegex(ValueError, "human approval does not match"):
                sync_google_doc.publish(self.args)
        build_drive.assert_not_called()


if __name__ == "__main__":
    unittest.main()
