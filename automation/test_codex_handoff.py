from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import codex_handoff as handoff


READY = {"status": "ready_for_human_review", "summary": "证据一致，可以提交人审", "issues": []}
BLOCKED = {"status": "needs_evidence", "summary": "数字未获证实", "issues": ["28亿缺少依据"]}


def write_run(folder: Path, *, unsupported: bool = False) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    task_date = "2026-11-27"
    source = {
        "title": "合成样本｜某机构发布演练公报",
        "source_url": "https://example.com/drill",
        "image_url": "https://example.com/drill-picture.png",
        "published_at": f"{task_date}T09:00:00+02:00",
        "verified_facts": [
            "演练公报：某机构在2026-11-27发布演练通告。附图为同一个演练通告的官方示意图。",
        ],
    }
    draft = (
        "【合成测试样本】某机构于2026-11-27发布演练通告。\n"
        + ("公报宣布筹资28亿欧元。\n" if unsupported else "")
        + "配图：https://example.com/drill-picture.png\n"
    ).encode("utf-8")
    research = json.dumps({
        "schema_version": "westcott-daily-research-v1", "task_date": task_date, "stories": [source],
    }, ensure_ascii=False).encode("utf-8")
    manifest = {
        "task_date": task_date, "run_id": f"daily-{task_date}",
        "status": "drafted", "ruleset_version": "synthetic-test",
        "artifacts": {},
    }
    for key, name, data in (("draft", "draft.md", draft), ("research", "research.json", research)):
        (folder / name).write_bytes(data)
        manifest["artifacts"][key] = {
            "path": name, "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
        }
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


class CodexHandoffTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        write_run(self.folder)

    def test_receipt_tied_to_both_artifacts_and_cached_after_success(self):
        with patch.object(handoff, "ask_codex", return_value=READY) as review:
            first = handoff.review_run(self.folder)
            self.assertEqual(first, handoff.review_run(self.folder))
            review.assert_called_once()
            write_run(self.folder, unsupported=True)
            review.return_value = BLOCKED
            second = handoff.review_run(self.folder)
        self.assertEqual(second["review"], BLOCKED)
        self.assertNotEqual(first["draft_sha256"], second["draft_sha256"])
        self.assertEqual(first["research_sha256"], second["research_sha256"])

    def test_changed_research_cannot_reuse_passing_receipt(self):
        with patch.object(handoff, "ask_codex", return_value=READY):
            handoff.review_run(self.folder)
        with (self.folder / "research.json").open("ab") as stream:
            stream.write(b" ")
        with patch.object(handoff, "ask_codex") as review:
            with self.assertRaisesRegex(handoff.HandoffError, "research differs"):
                handoff.review_run(self.folder)
            review.assert_not_called()

    def test_model_cannot_store_result_if_draft_changes_during_review(self):
        def update_while_reviewing(_packet):
            write_run(self.folder, unsupported=True)
            return READY

        with patch.object(handoff, "ask_codex", side_effect=update_while_reviewing):
            with self.assertRaisesRegex(handoff.HandoffError, "changed while reviewing|changed while Codex"):
                handoff.review_run(self.folder)
        self.assertFalse((self.folder / handoff.REPORT_NAME).exists())

    def test_model_response_needs_findings_when_blocked(self):
        cases = [{**BLOCKED, "issues": []}, {**READY, "issues": ["证据矛盾"]}, {**READY, "approved": True}]
        for response in cases:
            with self.subTest(response=response):
                with self.assertRaises(handoff.HandoffError):
                    handoff.parse_review(response)


if __name__ == "__main__":
    unittest.main()
