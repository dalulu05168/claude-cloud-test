from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from runtime_middleware import (
    DEFAULT_ROUTES,
    ProtocolMiddleware,
    ProtocolValidationError,
    StateTransitionError,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "docs" / "multi-agent-protocol.schema.json"
RUN_ID = "daily-2030-01-02"


def timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class RuntimeMiddlewareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state = self.root / ".westcott-state"
        self.calls = []
        self.middleware = ProtocolMiddleware(
            SCHEMA,
            self.root / "runtime.sqlite3",
            self.state,
            {
                "research-agent": self.success_handler,
                "writing-agent": self.success_handler,
                "editing-agent": self.success_handler,
                "validation-agent": self.success_handler,
                "feedback-evaluator": self.success_handler,
                "docs-publisher": self.success_handler,
            },
        )

    def tearDown(self):
        self.temp.cleanup()

    def success_handler(self, request):
        self.calls.append(request["message_id"])
        return self.make_success_body(request)

    def make_success_body(self, request):
        run_dir = self.state / "runs" / request["run_id"]
        run_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{request['stage']}-{request['attempt']}.json"
        path = run_dir / filename
        payload = json.dumps({"ok": True, "stage": request["stage"]}).encode()
        path.write_bytes(payload)
        kind = request["expected_output"]["artifact_kinds"][0]
        return {
            "status": "succeeded",
            "summary": f"fake {request['stage']} complete",
            "output_artifacts": [{
                "artifact_id": f"{request['stage']}-{request['attempt']}-artifact",
                "kind": kind,
                "uri": f"state://runs/{request['run_id']}/{filename}",
                "media_type": "application/json",
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size_bytes": len(payload),
                "schema_id": "test-v1",
                "created_at": timestamp(),
                "created_by": request["recipient"],
            }],
            "issues": [],
            "next_action": "continue",
        }

    def request(self, stage="research", attempt=1, *, recipient=None, run_id=RUN_ID, key=None):
        kind_by_stage = {
            "research": "research_packet", "writing": "draft", "editing": "edit_report",
            "validation": "validation_report", "docs": "google_doc_record",
            "evaluation": "feedback_record",
        }
        task_type = {
            "research": "collect_and_verify", "writing": "draft_content", "editing": "edit_content",
            "validation": "validate_content", "docs": "publish_google_doc",
            "evaluation": "evaluate_feedback",
        }[stage]
        return {
            "protocol_version": "1.0",
            "message_type": "task.request",
            "message_id": f"{run_id}-{stage}-{attempt}-request",
            "correlation_id": run_id,
            "trace_id": f"trace-{run_id}",
            "run_id": run_id,
            "task_date": "2030-01-02",
            "timezone": "Asia/Shanghai",
            "sender": "westcott-orchestrator",
            "recipient": recipient or DEFAULT_ROUTES[stage],
            "task_type": task_type,
            "stage": stage,
            "ruleset_version": "2026.11.1",
            "created_at": timestamp(),
            "attempt": attempt,
            "max_attempts": 3,
            "idempotency_key": key or f"{run_id}:{stage}:{attempt}",
            "input_artifacts": [],
            "constraints": {"output_language": "zh-CN", "human_review_required": True},
            "expected_output": {"schema_id": "test-v1", "artifact_kinds": [kind_by_stage[stage]], "require_structured_result": True},
            "instructions": "Perform the assigned stage using only supplied artifacts and constraints.",
        }

    def test_schema_and_allowlisted_research_dispatch(self):
        result = self.middleware.dispatch(self.request())
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["in_reply_to"], "daily-2030-01-02-research-1-request")
        self.assertEqual(self.middleware.get_run(RUN_ID)["state"], "researched")
        self.assertEqual(len(self.calls), 1)
        for event in self.middleware.list_events(RUN_ID):
            self.middleware.validate_message(event, "state.event")

    def test_duplicate_idempotency_key_returns_cached_result(self):
        request = self.request()
        first = self.middleware.dispatch(request)
        second = self.middleware.dispatch(request)
        self.assertEqual(first, second)
        self.assertEqual(len(self.calls), 1)

    def test_idempotency_key_rejects_changed_artifact_bytes(self):
        request = self.request()
        path = self.state / "runs" / RUN_ID / "input.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        original = b"revision 1"
        path.write_bytes(original)
        artifact = {
            "artifact_id": "input-1", "kind": "content_calendar",
            "uri": f"state://runs/{RUN_ID}/input.json", "media_type": "application/json",
            "sha256": hashlib.sha256(original).hexdigest(), "size_bytes": len(original),
            "schema_id": "test-v1", "created_at": timestamp(), "created_by": "westcott-orchestrator",
        }
        request["input_artifacts"] = [artifact]
        first = self.middleware.dispatch(request)

        revised = b"revision 2"
        path.write_bytes(revised)
        changed = {**request, "input_artifacts": [{**artifact, "sha256": hashlib.sha256(revised).hexdigest()}]}
        with self.assertRaisesRegex(ProtocolValidationError, "idempotency key"):
            self.middleware.dispatch(changed)
        self.assertEqual(len(self.calls), 1)

        path.write_bytes(original)
        self.assertEqual(self.middleware.dispatch(request), first)

    def test_idempotency_key_rejects_changed_message_id_or_deadline(self):
        request = self.request()
        self.middleware.dispatch(request)
        for field, value in {"message_id": "different-request", "deadline_at": "2030-01-02T08:30:00Z"}.items():
            with self.subTest(field=field):
                changed = {**request, field: value}
                with self.assertRaisesRegex(ProtocolValidationError, "idempotency key"):
                    self.middleware.dispatch(changed)
        self.assertEqual(len(self.calls), 1)

    def test_unregistered_recipient_is_rejected(self):
        request = self.request(recipient="shell-command-agent")
        with self.assertRaises(ProtocolValidationError):
            self.middleware.dispatch(request)
        self.assertEqual(self.calls, [])

    def test_out_of_order_stage_is_rejected(self):
        with self.assertRaises(StateTransitionError):
            self.middleware.dispatch(self.request(stage="writing"))
        self.assertIsNone(self.middleware.get_run(RUN_ID))

    def test_transition_research_to_writing(self):
        self.middleware.dispatch(self.request())
        result = self.middleware.dispatch(self.request(stage="writing"))
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(self.middleware.get_run(RUN_ID)["state"], "drafted")

    def test_blocked_stage_can_retry_once_with_new_attempt_and_key(self):
        def blocked_then_success(request):
            self.calls.append(request["message_id"])
            if request["attempt"] == 1:
                return {
                    "status": "blocked", "summary": "source needs clarification",
                    "output_artifacts": [],
                    "issues": [{"code": "SOURCE_REFERENCE_MISSING", "severity": "block", "message": "A source reference is missing."}],
                    "next_action": "revise",
                }
            return self.make_success_body(request)

        routes = {"research": "research-agent"}
        self.middleware = ProtocolMiddleware(
            SCHEMA, self.root / "retry.sqlite3", self.state,
            {"research-agent": blocked_then_success}, routes=routes,
        )
        first = self.middleware.dispatch(self.request())
        self.assertEqual(first["status"], "blocked")
        second = self.middleware.dispatch(self.request(attempt=2))
        self.assertEqual(second["status"], "succeeded")
        self.assertEqual(self.middleware.get_run(RUN_ID)["attempts"]["research"], 2)

    def test_artifact_hash_and_size_are_checked(self):
        request = self.request()
        path = self.state / "runs" / RUN_ID / "input.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = b"authoritative input"
        path.write_bytes(data)
        request["input_artifacts"] = [{
            "artifact_id": "input-1", "kind": "content_calendar",
            "uri": f"state://runs/{RUN_ID}/input.json", "media_type": "application/json",
            "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data),
            "schema_id": "calendar-v1", "created_at": timestamp(), "created_by": "westcott-orchestrator",
        }]
        request["input_artifacts"][0]["sha256"] = "0" * 64
        with self.assertRaises(ProtocolValidationError):
            self.middleware.dispatch(request)
        self.assertEqual(self.calls, [])

    def test_non_state_uri_requires_explicit_resolver(self):
        request = self.request()
        request["input_artifacts"] = [{
            "artifact_id": "remote-1", "kind": "content_calendar",
            "uri": "https://example.invalid/calendar.json", "media_type": "application/json",
            "sha256": "0" * 64, "size_bytes": 0, "schema_id": "calendar-v1",
            "created_at": timestamp(), "created_by": "westcott-orchestrator",
        }]
        with self.assertRaises(ProtocolValidationError):
            self.middleware.dispatch(request)


if __name__ == "__main__":
    unittest.main()
