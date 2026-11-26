#!/usr/bin/env bash
# Weekly feedback analysis. The evaluator adapter may propose, but cannot publish rules.
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
ENV_FILE="${WESTCOTT_FEEDBACK_ENV_FILE:-$SCRIPT_DIR/feedback.env}"
if [[ ! -r "$ENV_FILE" ]]; then
  echo "feedback workflow config is missing or unreadable: $ENV_FILE" >&2
  exit 78
fi
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

STATE_DIR="${WESTCOTT_STATE_DIR:-$PROJECT_ROOT/.westcott-state}"
FEEDBACK_FILE="${WESTCOTT_FEEDBACK_JSONL:-$STATE_DIR/feedback/inbox/feedback.jsonl}"
EVALUATOR="${WESTCOTT_FEEDBACK_EVALUATOR:-}"
ACTIVE_RULES_FILE="${WESTCOTT_ACTIVE_RULES_FILE:-}"
ACTIVE_RULES_VERSION="${WESTCOTT_ACTIVE_RULES_VERSION:-}"
TEST_INVENTORY="${WESTCOTT_FEEDBACK_TEST_INVENTORY:-}"
PYTHON="${WESTCOTT_PYTHON:-python3}"
CANDIDATE_ROOT="${WESTCOTT_CANDIDATE_ROOT:-$PROJECT_ROOT/rules/candidates}"
TIMEZONE="${WESTCOTT_TIMEZONE:-Asia/Shanghai}"
WINDOW_DAYS="${WESTCOTT_FEEDBACK_WINDOW_DAYS:-28}"
MIN_SAMPLES="${WESTCOTT_FEEDBACK_MIN_SAMPLES:-10}"
MIN_ISSUES="${WESTCOTT_FEEDBACK_MIN_ISSUE_COUNT:-3}"
MIN_RUNS="${WESTCOTT_FEEDBACK_MIN_DISTINCT_RUNS:-3}"
AS_OF="${1:-$(TZ="$TIMEZONE" date '+%F')}"

if ! command -v flock >/dev/null 2>&1; then echo "required command not found: flock" >&2; exit 69; fi
if ! command -v jq >/dev/null 2>&1; then echo "required command not found: jq" >&2; exit 69; fi
if ! command -v sha256sum >/dev/null 2>&1 || ! command -v awk >/dev/null 2>&1; then
  echo "required command not found: sha256sum or awk" >&2
  exit 69
