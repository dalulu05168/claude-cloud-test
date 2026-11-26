"""Deterministic rule-conflict checks for registered semantic cases.

Only stable case IDs with their canonical descriptions are evidence. This avoids
keyword guessing or opaque model judgments; add cases here as rule coverage grows.
"""
from __future__ import annotations

CANONICAL_SEMANTIC_CASES = {
    "case:claim-with-inline-source-marker": (
        "A factual claim with its source marker in the same paragraph."
    ),
    "case:claim-with-footnote-only-source-marker": (
        "A factual claim whose source marker appears only in a footnote."
    ),
}


def _normalize_text(value: str) -> str:
    return " ".join(value.casefold().split())


def _detect_canonical_conflicts(proposals: list[dict], *, cross_rule_only: bool) -> list[dict]:
    observations: dict[str, list[dict]] = {}
    for proposal in proposals:
        target_rule_id = proposal["target_rule_id"]
        candidate_id = proposal["candidate_id"]
        for test in proposal["tests_required"]:
            test_id = test["test_id"]
            canonical_input = CANONICAL_SEMANTIC_CASES.get(test_id)
            if canonical_input is None:
                continue
            if _normalize_text(test["input_summary"]) != _normalize_text(canonical_input):
                # A registered ID paired with another description is ambiguous;
                # do not infer a conflict from a mismatched or stale fixture.
                continue
            observations.setdefault(test_id, []).append(
                {
                    "candidate_id": candidate_id,
                    "target_rule_id": target_rule_id,
                    "expected": test["expected"],
                }
            )

    conflicts: list[dict] = []
    for test_id, entries in sorted(observations.items()):
        for index, left in enumerate(entries):
            for right in entries[index + 1 :]:
                if left["candidate_id"] == right["candidate_id"]:
                    continue
                same_rule = left["target_rule_id"] == right["target_rule_id"]
                if cross_rule_only and same_rule:
                    continue
                if left["expected"] == right["expected"]:
                    continue
                conflicts.append(
                    {
                        "semantic_case_id": test_id,
                        "input_summary": CANONICAL_SEMANTIC_CASES[test_id],
                        "left": left,
                        "right": right,
                        "conflict": f"{left['expected']} vs {right['expected']}",
                        "relationship": "same_rule" if same_rule else "cross_rule",
                    }
                )
    return conflicts


def detect_cross_rule_semantic_conflicts(proposals: list[dict]) -> list[dict]:
    """Find opposite outcomes for one canonical case across different rules."""
    return _detect_canonical_conflicts(proposals, cross_rule_only=True)


def detect_semantic_conflicts(proposals: list[dict]) -> list[dict]:
    """Find canonical same/cross-rule conflicts and incompatible retire actions."""
    conflicts = _detect_canonical_conflicts(proposals, cross_rule_only=False)
    by_rule: dict[str, list[dict]] = {}
    for proposal in proposals:
        by_rule.setdefault(proposal["target_rule_id"], []).append(proposal)

    for target_rule_id, candidates in sorted(by_rule.items()):
        for index, left_candidate in enumerate(candidates):
            for right_candidate in candidates[index + 1 :]:
                if left_candidate["candidate_id"] == right_candidate["candidate_id"]:
                    continue
                left_type = left_candidate["change_type"]
                right_type = right_candidate["change_type"]
                if (left_type == "retire") == (right_type == "retire"):
                    continue
                left = {
                    "candidate_id": left_candidate["candidate_id"],
                    "target_rule_id": target_rule_id,
                    "expected": left_type,
                }
                right = {
                    "candidate_id": right_candidate["candidate_id"],
                    "target_rule_id": target_rule_id,
                    "expected": right_type,
                }
                conflicts.append(
                    {
                        "semantic_case_id": f"rule-action:{target_rule_id}",
                        "input_summary": "One proposal retires a rule while another modifies that same rule.",
                        "left": left,
                        "right": right,
                        "conflict": f"{left_type} vs {right_type}",
                        "relationship": "incompatible_rule_actions",
                    }
                )
    return conflicts
