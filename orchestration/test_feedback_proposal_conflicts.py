#!/usr/bin/env python3
"""Integration tests for conflict scenarios in the Westcott proposal validator.

Same-rule and cross-rule semantic conflicts are detected deterministically by
comparing expected outcomes for the same registered canonical test case. This
avoids brittle keyword guessing or opaque model judgments. The registry is
deliberately small and must be extended as rule/test coverage grows.

The production validator and this test share the canonical semantic-case
registry. The strict option ensures detected semantic conflicts are rejected
before any candidate is staged.

Run:
  PYTHONPATH=orchestration:automation \
    /tmp/westcott-runtime-venv/bin/python \
    orchestration/test_feedback_proposal_conflicts.py
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from semantic_conflicts import (
    CANONICAL_SEMANTIC_CASES,
    detect_cross_rule_semantic_conflicts,
    detect_semantic_conflicts,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = PROJECT_ROOT / "orchestration" / "validate_feedback_proposal.py"
RULES_VERSION = "2026.11.1"
RULES_HASH = "a" * 64
RUN_IDS = ["daily-2026-11-18", "daily-2026-11-20", "daily-2026-11-22"]
EVALUATION_ID = "weekly-2026-W48-conflict-probe"

INLINE_ONLY = (
    "Every factual claim must place its source marker in the same paragraph; "
    "a footnote-only marker is invalid."
)
FOOTNOTE_ONLY = (
    "Every factual claim must place its source marker in a footnote; "
    "an inline marker in the same paragraph is invalid."
)

def make_candidate(
    candidate_id: str,
    *,
    target_rule_id: str = "SOURCE_TRACEABILITY",
    change_type: str = "clarify",
    proposed_after: str = INLINE_ONLY,
    test_prefix: str = "source-marker",
    tests: list[dict] | None = None,
) -> dict:
    return {
        "candidate_id": candidate_id,
        "target_rule_id": target_rule_id,
        "change_type": change_type,
        "before": "Every factual claim must carry a source marker.",
        "proposed_after": proposed_after,
        "rationale": "The candidate is grounded in the repeated source-reference signal.",
        "expected_benefit": "Clarify source-marker placement.",
        "risks": ["A competing interpretation could cause false blocks."],
        "scope": "Daily editorial content.",
        "evidence_run_ids": RUN_IDS,
        "tests_required": tests or [
            {
                "test_id": f"{test_prefix}-pass",
                "input_summary": "A factual claim with an inline source marker.",
                "expected": "pass",
            },
            {
                "test_id": f"{test_prefix}-block",
                "input_summary": "A factual claim whose marker appears only in a footnote.",
                "expected": "block",
            },
        ],
        "rollback_to": RULES_VERSION,
    }


def make_proposal(summary_path: Path, candidates: list[dict]) -> dict:
    summary_bytes = summary_path.read_bytes()
    return {
        "proposal_version": "westcott-feedback-proposal-v1",
        "evaluation_id": EVALUATION_ID,
        "source_summary_sha256": hashlib.sha256(summary_bytes).hexdigest(),
        "active_rules_version": RULES_VERSION,
        "active_rules_sha256": RULES_HASH,
        "decision": "review_required",
        "findings": [
            {
                "finding_id": "finding-source-marker-placement",
                "signal_type": "repeated_issue_code",
                "signal_key": "SOURCE_REFERENCE_MISSING",
                "observed_value": "3 occurrences across 3 runs",
                "evidence_run_ids": RUN_IDS,
                "likely_cause": "The placement requirement may be unclear; hypothesis only.",
                "confidence": "medium",
            }
        ],
        "proposals": candidates,
        "uncertainties": ["Conflict fixtures intentionally model competing rule changes."],
        "active_rules_modified": False,
    }


def summary_fixture() -> dict:
    return {
        "evaluation_id": EVALUATION_ID,
        "active_rules_version": RULES_VERSION,
        "active_rules_sha256": RULES_HASH,
        "decision": "review_required",
        "repeated_issue_codes": [
            {"code": "SOURCE_REFERENCE_MISSING", "evidence_run_ids": RUN_IDS}
        ],
        "low_score_signals": [],
    }


def make_cross_rule_conflict() -> list[dict]:
    """Two separate rules disagree on the same two canonical input cases."""
    inline_case = {
        "test_id": "case:claim-with-inline-source-marker",
        "input_summary": CANONICAL_SEMANTIC_CASES["case:claim-with-inline-source-marker"],
    }
    footnote_case = {
        "test_id": "case:claim-with-footnote-only-source-marker",
        "input_summary": CANONICAL_SEMANTIC_CASES["case:claim-with-footnote-only-source-marker"],
    }
    source_rule = make_candidate(
        "candidate-source-rule-inline",
        target_rule_id="SOURCE_TRACEABILITY",
        proposed_after=INLINE_ONLY,
        tests=[
            {**inline_case, "expected": "pass"},
            {**footnote_case, "expected": "block"},
        ],
    )
    citation_rule = make_candidate(
        "candidate-citation-rule-footnote",
        target_rule_id="CITATION_PLACEMENT",
        proposed_after=FOOTNOTE_ONLY,
        tests=[
            {**inline_case, "expected": "block"},
            {**footnote_case, "expected": "pass"},
        ],
    )
    return [source_rule, citation_rule]


@dataclass
class Case:
    name: str
    candidates: list[dict]
    expected_returncode: int
    expected_candidate_count: int | None
    expected_staged_files: int
    gap: str = ""
    expected_semantic_conflicts: int = 0
    setup: Callable[[Path, Path, list[dict]], bytes | None] | None = None
    verify_after: Callable[[Path, Path, bytes | None], None] | None = None


def make_cases() -> list[Case]:
    same_rule_opposites = [
        make_candidate(
            "candidate-conflict-inline-required",
            proposed_after=INLINE_ONLY,
            tests=[
                {"test_id": "case:claim-with-inline-source-marker", "input_summary": CANONICAL_SEMANTIC_CASES["case:claim-with-inline-source-marker"], "expected": "pass"},
                {"test_id": "case:claim-with-footnote-only-source-marker", "input_summary": CANONICAL_SEMANTIC_CASES["case:claim-with-footnote-only-source-marker"], "expected": "block"},
            ],
        ),
        make_candidate(
            "candidate-conflict-footnote-only",
            proposed_after=FOOTNOTE_ONLY,
            tests=[
                {"test_id": "case:claim-with-inline-source-marker", "input_summary": CANONICAL_SEMANTIC_CASES["case:claim-with-inline-source-marker"], "expected": "block"},
                {"test_id": "case:claim-with-footnote-only-source-marker", "input_summary": CANONICAL_SEMANTIC_CASES["case:claim-with-footnote-only-source-marker"], "expected": "pass"},
            ],
        ),
    ]
    add_retire = [
        make_candidate(
            "candidate-add-source-marker-rule",
            change_type="add",
            proposed_after="Add a rule requiring inline source markers for every factual claim.",
            test_prefix="add-marker",
        ),
        make_candidate(
            "candidate-retire-source-marker-rule",
            change_type="retire",
            proposed_after="Retire the source-marker requirement; do not require inline citations.",
            test_prefix="retire-marker",
        ),
    ]
    cross_rule = make_cross_rule_conflict()
    identical_duplicate = make_candidate(
        "candidate-duplicate-id-identical",
        proposed_after=INLINE_ONLY,
        test_prefix="same-content",
    )
    duplicate_id_conflict = [
        make_candidate(
            "candidate-duplicate-id-conflicting",
            proposed_after=INLINE_ONLY,
            test_prefix="duplicate-inline",
        ),
        make_candidate(
            "candidate-duplicate-id-conflicting",
            proposed_after=FOOTNOTE_ONLY,
            test_prefix="duplicate-footnote",
        ),
    ]
    preexisting_candidate = make_candidate(
        "candidate-already-staged",
        proposed_after=INLINE_ONLY,
        test_prefix="new-content",
    )

    def verify_existing_unchanged(candidate_root: Path, active_rules: Path, original: bytes | None) -> None:
        target = candidate_root / "candidate-already-staged" / "proposal.json"
        assert original is not None and target.read_bytes() == original, "existing candidate was overwritten"

    return [
        Case(
            name="same target_rule_id with mutually exclusive clarifications",
            candidates=same_rule_opposites,
            expected_returncode=2,
            expected_candidate_count=None,
            expected_staged_files=0,
            expected_semantic_conflicts=2,
            gap="Canonical pass/block disagreements for one rule are rejected before staging.",
        ),
        Case(
            name="same target_rule_id with add-versus-retire actions",
            candidates=add_retire,
            expected_returncode=2,
            expected_candidate_count=None,
            expected_staged_files=0,
            expected_semantic_conflicts=1,
            gap="A retirement and another modification to the same rule are rejected before staging.",
        ),
        Case(
            name="cross-rule semantic conflict detected by canonical test outcomes",
            candidates=cross_rule,
            expected_returncode=2,
            expected_candidate_count=None,
            expected_staged_files=0,
            expected_semantic_conflicts=2,
            gap="The production validator rejected the conflict before staging any candidate.",
        ),
        Case(
            name="duplicate candidate_id with identical content",
            candidates=[copy.deepcopy(identical_duplicate), copy.deepcopy(identical_duplicate)],
            expected_returncode=0,
            expected_candidate_count=1,
            expected_staged_files=1,
            gap="Identical duplicate proposals are idempotently deduplicated and report one unique candidate.",
        ),
        Case(
            name="duplicate candidate_id with conflicting content",
            candidates=duplicate_id_conflict,
            expected_returncode=2,
            expected_candidate_count=None,
            expected_staged_files=0,
            gap="The conflicting duplicate ID was rejected before any candidate in the batch was staged.",
        ),
        Case(
            name="candidate_id collides with a pre-existing different candidate",
            candidates=[preexisting_candidate],
            expected_returncode=2,
            expected_candidate_count=None,
            expected_staged_files=1,
            gap="Fail-closed collision handling should preserve the existing candidate bytes.",
            setup=lambda candidate_root, active_rules, candidates: seed_existing_candidate(
                candidate_root, active_rules, candidates
            ),
            verify_after=verify_existing_unchanged,
        ),
        Case(
            name="unsupported widen change_type",
            candidates=[
                make_candidate(
                    "candidate-widen-not-allowed",
                    change_type="widen",
                    proposed_after="Allow factual claims without any source marker.",
                    test_prefix="widen",
                )
            ],
            expected_returncode=2,
            expected_candidate_count=None,
            expected_staged_files=0,
            gap="The Schema must reject widen and stage no candidate.",
        ),
        Case(
            name="valid single-candidate control",
            candidates=[make_candidate("candidate-valid-control", test_prefix="control")],
            expected_returncode=0,
            expected_candidate_count=1,
            expected_staged_files=1,
            gap="Control case: proves the generated fixture is otherwise valid.",
        ),
    ]


def seed_existing_candidate(candidate_root: Path, active_rules: Path, candidates: list[dict]) -> bytes:
    candidate = candidates[0]
    target = candidate_root / candidate["candidate_id"] / "proposal.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    existing_payload = {
        "evaluation_id": EVALUATION_ID,
        "source_summary_sha256": "0" * 64,
        "active_rules_version": RULES_VERSION,
        "active_rules_sha256": RULES_HASH,
        "candidate": {**candidate, "proposed_after": "Previously reviewed text; do not overwrite."},
        "approval_status": "pending_human_review",
        "active_rules_modified": False,
    }
    original = (json.dumps(existing_payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    target.write_bytes(original)
    return original


def run_case(case: Case, require_validator_rejection: bool) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory(prefix="westcott-conflict-test-") as temp_dir:
        temp = Path(temp_dir)
        summary_path = temp / "summary.json"
        summary_path.write_text(json.dumps(summary_fixture(), sort_keys=True) + "\n", encoding="utf-8")
        proposal_path = temp / "proposal.json"
        proposal = make_proposal(summary_path, case.candidates)
        proposal_path.write_text(json.dumps(proposal, sort_keys=True) + "\n", encoding="utf-8")
        candidate_root = temp / "candidates"
        active_rules = temp / "active-rules.json"
        active_rules_bytes = b'{"version":"2026.11.1","rules":["unchanged"]}\n'
        active_rules.write_bytes(active_rules_bytes)

        seeded_bytes = case.setup(candidate_root, active_rules, case.candidates) if case.setup else None
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(
            filter(None, [str(PROJECT_ROOT / "orchestration"), env.get("PYTHONPATH", "")])
        )
        result = subprocess.run(
            [
                sys.executable,
                str(VALIDATOR),
                "--summary",
                str(summary_path),
                "--proposal",
                str(proposal_path),
                "--candidate-root",
                str(candidate_root),
            ],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        detected = detect_semantic_conflicts(proposal["proposals"])
        staged_files = sorted(candidate_root.glob("*/proposal.json")) if candidate_root.exists() else []
        failures: list[str] = []
        if result.returncode != case.expected_returncode:
            failures.append(f"return code {result.returncode}, expected {case.expected_returncode}")
        if len(staged_files) != case.expected_staged_files:
            failures.append(f"staged files {len(staged_files)}, expected {case.expected_staged_files}")
        if len(detected) != case.expected_semantic_conflicts:
            failures.append(
                f"detected semantic conflicts {len(detected)}, expected {case.expected_semantic_conflicts}"
            )
        if case.expected_semantic_conflicts and "semantic conflict detected before staging" not in result.stderr:
            failures.append("validator did not reject the proposal for the detected semantic conflict")
        if case.expected_candidate_count is not None:
            try:
                payload = json.loads(result.stdout)
            except json.JSONDecodeError:
                failures.append(f"stdout was not valid JSON: {result.stdout!r}")
            else:
                if payload.get("candidate_count") != case.expected_candidate_count:
                    failures.append(
                        f"candidate_count {payload.get('candidate_count')}, expected {case.expected_candidate_count}"
                    )
                if payload.get("active_rules_modified") is not False:
                    failures.append("validator reported active_rules_modified other than false")
        elif result.returncode == 0:
            failures.append("expected a rejection but validator returned success")
        if require_validator_rejection and detected and result.returncode == 0:
            failures.append("strict mode: validator accepted a detected semantic conflict")
        if active_rules.read_bytes() != active_rules_bytes:
            failures.append("active rules fixture changed")
        if case.verify_after:
            try:
                case.verify_after(candidate_root, active_rules, seeded_bytes)
            except AssertionError as exc:
                failures.append(str(exc))

        detail = result.stderr.strip() or result.stdout.strip()
        if failures:
            return False, "; ".join(failures) + (f"; validator: {detail}" if detail else "")
        note = case.gap
        if detected:
            details = ", ".join(
                f"{item['semantic_case_id']} ({item['conflict']})" for item in detected
            )
            note = f"Detected: {details}. {note}"
        return True, note


def verify_detector_controls() -> None:
    cases = make_cross_rule_conflict()
    found = detect_cross_rule_semantic_conflicts(cases)
    assert len(found) == 2, f"expected two cross-rule conflicts, got {len(found)}"

    aligned = copy.deepcopy(cases)
    for candidate in aligned:
        candidate["tests_required"][0]["expected"] = "pass"
        candidate["tests_required"][1]["expected"] = "block"
    assert not detect_cross_rule_semantic_conflicts(aligned), "aligned outcomes must not be flagged"

    unregistered = copy.deepcopy(cases)
    for candidate in unregistered:
        for test in candidate["tests_required"]:
            test["test_id"] = "unregistered:" + test["test_id"]
    assert not detect_cross_rule_semantic_conflicts(unregistered), "unregistered cases must not be guessed"

    mismatched = copy.deepcopy(cases)
    mismatched[0]["tests_required"][0]["input_summary"] = "A different behavior with a familiar ID."
    mismatched_conflicts = detect_cross_rule_semantic_conflicts(mismatched)
    assert all(
        item["semantic_case_id"] != "case:claim-with-inline-source-marker"
        for item in mismatched_conflicts
    ), "mismatched canonical descriptions must not be trusted"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--require-validator-rejection",
        action="store_true",
        help="Fail if the production validator accepts a conflict found by the shared detector.",
    )
    args = parser.parse_args()

    verify_detector_controls()
    cases = make_cases()
    failed = 0
    print("PASS: canonical-case detector identifies opposing expectations and ignores aligned/unregistered cases.")
    print(f"Running {len(cases)} validator integration scenarios against {VALIDATOR.name}\n")
    for case in cases:
        passed, detail = run_case(case, args.require_validator_rejection)
        status = "PASS" if passed else "FAIL"
        print(f"[{status}] {case.name}")
        if detail:
            print(f"       {detail}")
        if not passed:
            failed += 1
    print(f"\nSummary: {len(cases) - failed} passed, {failed} failed.")
    if failed:
        if args.require_validator_rejection:
            print("Strict mode was requested; detected semantic conflicts must be rejected before staging.")
        return 1
    print("Note: registered same/cross-rule conflicts and retire-versus-modify actions are blocked; unregistered/text-only cases still require review.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
