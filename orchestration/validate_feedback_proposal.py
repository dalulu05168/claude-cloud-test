#!/usr/bin/env python3
"""Validate an LLM proposal against deterministic evidence and stage it safely.

This command can only write under a caller-supplied candidate directory. It has
no code path that updates an active rules pointer or deploys a candidate.
"""
from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from semantic_conflicts import detect_semantic_conflicts

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback; no-clobber links remain enabled.
    fcntl = None

ROOT = Path(__file__).resolve().parents[1]
PROPOSAL_SCHEMA = ROOT / "docs" / "feedback-proposal.schema.json"
CANDIDATE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class ProposalError(ValueError):
    pass


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def validate_proposal(summary_path: Path, proposal_path: Path) -> tuple[dict, dict]:
    summary_bytes = summary_path.read_bytes()
    summary = json.loads(summary_bytes)
    proposal = _read_json(proposal_path)
    schema = _read_json(PROPOSAL_SCHEMA)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(proposal), key=lambda e: list(map(str, e.absolute_path)))
    if errors:
        message = "; ".join(f"/{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in errors[:8])
        raise ProposalError(f"proposal schema validation failed: {message}")
    expected_hash = hashlib.sha256(summary_bytes).hexdigest()
    if proposal["source_summary_sha256"].lower() != expected_hash:
        raise ProposalError("source_summary_sha256 does not match the deterministic summary file")
    if proposal["evaluation_id"] != summary.get("evaluation_id"):
        raise ProposalError("proposal evaluation_id does not match the deterministic summary")
    if proposal["decision"] != summary["decision"]:
        raise ProposalError("proposal decision does not match the deterministic report decision")
    if proposal["active_rules_version"] != summary.get("active_rules_version", proposal["active_rules_version"]):
        raise ProposalError("active rules version mismatch")
    if proposal["active_rules_sha256"].lower() != summary.get("active_rules_sha256", "").lower():
        raise ProposalError("active_rules_sha256 does not match the deterministic summary")

    repeated = {item["code"]: set(item["evidence_run_ids"]) for item in summary.get("repeated_issue_codes", [])}
    low_scores = {item["dimension"]: set(item.get("evidence_run_ids", [])) for item in summary.get("low_score_signals", [])}
    signal_evidence = {"repeated_issue_code": repeated, "low_score_dimension": low_scores}

    for finding in proposal["findings"]:
        allowed = signal_evidence.get(finding["signal_type"], {}).get(finding["signal_key"])
        if not allowed:
            raise ProposalError(f"finding references a signal absent from the summary: {finding['signal_key']}")
        if not set(finding["evidence_run_ids"]).issubset(allowed):
            raise ProposalError(f"finding cites run_id values not present in the summary: {finding['finding_id']}")

    for candidate in proposal["proposals"]:
        if not CANDIDATE_ID.fullmatch(candidate["candidate_id"]):
            raise ProposalError("unsafe candidate_id")
        allowed = set()
        for finding in proposal["findings"]:
            if set(candidate["evidence_run_ids"]).issubset(set(finding["evidence_run_ids"])):
                allowed.update(finding["evidence_run_ids"])
        if not set(candidate["evidence_run_ids"]).issubset(allowed):
            raise ProposalError(f"proposal evidence must be included in a declared finding: {candidate['candidate_id']}")
        outcomes = {test["expected"] for test in candidate["tests_required"]}
        if not {"pass", "block"}.issubset(outcomes):
            raise ProposalError(f"candidate needs at least one pass and one block test: {candidate['candidate_id']}")
        if candidate["rollback_to"] != proposal["active_rules_version"]:
            raise ProposalError(f"candidate rollback_to must equal active version: {candidate['candidate_id']}")

    conflicts = detect_semantic_conflicts(proposal["proposals"])
    if conflicts:
        details = "; ".join(
            f"{item['relationship']} {item['semantic_case_id']}: {item['left']['target_rule_id']}="
            f"{item['left']['expected']} vs {item['right']['target_rule_id']}="
            f"{item['right']['expected']}"
            for item in conflicts[:5]
        )
        raise ProposalError(f"semantic conflict detected before staging: {details}")

    return summary, proposal


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def stage_candidate_batch(candidate_root: Path, proposal: dict) -> None:
    """Preflight and stage all candidates, rolling back files on commit errors."""
    candidate_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    candidate_root = candidate_root.resolve()
    lock_path = candidate_root / ".staging.lock"
    lock_flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    lock_fd = os.open(lock_path, lock_flags, 0o600)
    lock_handle = os.fdopen(lock_fd, "r+")
    try:
        if fcntl is not None:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        _stage_candidate_batch_locked(candidate_root, proposal)
    finally:
        if fcntl is not None:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()


def _stage_candidate_batch_locked(candidate_root: Path, proposal: dict) -> None:
    plans_by_id: dict[str, dict] = {}
    for candidate in proposal["proposals"]:
        candidate_id = candidate["candidate_id"]
        payload = {
            "evaluation_id": proposal["evaluation_id"],
            "source_summary_sha256": proposal["source_summary_sha256"],
            "active_rules_version": proposal["active_rules_version"],
            "active_rules_sha256": proposal["active_rules_sha256"],
            "candidate": candidate,
            "approval_status": "pending_human_review",
            "active_rules_modified": False,
        }
        previous = plans_by_id.get(candidate_id)
        if previous is not None:
            if previous["payload"] != payload:
                raise ProposalError(
                    f"duplicate candidate_id has conflicting content; rejecting entire batch: {candidate_id}"
                )
            continue

        destination_dir = candidate_root / candidate_id
        if destination_dir.is_symlink():
            raise ProposalError(f"candidate directory must not be a symlink: {candidate_id}")
        if destination_dir.exists() and not destination_dir.is_dir():
            raise ProposalError(f"candidate path exists and is not a directory: {candidate_id}")
        resolved_dir = destination_dir.resolve()
        try:
            resolved_dir.relative_to(candidate_root)
        except ValueError as exc:
            raise ProposalError("candidate output escaped candidate-root") from exc
        candidate_file = resolved_dir / "proposal.json"
        if candidate_file.is_symlink():
            raise ProposalError(f"candidate proposal must not be a symlink: {candidate_id}")
        plans_by_id[candidate_id] = {
            "payload": payload,
            "directory": resolved_dir,
            "file": candidate_file,
        }

    plans = list(plans_by_id.values())
    # Preflight every existing target before writing any new candidate.
    for plan in plans:
        target = plan["file"]
        if target.exists() and _read_json(target) != plan["payload"]:
            candidate_id = plan["payload"]["candidate"]["candidate_id"]
            raise ProposalError(
                f"candidate already exists with different content; refusing entire batch: {candidate_id}"
            )

    missing = [plan for plan in plans if not plan["file"].exists()]
    if not missing:
        return

    transaction_dir = Path(tempfile.mkdtemp(prefix=".westcott-batch-", dir=candidate_root))
    temporary_files: dict[str, Path] = {}
    committed: list[Path] = []
    created_directories: list[Path] = []
    try:
        for plan in missing:
            candidate_id = plan["payload"]["candidate"]["candidate_id"]
            temporary_file = transaction_dir / f"{candidate_id}.json"
            atomic_write_json(temporary_file, plan["payload"])
            temporary_files[candidate_id] = temporary_file

        for plan in missing:
            candidate_id = plan["payload"]["candidate"]["candidate_id"]
            target_dir = plan["directory"]
            if not target_dir.exists():
                target_dir.mkdir(parents=True, mode=0o700)
                created_directories.append(target_dir)
            target = plan["file"]
            try:
                # Hard-link creation is atomic and never replaces an existing file.
                os.link(temporary_files[candidate_id], target)
            except FileExistsError:
                if target.is_symlink() or _read_json(target) != plan["payload"]:
                    raise ProposalError(
                        f"candidate appeared with different content during staging: {candidate_id}"
                    )
            else:
                committed.append(target)
    except BaseException as exc:
        rollback_errors: list[str] = []
        for target in reversed(committed):
            try:
                target.unlink()
            except OSError as rollback_exc:
                rollback_errors.append(f"{target}: {rollback_exc}")
        for directory in reversed(created_directories):
            try:
                directory.rmdir()
            except OSError as rollback_exc:
                if rollback_exc.errno not in (errno.ENOTEMPTY, errno.ENOENT):
                    rollback_errors.append(f"{directory}: {rollback_exc}")
        if rollback_errors:
            raise ProposalError(
                f"batch staging failed and rollback was incomplete: {'; '.join(rollback_errors)}"
            ) from exc
        raise
    finally:
        shutil.rmtree(transaction_dir, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--proposal", required=True, type=Path)
    parser.add_argument("--candidate-root", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        summary, proposal = validate_proposal(args.summary, args.proposal)
        if proposal["decision"] == "review_required":
            stage_candidate_batch(args.candidate_root, proposal)
        print(json.dumps({"status": "validated", "decision": proposal["decision"],
                          "candidate_count": len({item["candidate_id"] for item in proposal["proposals"]}),
                          "active_rules_modified": False}, separators=(",", ":")))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"candidate proposal rejected: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
