from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from feedback_eval import summarize
from validate_feedback_proposal import (
    ProposalError,
    main as validate_main,
    stage_candidate_batch,
    validate_proposal,
)


class FeedbackWorkflowTests(unittest.TestCase):
    def feedback_records(self, count=10):
        records = []
        start = date(2026, 10, 1)
        for index in range(count):
            records.append({
                "feedback_id": f"fb-{index}",
                "run_id": f"daily-2026-10-{index + 1:02d}",
                "task_date": (start + timedelta(days=index)).isoformat(),
                "reviewer_type": "editor",
                "ratings": {"factual_accuracy": 4, "structure_compliance": 3, "tone": 4, "readability": 3},
                "rework_required": index < 3,
                "issue_codes": ["SOURCE_REFERENCE_MISSING"] if index < 3 else [],
                "note_sanitized": "This note must not enter the summary.",
            })
        return records

    def test_summary_requires_sample_and_detects_repeated_issue(self):
        records = self.feedback_records()
        report = summarize(records, as_of=date(2026, 10, 10), window_days=28,
                           min_samples=10, min_issue_count=3, min_distinct_runs=3,
                           active_rules_version="2026.11.1")
        self.assertEqual(report["decision"], "review_required")
        self.assertEqual(report["repeated_issue_codes"][0]["code"], "SOURCE_REFERENCE_MISSING")
        self.assertEqual(len(report["repeated_issue_codes"][0]["evidence_run_ids"]), 3)
        self.assertNotIn("note_sanitized", json.dumps(report))
        self.assertFalse(report["governance"]["active_rules_modified"])

    def test_insufficient_samples_do_not_trigger_proposals(self):
        report = summarize(self.feedback_records(2), as_of=date(2026, 10, 2), min_samples=10)
        self.assertEqual(report["decision"], "insufficient_evidence")
        self.assertFalse(report["sample"]["sufficient"])

    def test_proposal_requires_exact_summary_hash_and_evidence(self):
        records = self.feedback_records()
        summary = summarize(records, as_of=date(2026, 10, 10), window_days=28,
                            active_rules_version="2026.11.1", evaluation_id="weekly-2026-W41")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            summary_path = root / "summary.json"
            summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            summary_hash = hashlib.sha256(summary_path.read_bytes()).hexdigest()
            run_ids = summary["repeated_issue_codes"][0]["evidence_run_ids"]
            proposal = {
                "proposal_version": "westcott-feedback-proposal-v1",
                "evaluation_id": "weekly-2026-W41",
                "source_summary_sha256": summary_hash,
                "active_rules_version": "2026.11.1",
                "active_rules_sha256": "0" * 64,
                "decision": "review_required",
                "findings": [{
                    "finding_id": "finding-source-missing",
                    "signal_type": "repeated_issue_code",
                    "signal_key": "SOURCE_REFERENCE_MISSING",
                    "observed_value": "3 across 3 runs",
                    "evidence_run_ids": run_ids,
                    "likely_cause": "Hypothesis only.",
                    "confidence": "medium",
                }],
                "proposals": [{
                    "candidate_id": "candidate-source-marker-001",
                    "target_rule_id": "SOURCE_TRACEABILITY",
                    "change_type": "clarify",
                    "before": "Existing rule excerpt.",
                    "proposed_after": "Every factual claim must carry a source marker.",
                    "rationale": "Repeated missing source references were observed.",
                    "expected_benefit": "Reduce missing source markers.",
                    "risks": ["May block claims whose source appears in a footnote."],
                    "scope": "Daily editorial content.",
                    "evidence_run_ids": run_ids,
                    "tests_required": [
                        {"test_id": "source-present", "input_summary": "A claim has a source marker.", "expected": "pass"},
                        {"test_id": "source-absent", "input_summary": "A claim lacks a source marker.", "expected": "block"},
                    ],
                    "rollback_to": "2026.11.1",
                }],
                "uncertainties": ["The feedback does not identify the exact paragraph."],
                "active_rules_modified": False,
            }
            proposal_path = root / "proposal.json"
            proposal_path.write_text(json.dumps(proposal, ensure_ascii=False), encoding="utf-8")
            _, loaded = validate_proposal(summary_path, proposal_path)
            self.assertEqual(loaded["decision"], "review_required")

            candidate_root = root / "rules" / "candidates"
            with redirect_stdout(StringIO()):
                exit_code = validate_main([
                    "--summary", str(summary_path), "--proposal", str(proposal_path),
                    "--candidate-root", str(candidate_root),
                ])
            staged = candidate_root / "candidate-source-marker-001" / "proposal.json"
            staged_json = json.loads(staged.read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 0)
            self.assertEqual(staged_json["approval_status"], "pending_human_review")
            self.assertFalse(staged_json["active_rules_modified"])
            self.assertFalse((root / "rules" / "active.json").exists())

            proposal["proposals"][0]["rationale"] = "Changed proposal must not replace the pending candidate."
            proposal_path.write_text(json.dumps(proposal, ensure_ascii=False), encoding="utf-8")
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                rejected = validate_main([
                    "--summary", str(summary_path), "--proposal", str(proposal_path),
                    "--candidate-root", str(candidate_root),
                ])
            self.assertEqual(rejected, 2)
            self.assertEqual(json.loads(staged.read_text(encoding="utf-8")), staged_json)

            proposal["source_summary_sha256"] = "0" * 64
            proposal_path.write_text(json.dumps(proposal), encoding="utf-8")
            with self.assertRaises(ProposalError):
                validate_proposal(summary_path, proposal_path)

    def test_candidate_does_not_accept_unknown_run_ids(self):
        records = self.feedback_records()
        summary = summarize(records, as_of=date(2026, 10, 10), active_rules_version="2026.11.1",
                            evaluation_id="eval-1")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            summary_path = root / "summary.json"
            summary_path.write_text(json.dumps(summary), encoding="utf-8")
            digest = hashlib.sha256(summary_path.read_bytes()).hexdigest()
            proposal = {
                "proposal_version": "westcott-feedback-proposal-v1", "evaluation_id": "eval-1",
                "source_summary_sha256": digest, "active_rules_version": "2026.11.1",
                "active_rules_sha256": "0" * 64,
                "decision": "review_required", "findings": [{
                    "finding_id": "finding-1", "signal_type": "repeated_issue_code",
                    "signal_key": "SOURCE_REFERENCE_MISSING", "observed_value": "3",
                    "evidence_run_ids": ["forged-run"], "likely_cause": "Maybe.", "confidence": "low",
                }], "proposals": [], "uncertainties": [], "active_rules_modified": False,
            }
            proposal_path = root / "proposal.json"
            proposal_path.write_text(json.dumps(proposal), encoding="utf-8")
            with self.assertRaises(ProposalError):
                validate_proposal(summary_path, proposal_path)

    def test_candidate_batch_rolls_back_if_commit_fails_midway(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "candidates"
            proposal = {
                "evaluation_id": "eval-atomic-batch",
                "source_summary_sha256": "a" * 64,
                "active_rules_version": "2026.11.1",
                "active_rules_sha256": "b" * 64,
                "proposals": [
                    {"candidate_id": "candidate-one"},
                    {"candidate_id": "candidate-two"},
                ],
            }
            real_link = os.link
            calls = 0

            def fail_on_second_link(source, destination):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("simulated commit failure")
                return real_link(source, destination)

            with patch("validate_feedback_proposal.os.link", side_effect=fail_on_second_link):
                with self.assertRaisesRegex(OSError, "simulated commit failure"):
                    stage_candidate_batch(root, proposal)

            self.assertEqual(list(root.glob("*/proposal.json")), [])


if __name__ == "__main__":
    unittest.main()
