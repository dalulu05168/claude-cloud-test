#!/usr/bin/env python3
"""Opt-in live integration test for Tavily + OpenAI fact checking.

Run: python3 automation/test_fact_validator_e2e.py --require-live
The script never prints provider keys or source text. Without the two dedicated
credentials it skips by default; --require-live makes missing credentials fail.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "automation" / "fact_validator.py"
TZ = "Asia/Shanghai"


def _run_case(claim: str, *, expected_pass: bool, case_name: str) -> None:
    zone = ZoneInfo(TZ)
    now = datetime.now(zone).replace(microsecond=0)
    task_date = now.date().isoformat()
    run_id = f"daily-{task_date}"
    source_url = "https://en.wikipedia.org/wiki/Paris"
    story = {
        "country": "法国",
        "title": "巴黎城市事实核查集成测试",
        "source_url": source_url,
        "source_title": "Paris",
        "published_at": (now - timedelta(minutes=2)).isoformat(),
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/a/a8/Tour_Eiffel_Wikimedia_Commons.jpg",
        "verified_facts": [claim],
    }
    draft = (
        "===== 资讯助理 =====\n\n"
        "法国财经资讯｜巴黎城市事实核查集成测试\n\n"
        f"{claim}\n\n"
        "Please verify the claim before publication.\n\n"
        f"配图：{story['image_url']}\n"
    )
    research = {
        "schema_version": "westcott-daily-research-v1",
        "task_date": task_date,
        "market_scope": "integration test only",
        "source_allowlist": ["en.wikipedia.org", "britannica.com"],
        "stories": [story],
    }
    with tempfile.TemporaryDirectory(prefix=f"westcott-fact-e2e-{case_name}-") as temp:
        run_dir = Path(temp)
        draft_path = run_dir / "draft.md"
        research_path = run_dir / "research.json"
        draft_path.write_text(draft, encoding="utf-8")
        research_bytes = (json.dumps(research, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        research_path.write_text(research_bytes, encoding="utf-8")
        args = [
            sys.executable, str(VALIDATOR),
            "--draft", str(draft_path), "--research", str(research_path),
            "--run-id", run_id, "--date", task_date, "--timezone", TZ,
            "--max-story-age-hours", "48", "--ruleset-version", "e2e-live-test-v1",
        ]
        completed = subprocess.run(
            args, cwd=ROOT, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=300, check=False, env={
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "LANG": "C.UTF-8",
                "TZ": TZ,
                "WESTCOTT_TAVILY_API_KEY": os.environ["WESTCOTT_TAVILY_API_KEY"],
                "WESTCOTT_FACT_CHECK_OPENAI_API_KEY": os.environ["WESTCOTT_FACT_CHECK_OPENAI_API_KEY"],
            },
        )
        if expected_pass:
            if completed.returncode != 0:
                raise RuntimeError(f"{case_name}: expected supported claim rejected (exit {completed.returncode})")
            try:
                receipt = json.loads(completed.stdout.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as exc:
                raise RuntimeError(f"{case_name}: validator did not return JSON receipt") from exc
            expected = {
                "status": "passed",
                "run_id": run_id,
                "ruleset_version": "e2e-live-test-v1",
                "draft_sha256": hashlib.sha256(draft.encode("utf-8")).hexdigest(),
                "research_sha256": hashlib.sha256(research_bytes.encode("utf-8")).hexdigest(),
            }
            if receipt != expected:
                raise RuntimeError(f"{case_name}: receipt does not match the Runner contract")
        else:
            semantic_rejections = (
                b"fact-check model did not support the story",
                b"fact-check model found an unsupported or unclear claim",
                b"fact-check evidence does not support the claim",
            )
            if (completed.returncode == 0 or completed.stdout.strip()
                    or not any(message in completed.stderr for message in semantic_rejections)):
                raise RuntimeError(f"{case_name}: false claim was not semantically rejected without a receipt")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-live", action="store_true",
                        help="fail instead of skipping if dedicated provider credentials are absent")
    args = parser.parse_args()
    required = ("WESTCOTT_TAVILY_API_KEY", "WESTCOTT_FACT_CHECK_OPENAI_API_KEY")
    missing = [name for name in required if not os.environ.get(name, "").strip()]
    if missing:
        if args.require_live:
            print("FAIL: live integration credentials are missing: " + ", ".join(missing), file=sys.stderr)
            return 2
        print("SKIP: set WESTCOTT_TAVILY_API_KEY and WESTCOTT_FACT_CHECK_OPENAI_API_KEY for live integration")
        return 0
    try:
        _run_case("Paris is the capital of France.", expected_pass=True, case_name="supported")
        _run_case("Paris is the capital of Germany.", expected_pass=False, case_name="false-claim")
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
        print(f"FAIL: live fact-check integration test failed ({exc})", file=sys.stderr)
        return 1
    print("PASS: live supported claim received a valid receipt; false claim was rejected without a receipt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
