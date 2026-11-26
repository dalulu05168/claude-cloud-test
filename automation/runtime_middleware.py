"""Westcott protocol validation, state gating, artifact verification and routing.

The middleware is transport-neutral. Register one callable per approved agent ID;
those adapters may invoke Claude Code, another agent runtime, or a local fake in tests.
Sub-agents never write the run state directly.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping
from urllib.parse import unquote, urlsplit

from jsonschema import Draft202012Validator, FormatChecker

LOG = logging.getLogger("westcott.runtime")

DEFAULT_ROUTES = {
    "research": "research-agent",
    "writing": "writing-agent",
    "editing": "editing-agent",
    "validation": "validation-agent",
    "evaluation": "feedback-evaluator",
    "docs": "docs-publisher",
}

IN_PROGRESS = {
    "research": "researching",
    "writing": "drafting",
    "editing": "editing",
    "validation": "validating",
    "evaluation": "evaluating",
    "docs": "docs_saving",
}
SUCCESS = {
    "research": "researched",
    "writing": "drafted",
    "editing": "edited",
    "validation": "ready_for_docs",
    "evaluation": "evaluated",
    "docs": "docs_saved",
}
PREREQUISITES = {
    "research": {"created"},
    "writing": {"researched"},
    "editing": {"drafted"},
    "validation": {"edited"},
    "evaluation": {"created"},
    "docs": {"ready_for_docs"},
}
BACKTRACKS = {
    "research": set(),
    "writing": {"research"},
    "editing": {"research", "writing"},
    "validation": {"research", "writing", "editing"},
    "evaluation": set(),
    "docs": {"validation"},
}
TASK_TYPES = {
    "research": "collect_and_verify",
    "writing": "draft_content",
    "editing": "edit_content",
    "validation": "validate_content",
    "evaluation": "evaluate_feedback",
    "docs": "publish_google_doc",
}
EVENT_STATUS = {
    "running": "running",
    "succeeded": "passed",
    "blocked": "blocked",
    "failed": "error",
}

class MiddlewareError(RuntimeError):
    """Base class for safe protocol/middleware failures."""

class ProtocolValidationError(MiddlewareError):
    pass

class StateTransitionError(MiddlewareError):
    pass

class DuplicateInProgress(MiddlewareError):
    pass

AgentHandler = Callable[[dict[str, Any]], Mapping[str, Any]]
ArtifactResolver = Callable[[str], bytes]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ProtocolMiddleware:
    """Validate, claim, route, validate the reply, and persist state atomically.

    `handlers` are the only callable destinations. A request cannot choose an
    arbitrary Python module, command, URL, or shell command as its recipient.
    Idempotency keys must be unique per *attempt* (for example `run:editing:2`).
    """

    def __init__(
        self,
        schema_path: str | Path,
        state_db: str | Path,
        state_dir: str | Path,
        handlers: Mapping[str, AgentHandler],
        *,
        routes: Mapping[str, str] | None = None,
        artifact_resolver: ArtifactResolver | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.schema_path = Path(schema_path)
        self.state_db = Path(state_db)
        self.state_dir = Path(state_dir).resolve()
        self.handlers = dict(handlers)
        self.routes = dict(routes or DEFAULT_ROUTES)
        self.artifact_resolver = artifact_resolver
        self.log = logger or LOG
        self._lock = threading.RLock()

        schema = json.loads(self.schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        self.validator = Draft202012Validator(schema, format_checker=FormatChecker())
        unknown = sorted(set(self.routes.values()) - set(self.handlers))
        if unknown:
            raise ValueError(f"route table references unregistered handlers: {unknown}")
        self.state_db.parent.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()
        try:
            os.chmod(self.state_db, 0o600)
        except OSError:
            self.log.warning("could not restrict SQLite state DB permissions path=%s", self.state_db)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.state_db, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @contextmanager
    def _database(self):
        db = self._connect()
        try:
            yield db
        except Exception:
            if db.in_transaction:
                db.rollback()
            raise
        finally:
            db.close()

    def _init_db(self) -> None:
        with self._database() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY, task_date TEXT NOT NULL, timezone TEXT NOT NULL,
                    ruleset_version TEXT NOT NULL, state TEXT NOT NULL, current_stage TEXT NOT NULL,
                    attempts_json TEXT NOT NULL, limits_json TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    run_id TEXT NOT NULL, idempotency_key TEXT NOT NULL, message_id TEXT NOT NULL,
                    stage TEXT NOT NULL, attempt INTEGER NOT NULL, max_attempts INTEGER NOT NULL,
                    status TEXT NOT NULL, result_json TEXT, request_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, idempotency_key), UNIQUE(message_id),
                    FOREIGN KEY(run_id) REFERENCES runs(run_id)
                );
                CREATE TABLE IF NOT EXISTS events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                    event_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES runs(run_id)
                );
            """)

    def validate_message(self, message: Mapping[str, Any], expected_type: str | None = None) -> None:
        errors = sorted(self.validator.iter_errors(dict(message)), key=lambda e: list(map(str, e.absolute_path)))
        if errors:
            details = "; ".join(
                f"/{'/'.join(map(str, err.absolute_path)) or ''}: {err.message}" for err in errors[:8]
            )
            raise ProtocolValidationError(f"message does not match protocol schema: {details}")
        if expected_type and message.get("message_type") != expected_type:
            raise ProtocolValidationError(f"expected message_type={expected_type}")

    def dispatch(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Route a valid task request to its allow-listed handler and return a result."""
        req = dict(request)
        self.validate_message(req, "task.request")
        stage = req["stage"]
        if req["task_type"] != TASK_TYPES[stage]:
            raise ProtocolValidationError("task_type does not match the configured stage route")
        if self.routes.get(stage) != req["recipient"]:
            raise ProtocolValidationError(f"recipient is not allow-listed for stage={stage}")
        self._verify_artifacts(req["input_artifacts"], req["run_id"])

        with self._lock:
            cached = self._claim(req)
            if cached is not None:
                return cached

        started_at = utc_now()
        try:
            raw_body = self.handlers[req["recipient"]](req)
            if not isinstance(raw_body, Mapping):
                raise ProtocolValidationError("agent handler must return a JSON object")
            result = self._build_result(req, raw_body, started_at)
            self.validate_message(result, "task.result")
            self._check_reply_correlation(req, result)
            self._verify_artifacts(result["output_artifacts"], req["run_id"])
            expected = set(req["expected_output"]["artifact_kinds"])
            actual = {item["kind"] for item in result["output_artifacts"]}
            if result["status"] == "succeeded" and not actual.issubset(expected):
                raise ProtocolValidationError("handler returned an artifact kind outside expected_output")
            if result["status"] == "succeeded" and not expected.issubset(actual):
                raise ProtocolValidationError("handler omitted one or more required output artifact kinds")
        except Exception as exc:  # contain adapter failures; never expose raw exception text in protocol
            self.log.exception("agent dispatch failed run_id=%s stage=%s", req["run_id"], stage)
            result = self._failure_result(req, started_at, exc)

        self.validate_message(result, "task.result")
        self._check_reply_correlation(req, result)
        self._finish(req, result)
        return result

    def _build_result(self, req: dict[str, Any], body: Mapping[str, Any], started_at: str) -> dict[str, Any]:
        allowed = {"status", "summary", "output_artifacts", "issues", "next_action", "error", "metrics", "extensions"}
        extras = set(body) - allowed
        if extras:
            raise ProtocolValidationError(f"handler returned unsupported result fields: {sorted(extras)}")
        result = {
            "protocol_version": "1.0",
            "message_type": "task.result",
            "message_id": f"result-{uuid.uuid4().hex}",
            "in_reply_to": req["message_id"],
            "run_id": req["run_id"],
            "task_date": req["task_date"],
            "timezone": req["timezone"],
            "agent": req["recipient"],
            "stage": req["stage"],
            "ruleset_version": req["ruleset_version"],
            "status": body.get("status"),
            "summary": body.get("summary"),
            "output_artifacts": body.get("output_artifacts", []),
            "issues": body.get("issues", []),
            "next_action": body.get("next_action"),
            "started_at": started_at,
            "finished_at": utc_now(),
        }
        for key in ("error", "metrics", "extensions"):
            if key in body:
                result[key] = body[key]
        for key in ("correlation_id", "trace_id"):
            if key in req:
                result[key] = req[key]
        return result

    def _failure_result(self, req: dict[str, Any], started_at: str, exc: Exception) -> dict[str, Any]:
        retryable = req["attempt"] < req["max_attempts"]
        # Keep internals in restricted server logs; safe protocol output only exposes the class.
        error_type = "middleware.protocol_error" if isinstance(exc, MiddlewareError) else "agent.adapter_error"
        result = {
            "protocol_version": "1.0", "message_type": "task.result",
            "message_id": f"result-{uuid.uuid4().hex}", "in_reply_to": req["message_id"],
            "run_id": req["run_id"], "task_date": req["task_date"], "timezone": req["timezone"],
            "agent": req["recipient"], "stage": req["stage"], "ruleset_version": req["ruleset_version"],
            "status": "failed", "summary": "Task failed; inspect the protected runtime log using the run ID.",
            "output_artifacts": [], "issues": [], "next_action": "retry" if retryable else "escalate",
            "error": {
                "error_type": error_type,
                "retryable": retryable,
                "safe_message": "The task could not be completed. Inspect the protected runtime log using the run ID.",
            },
            "started_at": started_at, "finished_at": utc_now(),
        }
        for key in ("correlation_id", "trace_id"):
            if key in req:
                result[key] = req[key]
        return result

    def _check_reply_correlation(self, req: dict[str, Any], result: dict[str, Any]) -> None:
        for key in ("run_id", "task_date", "timezone", "stage", "ruleset_version"):
            if result.get(key) != req.get(key):
                raise ProtocolValidationError(f"reply correlation mismatch: {key}")
        if result.get("in_reply_to") != req["message_id"] or result.get("agent") != req["recipient"]:
            raise ProtocolValidationError("reply does not match request message_id / recipient")

    def _verify_artifacts(self, artifacts: list[dict[str, Any]], run_id: str) -> None:
        for artifact in artifacts:
            uri = artifact["uri"]
            parts = urlsplit(uri)
            if parts.scheme == "state" and parts.netloc == "runs":
                rel_parts = [p for p in PurePosixPath(unquote(parts.path)).parts if p not in ("/", "", ".")]
                if not rel_parts or rel_parts[0] != run_id or any(p == ".." for p in rel_parts):
                    raise ProtocolValidationError("state artifact URI must be confined to this run")
                root = (self.state_dir / "runs" / run_id).resolve()
                path = (root.joinpath(*rel_parts[1:])).resolve()
                try:
                    path.relative_to(root)
                except ValueError as exc:
                    raise ProtocolValidationError("artifact path escapes run directory") from exc
                if not path.is_file():
                    raise ProtocolValidationError("referenced state artifact does not exist")
                data = path.read_bytes()
            else:
                if self.artifact_resolver is None:
                    raise ProtocolValidationError("non-state artifact URI requires an explicit resolver")
                data = self.artifact_resolver(uri)
                if not isinstance(data, bytes):
                    raise ProtocolValidationError("artifact resolver must return bytes")
            digest = hashlib.sha256(data).hexdigest()
            if digest.lower() != artifact["sha256"].lower():
                raise ProtocolValidationError("artifact SHA-256 mismatch")
            if len(data) != artifact["size_bytes"]:
                raise ProtocolValidationError("artifact size_bytes mismatch")

    def _claim(self, req: dict[str, Any]) -> dict[str, Any] | None:
        now = utc_now()
        stage = req["stage"]
        with self._database() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT status,result_json FROM tasks WHERE run_id=? AND idempotency_key=?",
                (req["run_id"], req["idempotency_key"]),
            ).fetchone()
            if existing:
                if existing["status"] == "running":
                    raise DuplicateInProgress("an identical idempotent request is already running")
                db.execute("COMMIT")
                return json.loads(existing["result_json"])
            if db.execute("SELECT 1 FROM tasks WHERE message_id=?", (req["message_id"],)).fetchone():
                raise ProtocolValidationError("message_id was already used with a different idempotency key")

            row = db.execute("SELECT * FROM runs WHERE run_id=?", (req["run_id"],)).fetchone()
            if row is None:
                if req["attempt"] != 1 or req["stage"] not in ("research", "evaluation"):
                    raise StateTransitionError("new run must begin at research or evaluation attempt 1")
                attempts = {name: 0 for name in self.routes}
                limits = {}
                current_state = "created"
                current_stage = stage
                db.execute(
                    "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (req["run_id"], req["task_date"], req["timezone"], req["ruleset_version"],
                     current_state, current_stage, _json(attempts), _json(limits), now, now),
                )
                from_state = current_state
            else:
                if (row["task_date"], row["timezone"], row["ruleset_version"]) != (
                    req["task_date"], req["timezone"], req["ruleset_version"]
                ):
                    raise ProtocolValidationError("run_id reused with a different date, timezone, or ruleset_version")
                attempts, limits = json.loads(row["attempts_json"]), json.loads(row["limits_json"])
                current_state, current_stage = row["state"], row["current_stage"]
                from_state = current_state

            previous_attempt = attempts.get(stage, 0)
            prior_limit = limits.get(stage)
            if req["attempt"] > req["max_attempts"]:
                raise StateTransitionError("attempt exceeds max_attempts")
            if prior_limit is not None and prior_limit != req["max_attempts"]:
                raise StateTransitionError("max_attempts cannot change within a run stage")

            is_recovery = current_state in {"blocked", "error"}
            if is_recovery:
                if current_state == "error" and stage != current_stage:
                    raise StateTransitionError("error recovery may retry only the failed stage")
                if current_state == "blocked" and stage != current_stage and stage not in BACKTRACKS[current_stage]:
                    raise StateTransitionError("blocked recovery must retry the stage or an approved upstream stage")
                if req["attempt"] != previous_attempt + 1:
                    raise StateTransitionError("recovery attempt must increment by exactly one for this stage")
            else:
                if current_state not in PREREQUISITES[stage]:
                    raise StateTransitionError(f"stage={stage} is not allowed from state={current_state}")
                if req["attempt"] != previous_attempt + 1:
                    raise StateTransitionError("attempt must increment by exactly one for this stage")

            attempts[stage] = req["attempt"]
            limits[stage] = req["max_attempts"]
            db.execute(
                "UPDATE runs SET state=?,current_stage=?,attempts_json=?,limits_json=?,updated_at=? WHERE run_id=?",
                (IN_PROGRESS[stage], stage, _json(attempts), _json(limits), now, req["run_id"]),
            )
            db.execute(
                "INSERT INTO tasks(run_id,idempotency_key,message_id,stage,attempt,max_attempts,status,result_json,request_json) VALUES (?,?,?,?,?,?,?,?,?)",
                (req["run_id"], req["idempotency_key"], req["message_id"], stage, req["attempt"],
                 req["max_attempts"], "running", None, _json(req)),
            )
            event = self._event(req, "task.started", "running", from_state, IN_PROGRESS[stage])
            self.validate_message(event, "state.event")
            db.execute("INSERT INTO events(run_id,event_json,created_at) VALUES (?,?,?)", (req["run_id"], _json(event), now))
            db.execute("COMMIT")
        return None

    def _finish(self, req: dict[str, Any], result: dict[str, Any]) -> None:
        stage = req["stage"]
        status = result["status"]
        if status == "succeeded":
            to_state = SUCCESS[stage]
            event_type = "task.succeeded"
            event_status = "passed"
        elif status == "blocked":
            to_state = "blocked"
            event_type = "task.blocked"
            event_status = "blocked"
        else:
            to_state = "error"
            event_type = "task.failed"
            event_status = "error"
        now = utc_now()
        with self._database() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM runs WHERE run_id=?", (req["run_id"],)).fetchone()
            from_state = row["state"] if row else IN_PROGRESS[stage]
            db.execute(
                "UPDATE runs SET state=?,current_stage=?,updated_at=? WHERE run_id=?",
                (to_state, stage, now, req["run_id"]),
            )
            db.execute(
                "UPDATE tasks SET status=?,result_json=? WHERE run_id=? AND idempotency_key=?",
                (status, _json(result), req["run_id"], req["idempotency_key"]),
            )
            event = self._event(req, event_type, event_status, from_state, to_state)
            self.validate_message(event, "state.event")
            db.execute("INSERT INTO events(run_id,event_json,created_at) VALUES (?,?,?)", (req["run_id"], _json(event), now))
            if status == "succeeded":
                changed = self._event(req, "stage.changed", "passed", from_state, to_state)
                self.validate_message(changed, "state.event")
                db.execute("INSERT INTO events(run_id,event_json,created_at) VALUES (?,?,?)", (req["run_id"], _json(changed), now))
            db.execute("COMMIT")

    @staticmethod
    def _event(req: dict[str, Any], event_type: str, status: str, from_state: str, to_state: str) -> dict[str, Any]:
        event = {
            "protocol_version": "1.0", "message_type": "state.event",
            "message_id": f"evt-{uuid.uuid4().hex}", "run_id": req["run_id"],
            "task_date": req["task_date"], "timestamp": utc_now(), "actor": "westcott-orchestrator",
            "event_type": event_type, "stage": req["stage"], "status": status,
            "from_state": from_state, "to_state": to_state, "attempt": req["attempt"],
            "correlation_id": req.get("correlation_id", req["run_id"]),
        }
        return event

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        """Return the current persistent state and attempts for operations/monitoring."""
        with self._database() as db:
            row = db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            return None
        return {
            "run_id": row["run_id"], "task_date": row["task_date"], "timezone": row["timezone"],
            "ruleset_version": row["ruleset_version"], "state": row["state"],
            "current_stage": row["current_stage"], "attempts": json.loads(row["attempts_json"]),
            "updated_at": row["updated_at"],
        }

    def list_events(self, run_id: str) -> list[dict[str, Any]]:
        with self._database() as db:
            rows = db.execute("SELECT event_json FROM events WHERE run_id=? ORDER BY event_id", (run_id,)).fetchall()
        return [json.loads(row["event_json"]) for row in rows]
