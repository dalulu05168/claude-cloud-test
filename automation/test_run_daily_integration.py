from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SOURCE_SCRIPT = Path(__file__).resolve().parent / "run-daily.sh"


@unittest.skipUnless(shutil.which("jq") and shutil.which("flock"), "run-daily integration needs jq and flock")
class RunDailyIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project"
        automation = self.root / "automation"
        automation.mkdir(parents=True)
        self.script = automation / "run-daily.sh"
        shutil.copy2(SOURCE_SCRIPT, self.script)
        self.script.chmod(0o755)
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        self.state = self.root / ".westcott-state"
        self.runner_count = self.root / "runner-count"
        self.publisher_count = self.root / "publisher-count"
        self.runner = self.bin_dir / "fake-runner"
        self.publisher = self.bin_dir / "fake-publisher"
        self.runner.write_text(self.runner_script(), encoding="utf-8")
        self.publisher.write_text(self.publisher_script(), encoding="utf-8")
        self.runner.chmod(0o755)
        self.publisher.chmod(0o755)
        self.env_file = self.root / "daily.env"
        self.write_env(fail_first=False)

    def tearDown(self):
        self.temp.cleanup()

    def runner_script(self) -> str:
        return r'''#!/usr/bin/env bash
set -euo pipefail
while (($#)); do
  case "$1" in
    --date) run_date="$2"; shift 2 ;;
    --run-id) run_id="$2"; shift 2 ;;
    --draft) draft="$2"; shift 2 ;;
    --manifest) manifest="$2"; shift 2 ;;
    *) shift ;;
  esac
done
printf 'called\n' >> "$FAKE_RUNNER_COUNTER"
printf '# Validated draft\n\n今天的内容。\n' > "$draft"
if [[ "${FAKE_DRAFT_ONLY:-0}" == "1" ]]; then
  printf '{"run_id":"%s","task_date":"%s","status":"drafted","validation":{"status":"pending_review"}}\n' "$run_id" "$run_date" > "$manifest"
  exit 0
fi
printf '{"run_id":"%s","task_date":"%s","status":"ready_for_docs","validation":{"status":"passed"}}\n' "$run_id" "$run_date" > "$manifest"
'''

    def publisher_script(self) -> str:
        return r'''#!/usr/bin/env bash
set -euo pipefail
run_id=""
while (($#)); do
  case "$1" in
    --run-id) run_id="$2"; shift 2 ;;
    *) shift ;;
  esac
done
count=0
[[ -f "$FAKE_PUBLISHER_COUNTER" ]] && count="$(wc -l < "$FAKE_PUBLISHER_COUNTER")"
count=$((count + 1))
printf 'called\n' >> "$FAKE_PUBLISHER_COUNTER"
if [[ "${FAKE_FAIL_FIRST:-0}" == "1" && "$count" == "1" ]]; then
  echo 'simulated Drive outage' >&2
  exit 75
fi
jq -cn --arg run_id "$run_id" '{run_id:$run_id,status:"saved",document_id:"fake-doc-id",url:"https://docs.google.com/document/d/fake-doc-id/edit"}'
'''

    def write_env(self, *, fail_first: bool, draft_only: bool = False):
        lines = [
            "WESTCOTT_TIMEZONE=UTC",
            f"WESTCOTT_STATE_DIR={self.state}",
            f"WESTCOTT_LOG_DIR={self.state}/logs",
            "WESTCOTT_ORCHESTRATOR_AGENT=westcott-orchestrator",
            f"WESTCOTT_AGENT_RUNNER={self.runner}",
        ]
        if not draft_only:
            lines.extend([
                f"WESTCOTT_DOCS_PUBLISHER={self.publisher}",
                "WESTCOTT_GOOGLE_DRIVE_FOLDER_ID=folder-test-123",
                'WESTCOTT_DOC_TITLE_PREFIX="Westcott Test"',
            ])
        lines.extend([
            f"FAKE_RUNNER_COUNTER={self.runner_count}",
            f"FAKE_PUBLISHER_COUNTER={self.publisher_count}",
            f"FAKE_FAIL_FIRST={1 if fail_first else 0}",
            f"FAKE_DRAFT_ONLY={1 if draft_only else 0}",
            "",
        ])
        self.env_file.write_text(
            "\n".join(lines),
            encoding="utf-8",
        )
        self.env_file.chmod(0o600)

    def run_daily(self, run_date="2030-04-05"):
        environment = os.environ.copy()
        environment["WESTCOTT_ENV_FILE"] = str(self.env_file)
        return subprocess.run(
            ["bash", str(self.script), run_date], env=environment,
            text=True, capture_output=True, timeout=20,
        )

    @staticmethod
    def count_calls(path: Path) -> int:
        return len(path.read_text(encoding="utf-8").splitlines()) if path.exists() else 0

    def test_successful_delivery_is_idempotent_on_rerun(self):
        first = self.run_daily()
        second = self.run_daily()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.count_calls(self.runner_count), 1)
        self.assertEqual(self.count_calls(self.publisher_count), 1)
        result_path = self.state / "runs" / "daily-2030-04-05" / "google-docs.json"
        result = json.loads(result_path.read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "saved")

    def test_docs_failure_retry_does_not_rerun_agent(self):
        self.write_env(fail_first=True)
        first = self.run_daily()
        self.assertNotEqual(first.returncode, 0)
        result_path = self.state / "runs" / "daily-2030-04-05" / "google-docs.json"
        self.assertFalse(result_path.exists())
        second = self.run_daily()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.count_calls(self.runner_count), 1)
        self.assertEqual(self.count_calls(self.publisher_count), 2)
        result = json.loads(result_path.read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "saved")

    def test_pending_review_saves_draft_without_publishing(self):
        self.write_env(fail_first=False, draft_only=True)
        first = self.run_daily()
        second = self.run_daily()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        run_dir = self.state / "runs" / "daily-2030-04-05"
        self.assertTrue((run_dir / "draft.md").is_file())
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "drafted")
        self.assertEqual(manifest["validation"]["status"], "pending_review")
        self.assertEqual(self.count_calls(self.runner_count), 1)
        self.assertEqual(self.count_calls(self.publisher_count), 0)

    def test_explicit_auto_publish_reenters_pending_run(self):
        self.write_env(fail_first=False, draft_only=True)
        first = self.run_daily()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.write_env(fail_first=False, draft_only=False)
        with self.env_file.open("a", encoding="utf-8") as stream:
            stream.write("WESTCOTT_AUTO_PUBLISH=true\n")
        promoted = self.run_daily()
        self.assertEqual(promoted.returncode, 0, promoted.stderr)
        self.assertEqual(self.count_calls(self.runner_count), 2)
        self.assertEqual(self.count_calls(self.publisher_count), 1)


if __name__ == "__main__":
    unittest.main()