fi
if [[ ! "$AS_OF" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || ! date -d "$AS_OF" '+%F' >/dev/null 2>&1; then
  echo "date argument must be a valid YYYY-MM-DD" >&2
  exit 64
fi
if [[ ! "$WINDOW_DAYS" =~ ^[1-9][0-9]*$ || ! "$MIN_SAMPLES" =~ ^[1-9][0-9]*$ || ! "$MIN_ISSUES" =~ ^[1-9][0-9]*$ || ! "$MIN_RUNS" =~ ^[1-9][0-9]*$ ]]; then
  echo "feedback thresholds must be positive integers" >&2
  exit 64
fi
if [[ "$PYTHON" == */* ]]; then
  [[ -x "$PYTHON" ]] || { echo "configured Python interpreter is unavailable" >&2; exit 69; }
else
  command -v "$PYTHON" >/dev/null 2>&1 || { echo "configured Python interpreter is unavailable" >&2; exit 69; }
fi
if [[ ! -x "$EVALUATOR" ]]; then
  echo "WESTCOTT_FEEDBACK_EVALUATOR must name an executable deployment adapter" >&2
  exit 69
fi
if [[ ! -r "$ACTIVE_RULES_FILE" || -z "$ACTIVE_RULES_VERSION" || ! -r "$TEST_INVENTORY" ]]; then
  echo "active rules file/version and feedback test inventory are required" >&2
  exit 78
fi
if ! jq -e . "$ACTIVE_RULES_FILE" >/dev/null 2>&1 || ! jq -e . "$TEST_INVENTORY" >/dev/null 2>&1; then
  echo "active rules or test inventory is not valid JSON" >&2
  exit 65
fi
if [[ ! -r "$FEEDBACK_FILE" ]]; then
  echo "feedback JSONL file is missing or unreadable: $FEEDBACK_FILE" >&2
  exit 66
fi

EVALUATION_WEEK="$(TZ="$TIMEZONE" date -d "$AS_OF" '+%G-W%V')"
INPUT_SHA="$(sha256sum "$FEEDBACK_FILE" | awk '{print $1}')"
RULES_SHA="$(sha256sum "$ACTIVE_RULES_FILE" | awk '{print $1}')"
CONFIG_SHA="$(printf '%s' "${ACTIVE_RULES_VERSION}|${WINDOW_DAYS}|${MIN_SAMPLES}|${MIN_ISSUES}|${MIN_RUNS}" | sha256sum | awk '{print $1}')"
EVALUATION_ID="weekly-${EVALUATION_WEEK}-${AS_OF}-${INPUT_SHA:0:10}-${RULES_SHA:0:6}-${CONFIG_SHA:0:6}"
EVALUATION_DIR="$STATE_DIR/feedback/evaluations"
LOCK_DIR="$STATE_DIR/locks"
mkdir -p "$EVALUATION_DIR" "$LOCK_DIR" "$CANDIDATE_ROOT"
exec {LOCK_FD}>"$LOCK_DIR/weekly-feedback.lock"
if ! flock -n "$LOCK_FD"; then
  echo "weekly feedback evaluation already running; exiting without duplicate work" >&2
  exit 0
fi

SUMMARY_FILE="$EVALUATION_DIR/${EVALUATION_ID}-summary.json"
PROPOSAL_FILE="$EVALUATION_DIR/${EVALUATION_ID}-proposal.json"
PROPOSAL_TMP="$EVALUATION_DIR/.${EVALUATION_ID}-proposal.$$.tmp"
cleanup() { rm -f -- "$PROPOSAL_TMP" "$PROPOSAL_TMP.normalized"; }
trap cleanup EXIT

"$PYTHON" "$PROJECT_ROOT/orchestration/feedback_eval.py" \
  --input "$FEEDBACK_FILE" \
  --output "$SUMMARY_FILE" \
  --as-of "$AS_OF" \
  --window-days "$WINDOW_DAYS" \
  --min-samples "$MIN_SAMPLES" \
  --min-issue-count "$MIN_ISSUES" \
  --min-distinct-runs "$MIN_RUNS" \
  --active-rules-version "$ACTIVE_RULES_VERSION" \
  --active-rules-sha256 "$RULES_SHA" \
  --evaluation-id "$EVALUATION_ID"

DECISION="$(jq -r '.decision' "$SUMMARY_FILE")"
echo "feedback evaluation_id=$EVALUATION_ID decision=$DECISION summary=$SUMMARY_FILE"
if [[ "$DECISION" != "review_required" ]]; then
  exit 0
fi

if [[ -s "$PROPOSAL_FILE" ]]; then
  "$PYTHON" "$PROJECT_ROOT/orchestration/validate_feedback_proposal.py" \
    --summary "$SUMMARY_FILE" \
    --proposal "$PROPOSAL_FILE" \
    --candidate-root "$CANDIDATE_ROOT"
  echo "existing proposal revalidated; evaluator was not invoked again"
  exit 0
fi

# Adapter contract: read-only active rules + sanitized evidence in, JSON proposal on stdout.
# Adapter-specific model/Agent CLI is deliberately deployment-owned, not assumed here.
"$EVALUATOR" \
  --evaluation-id "$EVALUATION_ID" \
  --summary "$SUMMARY_FILE" \
  --system-prompt "$PROJECT_ROOT/prompts/feedback-evaluator.md" \
  --active-rules "$ACTIVE_RULES_FILE" \
  --test-inventory "$TEST_INVENTORY" \
  > "$PROPOSAL_TMP"

# Canonicalize JSON to make retries byte-stable while preserving array order.
jq -S . "$PROPOSAL_TMP" > "$PROPOSAL_TMP.normalized"
mv -- "$PROPOSAL_TMP.normalized" "$PROPOSAL_TMP"

if [[ -e "$PROPOSAL_FILE" ]] && ! cmp -s -- "$PROPOSAL_TMP" "$PROPOSAL_FILE"; then
  echo "proposal output already exists with different content; refusing to overwrite: $PROPOSAL_FILE" >&2
  exit 73
fi

"$PYTHON" "$PROJECT_ROOT/orchestration/validate_feedback_proposal.py" \
  --summary "$SUMMARY_FILE" \
  --proposal "$PROPOSAL_TMP" \
  --candidate-root "$CANDIDATE_ROOT"

if [[ -e "$PROPOSAL_FILE" ]]; then
  rm -f -- "$PROPOSAL_TMP"
else
  mv -- "$PROPOSAL_TMP" "$PROPOSAL_FILE"
fi

echo "candidate proposal validated and staged for human review; active rules were not changed"
