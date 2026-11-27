#!/usr/bin/env bash
# Westcott daily content pipeline launcher.
# Runner and Google Docs publisher are explicit adapters owned by the deployment.
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
ENV_FILE="${WESTCOTT_ENV_FILE:-$PROJECT_ROOT/automation/daily.env}"
BOOT_LOG_DIR="$PROJECT_ROOT/logs"
mkdir -p "$BOOT_LOG_DIR"
LOG_FILE="$BOOT_LOG_DIR/run-daily-bootstrap.log"
exec >>"$LOG_FILE" 2>&1

CURRENT_STAGE="bootstrap"
RUN_ID="bootstrap"
RUN_DATE="unknown"
STATE_DIR="${WESTCOTT_STATE_DIR:-$PROJECT_ROOT/.westcott-state}"
EVENT_LOG="$STATE_DIR/logs/run-events.jsonl"

log() {
  local level="$1"; shift
  printf '%s level=%s stage=%s run_id=%s %s\n' \
    "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$level" "$CURRENT_STAGE" "$RUN_ID" "$*"
}

record_event() {
  local event_type="$1" status="$2"
  [[ -n "${EVENT_LOG:-}" ]] || return 0
  command -v jq >/dev/null 2>&1 || return 0
  mkdir -p "$(dirname -- "$EVENT_LOG")" 2>/dev/null || return 0
  jq -cn \
    --arg timestamp "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
    --arg event_id "${RUN_ID}-${event_type}-$(date -u '+%s%N')" \
    --arg run_id "$RUN_ID" \
    --arg task_date "$RUN_DATE" \
    --arg stage "$CURRENT_STAGE" \
    --arg event_type "$event_type" \
    --arg status "$status" \
    '{protocol_version:"1.0",timestamp:$timestamp,event_id:$event_id,run_id:$run_id,task_date:$task_date,stage:$stage,event_type:$event_type,status:$status}' \
    >> "$EVENT_LOG" 2>/dev/null || true
}

notify_error() {
  local exit_code="$1"
  local webhook="${WESTCOTT_ERROR_WEBHOOK_URL:-}"
  if [[ -z "$webhook" ]]; then
    log WARN "error notification skipped: WESTCOTT_ERROR_WEBHOOK_URL is not configured"
    return 0
  fi
  if ! command -v jq >/dev/null 2>&1 || ! command -v curl >/dev/null 2>&1; then
    log ERROR "error notification unavailable: jq and curl are required"
    return 0
  fi
  local text payload
  text="Westcott daily run failed: run_id=$RUN_ID date=$RUN_DATE stage=$CURRENT_STAGE exit=$exit_code log=$LOG_FILE"
  payload="$(jq -cn --arg text "$text" '{text:$text}')" || {
    log ERROR "could not encode notification payload"
    return 0
  }
  if ! curl --silent --show-error --fail --max-time 15 \
      -H 'Content-Type: application/json' \
      --data-binary "$payload" "$webhook" >/dev/null 2>&1; then
    # Deliberately do not print the webhook URL or response body; both may contain secrets.
    log ERROR "error notification delivery failed"
  else
    log INFO "error notification sent"
  fi
}

record_failure_event() {
  if [[ "$CURRENT_STAGE" == "google_docs_publish" ]]; then
    record_event "docs_delivery_pending" "delivery_pending"
  else
    record_event "run_failed" "error"
  fi
}

handle_error() {
  local exit_code="$1" line_number="$2"
  trap - ERR
  log ERROR "command failed at script_line=$line_number exit=$exit_code"
  record_failure_event
  notify_error "$exit_code"
  exit "$exit_code"
}

fatal() {
  local exit_code="$1" message="$2"
  log ERROR "$message"
  record_failure_event
  notify_error "$exit_code"
  exit "$exit_code"
}

