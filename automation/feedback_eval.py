#!/usr/bin/env python3
"""Create a deterministic, privacy-minimized feedback evaluation packet.

This script never calls an LLM and never edits active rules. Its output is input
to the evaluator prompt documented in docs/feedback-upgrade-workflow.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

DIMENSIONS = ("factual_accuracy", "structure_compliance", "tone", "readability")
ISSUE_CODE = re.compile(r"^[A-Z][A-Z0-9_:-]{0,95}$")


class FeedbackInputError(ValueError):
    pass


def _valid_record(record: Any, line_no: int) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise FeedbackInputError(f"line {line_no}: each record must be a JSON object")
    required = {"feedback_id", "run_id", "task_date", "reviewer_type", "ratings", "rework_required", "issue_codes"}
    missing = sorted(required - record.keys())
    if missing:
        raise FeedbackInputError(f"line {line_no}: missing fields {missing}")
    if not isinstance(record["task_date"], str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", record["task_date"]):
        raise FeedbackInputError(f"line {line_no}: task_date must be YYYY-MM-DD")
    try:
        date.fromisoformat(record["task_date"])
    except (TypeError, ValueError) as exc:
        raise FeedbackInputError(f"line {line_no}: task_date must be YYYY-MM-DD") from exc
    if not isinstance(record["feedback_id"], str) or not record["feedback_id"].strip():
        raise FeedbackInputError(f"line {line_no}: feedback_id must be non-empty")
    if not isinstance(record["run_id"], str) or not record["run_id"].strip():
        raise FeedbackInputError(f"line {line_no}: run_id must be non-empty")
    if not isinstance(record["reviewer_type"], str) or not record["reviewer_type"].strip():
        raise FeedbackInputError(f"line {line_no}: reviewer_type must be non-empty")
    ratings = record["ratings"]
    if not isinstance(ratings, dict):
        raise FeedbackInputError(f"line {line_no}: ratings must be an object")
    for dimension, score in ratings.items():
        if dimension not in DIMENSIONS:
            raise FeedbackInputError(f"line {line_no}: unsupported rating dimension {dimension!r}")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not 1 <= score <= 5:
            raise FeedbackInputError(f"line {line_no}: rating {dimension} must be in range 1..5")
    if not isinstance(record["rework_required"], bool):
        raise FeedbackInputError(f"line {line_no}: rework_required must be boolean")
    if not isinstance(record["issue_codes"], list) or any(
        not isinstance(code, str) or not ISSUE_CODE.fullmatch(code) for code in record["issue_codes"]
    ):
        raise FeedbackInputError(f"line {line_no}: issue_codes must be a list of uppercase rule codes")
    # Free-text notes are intentionally not copied into this packet; keep source JSONL
    # access-controlled and sanitize before it is sent to any model.
    return record


def load_feedback(path: Path, as_of: date, window_days: int) -> list[dict[str, Any]]:
    first_day = as_of - timedelta(days=window_days - 1)
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8") as source:
        for line_no, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                record = _valid_record(json.loads(line), line_no)
            except json.JSONDecodeError as exc:
                raise FeedbackInputError(f"line {line_no}: invalid JSON") from exc
            if record["feedback_id"] in seen:
                raise FeedbackInputError(f"line {line_no}: duplicate feedback_id")
            seen.add(record["feedback_id"])
            sample_date = date.fromisoformat(record["task_date"])
            if first_day <= sample_date <= as_of:
                records.append(record)
    return records


def summarize(
    records: list[dict[str, Any]], *, as_of: date, window_days: int = 28,
    min_samples: int = 10, min_issue_count: int = 3, min_distinct_runs: int = 3,
    active_rules_version: str = "unknown", evaluation_id: str = "manual-evaluation",
    source_feedback_sha256: str = "0" * 64, active_rules_sha256: str = "0" * 64,
) -> dict[str, Any]:
    start = as_of - timedelta(days=window_days - 1)
    score_values: dict[str, list[float]] = defaultdict(list)
    score_runs: dict[str, set[str]] = defaultdict(set)
    issue_runs: dict[str, set[str]] = defaultdict(set)
    issue_counts: Counter[str] = Counter()
    for record in records:
        for dimension, score in record["ratings"].items():
            score_values[dimension].append(float(score))
            score_runs[dimension].add(record["run_id"])
        for code in set(record["issue_codes"]):
            issue_counts[code] += 1
            issue_runs[code].add(record["run_id"])

    sample_count = len(records)
    run_count = len({record["run_id"] for record in records})
    means = {
        dimension: round(sum(values) / len(values), 3) if values else None
        for dimension, values in sorted(score_values.items())
    }
    score_coverage = {dimension: len(values) for dimension, values in sorted(score_values.items())}
    rework_count = sum(record["rework_required"] for record in records)
    repeated = [
        {"code": code, "feedback_count": issue_counts[code], "distinct_run_count": len(issue_runs[code]),
         "evidence_run_ids": sorted(issue_runs[code])[:20]}
        for code in sorted(issue_counts)
        if issue_counts[code] >= min_issue_count and len(issue_runs[code]) >= min_distinct_runs
    ]
    low_score_signals = [
        {"dimension": dimension, "mean": score, "rated_feedback_count": score_coverage[dimension],
         "evidence_run_ids": sorted(score_runs[dimension])[:20]}
        for dimension, score in means.items() if score is not None and score < 3.5 and score_coverage[dimension] >= min_samples
    ]
    evidence_sufficient = sample_count >= min_samples and run_count >= min_distinct_runs
    if not evidence_sufficient:
        decision = "insufficient_evidence"
    elif repeated or low_score_signals:
        decision = "review_required"
    else:
        decision = "no_change"

    return {
        "report_version": "westcott-feedback-summary-v1",
        "evaluation_id": evaluation_id,
        "source_feedback_sha256": source_feedback_sha256,
        "active_rules_version": active_rules_version,
        "active_rules_sha256": active_rules_sha256,
        "window": {"from": start.isoformat(), "through": as_of.isoformat(), "days": window_days},
        "sample": {"feedback_count": sample_count, "distinct_run_count": run_count,
                   "minimum_feedback_count": min_samples, "minimum_distinct_runs": min_distinct_runs,
                   "sufficient": evidence_sufficient},
        "metrics": {
            "mean_scores": means,
            "score_coverage": score_coverage,
            "rework_required_count": rework_count,
            "rework_rate": round(rework_count / sample_count, 4) if sample_count else None,
        },
        "repeated_issue_codes": repeated,
        "low_score_signals": low_score_signals,
        "decision": decision,
        "governance": {
            "active_rules_modified": False,
            "model_may_propose_only": True,
            "human_approval_required": True,
            "minimum_issue_count": min_issue_count,
            "minimum_distinct_runs_per_issue": min_distinct_runs,
        },
    }


def atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(payload, output, ensure_ascii=False, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="append-only feedback JSONL")
    parser.add_argument("--output", required=True, type=Path, help="deterministic evaluation packet JSON")
    parser.add_argument("--as-of", default=date.today().isoformat(), help="window end YYYY-MM-DD")
    parser.add_argument("--window-days", type=int, default=28)
    parser.add_argument("--min-samples", type=int, default=10)
    parser.add_argument("--min-issue-count", type=int, default=3)
    parser.add_argument("--min-distinct-runs", type=int, default=3)
    parser.add_argument("--active-rules-version", required=True)
    parser.add_argument("--active-rules-sha256", required=True)
    parser.add_argument("--evaluation-id", required=True)
    args = parser.parse_args(argv)
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.as_of):
            raise FeedbackInputError("--as-of must use YYYY-MM-DD")
        as_of = date.fromisoformat(args.as_of)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}", args.active_rules_version):
            raise FeedbackInputError("--active-rules-version must be a valid 1..64 character ruleset ID")
        if not re.fullmatch(r"[a-fA-F0-9]{64}", args.active_rules_sha256):
            raise FeedbackInputError("--active-rules-sha256 must be a 64 character hexadecimal digest")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", args.evaluation_id):
            raise FeedbackInputError("--evaluation-id must be a valid protocol identifier")
        if min(args.window_days, args.min_samples, args.min_issue_count, args.min_distinct_runs) < 1:
            raise FeedbackInputError("window and threshold values must be positive integers")
        records = load_feedback(args.input, as_of, args.window_days)
        source_hash = hashlib.sha256(args.input.read_bytes()).hexdigest()
        report = summarize(records, as_of=as_of, window_days=args.window_days,
                           min_samples=args.min_samples, min_issue_count=args.min_issue_count,
                           min_distinct_runs=args.min_distinct_runs,
                           active_rules_version=args.active_rules_version,
                           evaluation_id=args.evaluation_id,
                           source_feedback_sha256=source_hash,
                           active_rules_sha256=args.active_rules_sha256)
        atomic_write(args.output, report)
        print(json.dumps({"status": "written", "output": str(args.output), "decision": report["decision"]}))
        return 0
    except (OSError, ValueError, FeedbackInputError) as exc:
        print(f"feedback evaluation input error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
