from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import claude_code_runner as runner


class ClaudeCodeRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state = self.root / "state"
        self.run_id = "daily-2030-04-05"
        self.run_dir = self.state / "runs" / self.run_id
        self.run_dir.mkdir(parents=True)
        self.brief_path = self.root / "daily-brief.json"
        self.brief_path.write_text(json.dumps({
            "task_date": "2030-04-05",
            "market_scope": "美国市场",
            "news_count": 1,
            "topic_instructions": ["央行政策与通胀数据"],
            "source_allowlist": ["example.com"],
        }), encoding="utf-8")
        self.fake_bin = self.root / "bin"
        self.fake_bin.mkdir()
        self.args_record = self.root / "claude-args.json"
        self.claude = self.fake_bin / "claude-fake"
        self._write_fake_claude(self.output())
        frozen_now = datetime.fromisoformat("2030-04-05T08:00:00+08:00")
        self.now_patch = patch.object(runner, "_now_in_timezone", return_value=frozen_now)
        self.now_patch.start()
        self.addCleanup(self.now_patch.stop)
        self.args = Namespace(
            agent="westcott-orchestrator",
            task="daily-rhythm",
            date="2030-04-05",
            timezone="Asia/Shanghai",
            run_id=self.run_id,
            run_dir=str(self.run_dir),
            draft=str(self.run_dir / "draft.md"),
            manifest=str(self.run_dir / "manifest.json"),
        )
        self.env = {
            "PATH": f"{self.fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "WESTCOTT_RULESET_VERSION": "rules-v1",
            "WESTCOTT_STATE_DIR": str(self.state),
            "WESTCOTT_DAILY_BRIEF_FILE": str(self.brief_path),
            "WESTCOTT_CLAUDE_BIN": str(self.claude),
            "WESTCOTT_CLAUDE_TIMEOUT_SECONDS": "20",
            "WESTCOTT_STOP_GATE_TIMEOUT_SECONDS": "5",
            "WESTCOTT_AUTO_PUBLISH": "false",
            "FAKE_CLAUDE_ARGS_PATH": str(self.args_record),
            "GOOGLE_APPLICATION_CREDENTIALS": "/secret/adc.json",
        }
        if os.name == "nt":
            self.env["SystemRoot"] = os.environ["SystemRoot"]
            # Windows cannot execute the fixture scripts via POSIX shebangs.
            # Keep the real child process to exercise stdin, timeouts and exit codes.
            popen = subprocess.Popen

            def launch_fixture(args, **kwargs):
                if Path(args[0]).parent == self.fake_bin:
                    args = [sys.executable, "-B", "-X", "utf8", *args]
                return popen(args, **kwargs)

            fixture_patch = patch.object(subprocess, "Popen", side_effect=launch_fixture)
            fixture_patch.start()
            self.addCleanup(fixture_patch.stop)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def output(source_url="https://www.example.com/news/1",
               published_at="2030-04-05T07:30:00+08:00") -> str:
        draft = (
            "===== 资讯助理 =====\n\n"
            "🇺🇸 美国财经新闻｜央行公布政策决定\n\n"
            "央行公布了政策决定，并说明后续安排。\n\n"
            "市场后续表现仍取决于通胀与就业数据。\n\n"
            "配图：https://cdn.example.com/image.jpg\n"
        )
        return json.dumps({
            "draft_markdown": draft,
            "stories": [{
                "country": "美国",
                "title": "央行公布政策决定",
                "source_url": source_url,
                "source_title": "中央银行公告",
                "published_at": published_at,
                "image_url": "https://cdn.example.com/image.jpg",
                "verified_facts": ["政策决定已发布"],
            }],
        }, ensure_ascii=False)

    def _write_fake_claude(self, output: str, *, model_exit: int = 0, auth_exit: int = 0,
                           version: str = "2.1.300", sleep_seconds: int = 0):
        script = f"""#!/usr/bin/env python3
import json, os, sys, time
args = sys.argv[1:]
if args == ["--version"]:
    print({version!r} + " (Claude Code)")
elif args == ["auth", "status"]:
    raise SystemExit({auth_exit})
else:
    with open(os.environ["FAKE_CLAUDE_ARGS_PATH"], "w", encoding="utf-8") as f:
        json.dump({{"args": args, "has_google_credentials": "GOOGLE_APPLICATION_CREDENTIALS" in os.environ}}, f)
    sys.stdin.read()
    time.sleep({sleep_seconds})
    sys.stdout.write({output!r})
    raise SystemExit({model_exit})
"""
        self.claude.write_text(script, encoding="utf-8")
        self.claude.chmod(0o755)

    def run_execute(self):
        with patch.dict(os.environ, self.env, clear=True), \
             patch.object(runner, "_run_stop_gate", return_value=(True, "")):
            return runner.execute(self.args)

    def test_success_is_saved_as_pending_review_and_claude_has_read_only_tools(self):
        result = self.run_execute()
        self.assertEqual(result, 0)
        draft = self.run_dir / "draft.md"
        manifest_path = self.run_dir / "manifest.json"
        research_path = self.run_dir / "research.json"
        self.assertTrue(draft.is_file())
        self.assertTrue(research_path.is_file())
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "drafted")
        self.assertEqual(manifest["validation"]["status"], "pending_review")
        self.assertEqual(manifest["google_docs"]["status"], "not_started")
        if os.name != "nt":
            self.assertEqual(draft.stat().st_mode & 0o777, 0o600)

        invocation = json.loads(self.args_record.read_text(encoding="utf-8"))
        args = invocation["args"]
        self.assertIn("--bare", args)
        self.assertIn("--strict-mcp-config", args)
        self.assertIn("--permission-mode", args)
        self.assertIn("plan", args)
        self.assertIn("--permission-prompts", args)
        self.assertIn("none", args)
        tools_index = args.index("--tools")
        self.assertEqual(args[tools_index + 1], "WebSearch,WebFetch")
        allowed_index = args.index("--allowedTools")
        self.assertEqual(args[allowed_index + 1:allowed_index + 3], ["WebSearch", "WebFetch"])
        self.assertNotIn("Bash", args)
        self.assertFalse(invocation["has_google_credentials"])

    def test_source_outside_allowlist_is_rejected_before_writing(self):
        self._write_fake_claude(self.output("https://unapproved.invalid/story"))
        with patch.dict(os.environ, self.env, clear=True), \
             patch.object(runner, "_run_stop_gate", return_value=(True, "")):
            with self.assertRaises(runner.RunnerError):
                runner.execute(self.args)
        self.assertFalse((self.run_dir / "draft.md").exists())
        self.assertFalse((self.run_dir / "manifest.json").exists())

    def test_stop_gate_block_preserves_reviewable_draft_but_blocks_publishing(self):
        with patch.dict(os.environ, self.env, clear=True), \
             patch.object(runner, "_run_stop_gate", return_value=(False, "blocked")):
            result = runner.execute(self.args)
        self.assertEqual(result, 0)
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "drafted")
        self.assertEqual(manifest["validation"]["status"], "blocked")
        self.assertEqual(manifest["validation"]["structural_gate"], "blocked")
        self.assertTrue((self.run_dir / "draft.md").is_file())

    def test_auto_publish_without_fact_validator_stays_pending_review(self):
        self.env["WESTCOTT_AUTO_PUBLISH"] = "true"
        self.env.pop("WESTCOTT_FACT_VALIDATOR", None)
        with patch.dict(os.environ, self.env, clear=True), \
             patch.object(runner, "_run_stop_gate", return_value=(True, "")):
            result = runner.execute(self.args)
        self.assertEqual(result, 0)
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "drafted")
        self.assertEqual(manifest["validation"]["status"], "pending_review")

    def test_auto_publish_requires_matching_independent_fact_validation_receipt(self):
        validator = self.fake_bin / "fact-validator"
        validator.write_text("""#!/usr/bin/env python3
import argparse, hashlib, json
from pathlib import Path
parser = argparse.ArgumentParser()
parser.add_argument('--draft'); parser.add_argument('--research'); parser.add_argument('--run-id')
parser.add_argument('--date'); parser.add_argument('--timezone'); parser.add_argument('--max-story-age-hours')
parser.add_argument('--ruleset-version')
args = parser.parse_args()
print(json.dumps({
  'status': 'passed', 'run_id': args.run_id, 'ruleset_version': args.ruleset_version,
  'draft_sha256': hashlib.sha256(Path(args.draft).read_bytes()).hexdigest(),
  'research_sha256': hashlib.sha256(Path(args.research).read_bytes()).hexdigest(),
}))
""", encoding="utf-8")
        validator.chmod(0o755)
        self.env["WESTCOTT_AUTO_PUBLISH"] = "true"
        self.env["WESTCOTT_FACT_VALIDATOR"] = str(validator)
        with patch.dict(os.environ, self.env, clear=True), \
             patch.object(runner, "_run_stop_gate", return_value=(True, "")):
            result = runner.execute(self.args)
        self.assertEqual(result, 0)
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "ready_for_docs")
        self.assertEqual(manifest["validation"]["status"], "passed")
        self.assertTrue(manifest["validation"]["automatic_publish_enabled"])

    def test_pending_draft_can_be_promoted_without_another_claude_call(self):
        with patch.dict(os.environ, self.env, clear=True), \
             patch.object(runner, "_run_stop_gate", return_value=(True, "")):
            self.assertEqual(runner.execute(self.args), 0)
        original_manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        validator = self.fake_bin / "fact-validator-promote"
        validator.write_text("""#!/usr/bin/env python3
import argparse, hashlib, json
from pathlib import Path
parser = argparse.ArgumentParser()
parser.add_argument('--draft'); parser.add_argument('--research'); parser.add_argument('--run-id')
parser.add_argument('--date'); parser.add_argument('--timezone'); parser.add_argument('--max-story-age-hours')
parser.add_argument('--ruleset-version')
args = parser.parse_args()
print(json.dumps({
  'status': 'passed', 'run_id': args.run_id, 'ruleset_version': args.ruleset_version,
  'draft_sha256': hashlib.sha256(Path(args.draft).read_bytes()).hexdigest(),
  'research_sha256': hashlib.sha256(Path(args.research).read_bytes()).hexdigest(),
}))
""", encoding="utf-8")
        validator.chmod(0o755)
        self._write_fake_claude("should-not-be-used", model_exit=7)
        self.env["WESTCOTT_AUTO_PUBLISH"] = "true"
        self.env["WESTCOTT_FACT_VALIDATOR"] = str(validator)
        with patch.dict(os.environ, self.env, clear=True), \
             patch.object(runner, "_run_stop_gate", return_value=(True, "")):
            self.assertEqual(runner.execute(self.args), 0)
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "ready_for_docs")
        self.assertEqual(manifest["validation"]["detail_code"], "independent_fact_validator_passed")
        self.assertEqual(manifest["created_at"], original_manifest["created_at"])
        self.assertEqual(manifest["attempts"]["fact_review"], 1)

    def test_pending_artifact_hash_drift_blocks_promotion(self):
        with patch.dict(os.environ, self.env, clear=True), \
             patch.object(runner, "_run_stop_gate", return_value=(True, "")):
            self.assertEqual(runner.execute(self.args), 0)
        draft = self.run_dir / "draft.md"
        draft.write_text(draft.read_text(encoding="utf-8") + "被篡改\n", encoding="utf-8")
        self.env["WESTCOTT_AUTO_PUBLISH"] = "true"
        with patch.dict(os.environ, self.env, clear=True), \
             patch.object(runner, "_run_stop_gate", return_value=(True, "")):
            with self.assertRaisesRegex(runner.RunnerError, "differs from its manifest"):
                runner.execute(self.args)

    def test_changed_daily_brief_blocks_pending_promotion(self):
        with patch.dict(os.environ, self.env, clear=True), \
             patch.object(runner, "_run_stop_gate", return_value=(True, "")):
            self.assertEqual(runner.execute(self.args), 0)
        brief = json.loads(self.brief_path.read_text(encoding="utf-8"))
        brief["topic_instructions"] = ["不同的选题"]
        self.brief_path.write_text(json.dumps(brief), encoding="utf-8")
        self.env["WESTCOTT_AUTO_PUBLISH"] = "true"
        with patch.dict(os.environ, self.env, clear=True), \
             patch.object(runner, "_run_stop_gate", return_value=(True, "")):
            with self.assertRaisesRegex(runner.RunnerError, "daily brief changed"):
                runner.execute(self.args)

    def test_stale_and_future_publication_times_are_rejected(self):
        for timestamp in ("2030-04-02T07:00:00+08:00", "2030-04-05T08:06:00+08:00"):
            with self.subTest(published_at=timestamp):
                self._write_fake_claude(self.output(published_at=timestamp))
                with patch.dict(os.environ, self.env, clear=True), \
                     patch.object(runner, "_run_stop_gate", return_value=(True, "")):
                    with self.assertRaises(runner.RunnerError):
                        runner.execute(self.args)
                self.assertFalse((self.run_dir / "manifest.json").exists())

    def test_cli_nonzero_exit_does_not_write_draft(self):
        self._write_fake_claude(self.output(), model_exit=7)
        with patch.dict(os.environ, self.env, clear=True), \
             patch.object(runner, "_run_stop_gate", return_value=(True, "")):
            with self.assertRaises(runner.RunnerError):
                runner.execute(self.args)
        self.assertFalse((self.run_dir / "draft.md").exists())

    def test_invalid_cli_output_is_rejected(self):
        self._write_fake_claude("not-json")
        with patch.dict(os.environ, self.env, clear=True), \
             patch.object(runner, "_run_stop_gate", return_value=(True, "")):
            with self.assertRaises(runner.RunnerError):
                runner.execute(self.args)
        self.assertFalse((self.run_dir / "manifest.json").exists())

    def test_cli_timeout_fails_closed_and_does_not_write_draft(self):
        self._write_fake_claude(self.output(), sleep_seconds=4)
        self.env["WESTCOTT_CLAUDE_TIMEOUT_SECONDS"] = "1"
        with patch.dict(os.environ, self.env, clear=True), \
             patch.object(runner, "_run_stop_gate", return_value=(True, "")):
            with self.assertRaises(runner.RunnerError):
                runner.execute(self.args)
        self.assertFalse((self.run_dir / "draft.md").exists())

    def test_fact_validator_environment_uses_allowlist_not_parent_secrets(self):
        values = {
            "PATH": "/usr/bin", "LANG": "C.UTF-8", "TZ": "Asia/Shanghai",
            "GOOGLE_APPLICATION_CREDENTIALS": "/secrets/google.json",
            "WESTCOTT_GOOGLE_DRIVE_FOLDER_ID": "drive-folder",
            "WESTCOTT_ERROR_WEBHOOK_URL": "https://secret.invalid/hook",
            "ANTHROPIC_API_KEY": "must-not-leak",
            "CUSTOM_PRIVATE_TOKEN": "must-not-leak",
            "WESTCOTT_TAVILY_API_KEY": "tavily-purpose-key",
            "WESTCOTT_FACT_CHECK_OPENAI_API_KEY": "openai-purpose-key",
            "OPENAI_API_KEY": "unscoped-openai-key-must-not-leak",
        }
        draft = self.run_dir / "draft.md"
        research = self.run_dir / "research.json"
        draft.write_text("draft\n", encoding="utf-8")
        research.write_text("{}\n", encoding="utf-8")
        draft_hash = runner.hashlib.sha256(draft.read_bytes()).hexdigest()
        research_hash = runner.hashlib.sha256(research.read_bytes()).hexdigest()
        receipt = json.dumps({
            "status": "passed", "run_id": self.run_id, "ruleset_version": "rules-v1",
            "draft_sha256": draft_hash, "research_sha256": research_hash,
        }).encode("utf-8")
        with patch.dict(os.environ, values, clear=True):
            with patch.object(runner.subprocess, "run", return_value=runner.subprocess.CompletedProcess(
                args=[str(self.claude)], returncode=0, stdout=receipt, stderr=b"",
            )) as mocked_run:
                accepted = runner._run_fact_validator(
                    str(self.claude), draft_path=draft, research_path=research,
                    run_id=self.run_id, task_date="2030-04-05", timezone_name="Asia/Shanghai",
                    max_story_age_hours=48, ruleset_version="rules-v1",
                    draft_hash=draft_hash, research_hash=research_hash,
                )
                clean = mocked_run.call_args.kwargs["env"]
        self.assertTrue(accepted)
        self.assertEqual(clean, {
            "PATH": "/usr/bin", "LANG": "C.UTF-8", "TZ": "Asia/Shanghai",
            "WESTCOTT_TAVILY_API_KEY": "tavily-purpose-key",
            "WESTCOTT_FACT_CHECK_OPENAI_API_KEY": "openai-purpose-key",
        })
        self.assertNotIn("GOOGLE_APPLICATION_CREDENTIALS", clean)
        self.assertEqual({key for key in clean if "KEY" in key}, {
            "WESTCOTT_TAVILY_API_KEY", "WESTCOTT_FACT_CHECK_OPENAI_API_KEY",
        })
        self.assertNotIn("OPENAI_API_KEY", clean)

    def test_old_cli_version_and_auth_failure_are_rejected(self):
        self._write_fake_claude(self.output(), version="2.1.100")
        with patch.dict(os.environ, self.env, clear=True):
            with self.assertRaises(runner.RunnerError):
                runner.check_claude(str(self.claude), runner._clean_model_env())
        self._write_fake_claude(self.output(), auth_exit=1)
        with patch.dict(os.environ, self.env, clear=True):
            with self.assertRaises(runner.RunnerError):
                runner.check_claude(str(self.claude), runner._clean_model_env())

    def test_run_directory_must_match_state_and_run_id(self):
        self.args.run_dir = str(self.root / "elsewhere")
        with patch.dict(os.environ, self.env, clear=True):
            with self.assertRaises(runner.RunnerError):
                runner._validate_args(self.args)

    def test_invalid_timezone_is_rejected(self):
        self.args.timezone = "Mars/Olympus"
        with patch.dict(os.environ, self.env, clear=True):
            with self.assertRaises(runner.RunnerError):
                runner._validate_args(self.args)


if __name__ == "__main__":
    unittest.main()