handle_signal() {
  local signal_name="$1" exit_code="$2"
  trap - ERR
  CURRENT_STAGE="signal"
  log ERROR "received signal=$signal_name exit=$exit_code"
  record_event "run_interrupted" "error"
  notify_error "$exit_code"
  exit "$exit_code"
}

trap 'handle_error "$?" "$LINENO"' ERR
trap 'handle_signal TERM 143' TERM
trap 'handle_signal INT 130' INT

if [[ ! -r "$ENV_FILE" ]]; then
  fatal 78 "trusted environment file is missing or unreadable: $ENV_FILE"
fi

CURRENT_STAGE="load_environment"
# This file is shell code, not an untrusted dotenv file. Keep it owner-controlled and mode 0600.
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

WESTCOTT_TIMEZONE="${WESTCOTT_TIMEZONE:-Europe/Bucharest}"
export TZ="$WESTCOTT_TIMEZONE"
RUN_DATE="${1:-$(date '+%F')}"
if [[ ! "$RUN_DATE" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
  fatal 64 "date argument must use YYYY-MM-DD"
fi
if ! date -d "$RUN_DATE" '+%F' >/dev/null 2>&1; then
  fatal 64 "date argument is not a valid calendar date"
fi
RUN_ID="daily-$RUN_DATE"
STATE_DIR="${WESTCOTT_STATE_DIR:-$PROJECT_ROOT/.westcott-state}"
LOG_DIR="${WESTCOTT_LOG_DIR:-$STATE_DIR/logs}"
mkdir -p "$LOG_DIR" "$STATE_DIR/runs" "$STATE_DIR/locks"
LOG_FILE="$LOG_DIR/run-daily-$RUN_DATE.log"
exec >>"$LOG_FILE" 2>&1
EVENT_LOG="$LOG_DIR/run-events.jsonl"

CURRENT_STAGE="preflight"
if ! command -v jq >/dev/null 2>&1; then fatal 69 "required command not found: jq"; fi
if ! command -v flock >/dev/null 2>&1; then fatal 69 "required command not found: flock"; fi

WESTCOTT_ORCHESTRATOR_AGENT="${WESTCOTT_ORCHESTRATOR_AGENT:-westcott-orchestrator}"
WESTCOTT_AGENT_RUNNER="${WESTCOTT_AGENT_RUNNER:-}"
WESTCOTT_DOCS_PUBLISHER="${WESTCOTT_DOCS_PUBLISHER:-}"
WESTCOTT_GOOGLE_DRIVE_FOLDER_ID="${WESTCOTT_GOOGLE_DRIVE_FOLDER_ID:-}"
WESTCOTT_DOC_TITLE_PREFIX="${WESTCOTT_DOC_TITLE_PREFIX:-Westcott Daily Rhythm}"

if [[ -z "$WESTCOTT_AGENT_RUNNER" || ! -x "$WESTCOTT_AGENT_RUNNER" ]]; then
  fatal 69 "WESTCOTT_AGENT_RUNNER must name an executable orchestrator adapter"
fi
RUN_DIR="$STATE_DIR/runs/$RUN_ID"
mkdir -p "$RUN_DIR"
LOCK_FILE="$STATE_DIR/locks/daily.lock"
exec {LOCK_FD}>"$LOCK_FILE"
if ! flock -n "$LOCK_FD"; then
  log WARN "another daily run holds the lock; this invocation exits without starting a duplicate"
  exit 0
fi

DRAFT_FILE="$RUN_DIR/draft.md"
MANIFEST_FILE="$RUN_DIR/manifest.json"
DOCS_RESULT="$RUN_DIR/google-docs.json"
DOCS_RESULT_TMP="$RUN_DIR/google-docs.json.tmp.$$"
cleanup() {
  [[ -n "${DOCS_RESULT_TMP:-}" ]] && rm -f -- "$DOCS_RESULT_TMP"
}
trap cleanup EXIT

manifest_is_ready() {
  [[ -s "$MANIFEST_FILE" && -s "$DRAFT_FILE" ]] || return 1
  jq -e --arg run_id "$RUN_ID" --arg task_date "$RUN_DATE" \
    '.run_id == $run_id and .task_date == $task_date and .status == "ready_for_docs" and .validation.status == "passed"' \
    "$MANIFEST_FILE" >/dev/null 2>&1
}

manifest_is_draft_pending_review() {
  [[ -s "$MANIFEST_FILE" && -s "$DRAFT_FILE" ]] || return 1
  jq -e --arg run_id "$RUN_ID" --arg task_date "$RUN_DATE" \
    '.run_id == $run_id and .task_date == $task_date and .status == "drafted" and (.validation.status == "pending_review" or .validation.status == "blocked")' \
    "$MANIFEST_FILE" >/dev/null 2>&1
}

docs_result_is_saved() {
  [[ -s "$DOCS_RESULT" ]] || return 1
  jq -e --arg run_id "$RUN_ID" \
    '.run_id == $run_id and .status == "saved" and ((.document_id | type) == "string") and ((.url | type) == "string") and (.url | startswith("https://docs.google.com/document/d/"))' \
    "$DOCS_RESULT" >/dev/null 2>&1
}

if docs_result_is_saved; then
  log INFO "Google Doc already exists for this run; idempotent no-op url=$(jq -r '.url' "$DOCS_RESULT")"
  exit 0
fi

finish_pending_review() {
  local validation_status
  validation_status="$(jq -r '.validation.status' "$MANIFEST_FILE")"
  record_event "draft_pending_review" "$validation_status"
  log INFO "draft saved for review; Google Docs publishing skipped validation=$validation_status path=$DRAFT_FILE"
  exit 0
}

finish_waiting_for_approval() {
  record_event "docs_approval_pending" "pending_review"
  log INFO "validated draft retained for human approval: $1"
  exit 0
}

review_with_codex() {
  case "${WESTCOTT_CODEX_REVIEW,,}" in
    false|0|no) return 0 ;;
    true|1|yes) ;;
    *) log ERROR "WESTCOTT_CODEX_REVIEW must be true or false"; return 2 ;;
  esac
  CURRENT_STAGE="codex_review"
  if "${WESTCOTT_PYTHON:-python3}" "$SCRIPT_DIR/codex_handoff.py" --run-dir "$RUN_DIR" >/dev/null; then
    record_event "codex_review_completed" "ready_for_human_review"
    return 0
  fi
  record_event "codex_review_pending" "pending_review"
  log INFO "Codex review needs evidence or is unavailable; draft retained for review"
  return 2
}

