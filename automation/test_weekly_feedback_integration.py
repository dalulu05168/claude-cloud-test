from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "automation" / "run-weekly-feedback.sh"


@unittest.skipUnless(shutil.which("jq") and shutil.which("flock") and shutil.which("sha256sum"), "weekly feedback integration needs jq, flock and sha256sum")
class WeeklyFeedbackIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state = self.root / "state"
        self.feedback = self.state / "feedback" / "inbox" / "feedback.jsonl"
        self.feedback.parent.mkdir(parents=True)
        self.active_rules = self.root / "active-rules.json"
        self.active_rules.write_text('{"active_version":"2026.11.1","rules":[]}\n', encoding="utf-8")
        self.candidate_root = self.root / "rules" / "candidates"
        self.evaluator_calls = self.root / "evaluator-calls.log"
        self.env_file = self.root / "feedback.env"
        self.evaluator = self.root / "fake-evaluator"
        self.evaluator.write_text(self.evaluator_script(), encoding="utf-8")
        self.evaluator.chmod(0o755)
        self.write_feedback()
        self.write_env(change=False)

    def tearDown(self):
        self.temp.cleanup()

    def write_feedback(self):
        rows = []
        base = date(2026, 11, 14)
        for index in range(10):
            rows.append({
                "feedback_id": f"fb-{index}", "run_id": f"daily-2026-11-{14 + index:02d}",
                "task_date": (base + timedelta(days=index)).isoformat(), "reviewer_type": "editor",
                "ratings": {"factual_accuracy": 4, "structure_compliance": 4, "tone": 4, "readability": 4},
                "rework_required": index < 3,
                "issue_codes": ["SOURCE_REFERENCE_MISSING"] if index < 3 else [],
            })
        self.feedback.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    def evaluator_script(self):
        return r'''#!/usr/bin/env python3
import argparse, hashlib, json, os
p=argparse.ArgumentParser()
p.add_argument('--evaluation-id', required=True)
p.add_argument('--summary', required=True)
p.add_argument('--system-prompt', required=True)
p.add_argument('--active-rules', required=True)
p.add_argument('--test-inventory', required=True)
a=p.parse_args()
with open(os.environ['FAKE_CALLS_FILE'],'a',encoding='utf-8') as calls: calls.write('called\n')
raw=open(a.summary,'rb').read(); summary=json.loads(raw)
signal=summary['repeated_issue_codes'][0]
run_ids=signal['evidence_run_ids']
proposal={
 'proposal_version':'westcott-feedback-proposal-v1',
 'evaluation_id':a.evaluation_id,
 'source_summary_sha256':hashlib.sha256(raw).hexdigest(),
 'active_rules_version':summary['active_rules_version'],
 'active_rules_sha256':summary['active_rules_sha256'],
 'decision':'review_required',
 'findings':[{'finding_id':'finding-source-missing','signal_type':'repeated_issue_code','signal_key':signal['code'],'observed_value':str(signal['feedback_count']),'evidence_run_ids':run_ids,'likely_cause':'A source-marker rule may need clarification.','confidence':'medium'}],
 'proposals':[{'candidate_id':'candidate-'+a.evaluation_id+'-source-marker','target_rule_id':'SOURCE_TRACEABILITY','change_type':'clarify','before':'Current rule excerpt.','proposed_after':'Require a source marker for each factual claim.','rationale':'Repeated missing source references were observed.' + (' revised' if os.getenv('FAKE_CHANGE') == '1' else ''),'expected_benefit':'Reduce missing source references.','risks':['May block claims with distant footnotes.'],'scope':'Daily editorial content.','evidence_run_ids':run_ids,'tests_required':[{'test_id':'marker-present','input_summary':'Claim has a source marker.','expected':'pass'},{'test_id':'marker-missing','input_summary':'Claim has no source marker.','expected':'block'}],'rollback_to':summary['active_rules_version']}],
 'uncertainties':['Feedback does not identify every affected paragraph.'],
 'active_rules_modified':False
}
print(json.dumps(proposal, ensure_ascii=False))
'''

    def write_env(self, *, change: bool):
        inventory = PROJECT / "docs" / "feedback-test-inventory.json"
        self.env_file.write_text("\n".join([
            "WESTCOTT_TIMEZONE=UTC",
            f"WESTCOTT_STATE_DIR={self.state}",
            f"WESTCOTT_FEEDBACK_JSONL={self.feedback}",
            f"WESTCOTT_FEEDBACK_EVALUATOR={self.evaluator}",
            f"WESTCOTT_ACTIVE_RULES_FILE={self.active_rules}",
            "WESTCOTT_ACTIVE_RULES_VERSION=2026.11.1",
            f"WESTCOTT_FEEDBACK_TEST_INVENTORY={inventory}",
            f"WESTCOTT_CANDIDATE_ROOT={self.candidate_root}",
            f"WESTCOTT_PYTHON={sys.executable}",
            "WESTCOTT_FEEDBACK_WINDOW_DAYS=28",
            "WESTCOTT_FEEDBACK_MIN_SAMPLES=10",
            "WESTCOTT_FEEDBACK_MIN_ISSUE_COUNT=3",
            "WESTCOTT_FEEDBACK_MIN_DISTINCT_RUNS=3",
            f"FAKE_CHANGE={1 if change else 0}",
            f"FAKE_CALLS_FILE={self.evaluator_calls}",
            "",
        ]), encoding="utf-8")
        self.env_file.chmod(0o600)

    def run_workflow(self):
        env = os.environ.copy()
        env["WESTCOTT_FEEDBACK_ENV_FILE"] = str(self.env_file)
        return subprocess.run(["bash", str(SCRIPT), "2026-11-23"], env=env,
                              capture_output=True, text=True, timeout=30)

    def test_review_required_stages_candidate_without_modifying_active_rules(self):
        before = self.active_rules.read_bytes()
        first = self.run_workflow()
        self.assertEqual(first.returncode, 0, first.stderr)
        summary_files = list((self.state / "feedback" / "evaluations").glob("*-summary.json"))
        proposal_files = list((self.state / "feedback" / "evaluations").glob("*-proposal.json"))
        staged_files = list(self.candidate_root.glob("*/proposal.json"))
        self.assertEqual(len(summary_files), 1)
        self.assertEqual(len(proposal_files), 1)
        self.assertEqual(len(staged_files), 1)
        self.assertEqual(json.loads(summary_files[0].read_text())["decision"], "review_required")
        self.assertEqual(json.loads(staged_files[0].read_text())["approval_status"], "pending_human_review")
        self.assertEqual(self.active_rules.read_bytes(), before)
        self.assertEqual(self.evaluator_calls.read_text().splitlines(), ["called"])

        second = self.run_workflow()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("evaluator was not invoked again", second.stdout)
        self.assertEqual(self.active_rules.read_bytes(), before)
        self.assertEqual(len(list(self.candidate_root.glob("*/proposal.json"))), 1)
        self.assertEqual(self.evaluator_calls.read_text().splitlines(), ["called"])

        self.write_env(change=True)
        third = self.run_workflow()
        self.assertEqual(third.returncode, 0, third.stderr)
        self.assertEqual(self.active_rules.read_bytes(), before)
        self.assertEqual(len(list(self.candidate_root.glob("*/proposal.json"))), 1)
        self.assertEqual(self.evaluator_calls.read_text().splitlines(), ["called"])


if __name__ == "__main__":
    unittest.main()