record_event "daily_run_started" "running"
if [[ -n "${WESTCOTT_HISTORY_DOC_ID:-}" ]]; then
  CURRENT_STAGE="history_refresh"
  if "${WESTCOTT_PYTHON:-python3}" "$SCRIPT_DIR/sync_docs_history.py" \
      --document-id "$WESTCOTT_HISTORY_DOC_ID" --cache-dir "$STATE_DIR"; then
    record_event "history_refreshed" "passed"
  else
    record_event "history_refresh_pending" "pending_review"
    log WARN "history refresh unavailable; cached dates may be out of date"
  fi
fi
AUTO_PUBLISH_SETTING="${WESTCOTT_AUTO_PUBLISH:-false}"
AUTO_PUBLISH_SETTING="${AUTO_PUBLISH_SETTING,,}"
WESTCOTT_CODEX_REVIEW="${WESTCOTT_CODEX_REVIEW:-false}"
if manifest_is_draft_pending_review \
  && [[ "$AUTO_PUBLISH_SETTING" != "true" && "$AUTO_PUBLISH_SETTING" != "1" && "$AUTO_PUBLISH_SETTING" != "yes" ]]; then
  log INFO "existing candidate retained for review"
elif ! manifest_is_ready; then
  CURRENT_STAGE="orchestrator"
  log INFO "starting orchestrator agent=$WESTCOTT_ORCHESTRATOR_AGENT"
  "$WESTCOTT_AGENT_RUNNER" \
    --agent "$WESTCOTT_ORCHESTRATOR_AGENT" \
    --task daily-rhythm \
    --date "$RUN_DATE" \
    --timezone "$WESTCOTT_TIMEZONE" \
    --run-id "$RUN_ID" \
    --run-dir "$RUN_DIR" \
    --draft "$DRAFT_FILE" \
    --manifest "$MANIFEST_FILE"
fi

if manifest_is_draft_pending_review; then
  review_with_codex || true
  finish_pending_review
fi

if ! manifest_is_ready; then
  fatal 65 "orchestrator did not produce a validated draft and ready_for_docs manifest"
fi

if ! review_with_codex; then
  finish_waiting_for_approval "Codex review needs evidence or is unavailable"
fi

CURRENT_STAGE="docs_approval"
case "$AUTO_PUBLISH_SETTING" in
  true|1|yes) ;;
  *) finish_waiting_for_approval "Docs publishing is disabled" ;;
esac
if [[ -z "${WESTCOTT_APPROVAL_DIR:-}" ]]; then
  finish_waiting_for_approval "approval directory is not configured"
fi
APPROVAL_FILE="$WESTCOTT_APPROVAL_DIR/$RUN_ID.json"
if [[ ! -s "$APPROVAL_FILE" ]]; then
  finish_waiting_for_approval "no approval receipt for this run"
fi
publisher_args=(
  --input "$DRAFT_FILE"
  --run-id "$RUN_ID"
  --date "$RUN_DATE"
  --title "$WESTCOTT_DOC_TITLE_PREFIX $RUN_DATE"
  --folder-id "$WESTCOTT_GOOGLE_DRIVE_FOLDER_ID"
  --manifest "$MANIFEST_FILE"
  --approval "$APPROVAL_FILE"
)
if ! "${WESTCOTT_PYTHON:-python3}" "$SCRIPT_DIR/sync_google_doc.py" --check-only "${publisher_args[@]}"; then
  finish_waiting_for_approval "approval or artifact integrity check failed"
fi

CURRENT_STAGE="google_docs_publish"
if [[ -z "$WESTCOTT_DOCS_PUBLISHER" || ! -x "$WESTCOTT_DOCS_PUBLISHER" ]]; then
  fatal 69 "WESTCOTT_DOCS_PUBLISHER must name an executable Google Docs publisher adapter before publishing"
fi
if [[ -z "$WESTCOTT_GOOGLE_DRIVE_FOLDER_ID" || "$WESTCOTT_GOOGLE_DRIVE_FOLDER_ID" == "replace-with-authorized-folder-id" ]]; then
  fatal 78 "a valid WESTCOTT_GOOGLE_DRIVE_FOLDER_ID is required before publishing"
fi
log INFO "publishing validated draft to Google Docs folder_id=$WESTCOTT_GOOGLE_DRIVE_FOLDER_ID"
rm -f -- "$DOCS_RESULT_TMP"
"$WESTCOTT_DOCS_PUBLISHER" "${publisher_args[@]}" > "$DOCS_RESULT_TMP"

if ! jq -e --arg run_id "$RUN_ID" \
    '.run_id == $run_id and .status == "saved" and ((.document_id | type) == "string") and ((.url | type) == "string") and (.url | startswith("https://docs.google.com/document/d/"))' \
    "$DOCS_RESULT_TMP" >/dev/null 2>&1; then
  fatal 65 "Google Docs publisher returned an invalid result; expected run_id, status=saved, document_id, and docs URL"
fi
mv -f -- "$DOCS_RESULT_TMP" "$DOCS_RESULT"
record_event "google_doc_saved" "passed"
CURRENT_STAGE="complete"
log INFO "daily content saved to Google Docs url=$(jq -r '.url' "$DOCS_RESULT")"
exit 0
