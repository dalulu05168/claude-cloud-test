from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

import fact_validator
import fact_check_backend


class FactValidatorFrameworkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        zone = ZoneInfo("Asia/Shanghai")
        now = datetime.now(zone).replace(microsecond=0)
        self.task_date = now.date().isoformat()
        self.run_id = f"daily-{self.task_date}"
        self.draft_path = self.root / "draft.md"
        self.research_path = self.root / "research.json"
        self.draft_path.write_text(
            "===== 资讯助理 =====\n\n"
            "🇺🇸 美国财经新闻｜央行公布政策决定\n\n"
            "央行公布政策决定，并说明后续安排。\n\n"
            "市场影响仍需结合后续数据观察。\n\n"
            "配图：https://cdn.example.com/image.jpg\n",
            encoding="utf-8",
        )
        self.research_path.write_text(json.dumps({
            "schema_version": "westcott-daily-research-v1",
            "task_date": self.task_date,
            "market_scope": "美国市场",
            "source_allowlist": ["example.com"],
            "stories": [{
                "country": "美国",
                "title": "央行公布政策决定",
                "source_url": "https://www.example.com/news/1",
                "source_title": "央行公告",
                "published_at": (now - timedelta(minutes=10)).isoformat(),
                "image_url": "https://cdn.example.com/image.jpg",
                "verified_facts": ["政策决定已发布"],
            }],
        }, ensure_ascii=False), encoding="utf-8")
        self.args = SimpleNamespace(
            draft=str(self.draft_path),
            research=str(self.research_path),
            run_id=self.run_id,
            date=self.task_date,
            timezone="Asia/Shanghai",
            max_story_age_hours=48,
            ruleset_version="rules-v1",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_missing_api_credentials_fail_closed_without_receipt(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = [
            "--draft", str(self.draft_path), "--research", str(self.research_path),
            "--run-id", self.run_id, "--date", self.task_date,
            "--timezone", "Asia/Shanghai", "--max-story-age-hours", "48",
            "--ruleset-version", "rules-v1",
        ]
        with patch.dict(os.environ, {}, clear=True), \
             contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = fact_validator.main(argv)
        self.assertEqual(result, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("WESTCOTT_TAVILY_API_KEY is unavailable", stderr.getvalue())

    def test_success_receipt_matches_exact_runner_contract_and_file_hashes(self):
        with patch.object(fact_validator, "verify_story_claims", return_value=True):
            receipt = fact_validator.validate(self.args)
        expected = {
            "status": "passed",
            "run_id": self.run_id,
            "ruleset_version": "rules-v1",
            "draft_sha256": hashlib.sha256(self.draft_path.read_bytes()).hexdigest(),
            "research_sha256": hashlib.sha256(self.research_path.read_bytes()).hexdigest(),
        }
        self.assertEqual(receipt, expected)

    def test_windows_line_endings_are_parsed_and_hashed_as_supplied(self):
        normalized = self.draft_path.read_text(encoding="utf-8")
        windows_bytes = normalized.replace("\n", "\r\n").encode("utf-8")
        self.draft_path.write_bytes(windows_bytes)
        with patch.object(fact_validator, "verify_story_claims", return_value=True) as backend:
            receipt = fact_validator.validate(self.args)
        backend.assert_called_once()
        self.assertNotIn("\r", backend.call_args.kwargs["draft_text"])
        self.assertEqual(receipt["draft_sha256"], hashlib.sha256(windows_bytes).hexdigest())

    def test_source_outside_allowlist_is_rejected_without_backend_approval(self):
        research = json.loads(self.research_path.read_text(encoding="utf-8"))
        research["stories"][0]["source_url"] = "https://not-allowed.invalid/news"
        self.research_path.write_text(json.dumps(research), encoding="utf-8")
        with patch.object(fact_validator, "verify_story_claims", return_value=True) as backend:
            with self.assertRaisesRegex(fact_validator.ValidationFailure, "outside the source allowlist"):
                fact_validator.validate(self.args)
        backend.assert_not_called()


class LiveBackendLogicTests(unittest.TestCase):
    def setUp(self):
        self.story = {
            "country": "United States",
            "title": "Central bank announces policy decision",
            "source_url": "https://www.example.com/news/1",
            "source_title": "Central bank announcement",
            "published_at": "2030-04-05T07:30:00+08:00",
            "image_url": "https://cdn.example.com/image.jpg",
            "verified_facts": ["The central bank announced its policy."],
        }
        self.domains = ["example.com", "second.example"]
        self.draft_text = "The central bank held its rate unchanged."
        self.url_one = "https://www.example.com/news/1"
        self.url_two = "https://second.example/report/1"
        self.text_one = (
            "The central bank announced its policy. The central bank held its rate unchanged. "
            "The report provides an official account of the decision."
        )
        self.text_two = (
            "The central bank's announcement described its policy. The bank held its benchmark rate unchanged. "
            "This separate report confirms the public decision."
        )
        self.search_response = {"results": [
            {"url": self.url_one, "title": "Primary source", "raw_content": self.text_one,
             "published_date": "2030-04-05"},
            {"url": self.url_two, "title": "Independent report", "raw_content": self.text_two,
             "published_date": "2030-04-05"},
        ]}

    def _model_response(self, *, verdict="supported", quote_override=None, omit_draft=False,
                        evidence_urls=None, claim_override=None):
        urls = evidence_urls or [self.url_one, self.url_two]
        fact_quotes = [
            "The central bank announced its policy.",
            "The central bank's announcement described its policy.",
        ]
        draft_quotes = [
            quote_override or "The central bank held its rate unchanged.",
            "The bank held its benchmark rate unchanged.",
        ]

        def cites(quotes):
            return [
                {"url": url, "quote": quote, "stance": "supports"}
                for url, quote in zip(urls, quotes)
            ]

        checks = [{
            "claim_id": "fact-1",
            "claim": claim_override or self.story["verified_facts"][0],
            "verdict": "supported",
            "evidence": cites(fact_quotes),
        }]
        if not omit_draft:
            checks.append({
                "claim_id": "draft-1", "claim": self.draft_text,
                "verdict": "supported", "evidence": cites(draft_quotes),
            })
        return {"choices": [{"finish_reason": "stop", "message": {
            "refusal": None,
            "content": json.dumps({"overall_verdict": verdict, "checks": checks}),
        }}]}

    def _run_backend(self, model_response=None, search_response=None):
        values = {
            "WESTCOTT_TAVILY_API_KEY": "test-search-key",
            "WESTCOTT_FACT_CHECK_OPENAI_API_KEY": "test-judge-key",
        }
        responses = [search_response or self.search_response,
                     model_response or self._model_response()]
        with patch.dict(os.environ, values, clear=True), \
             patch.object(fact_check_backend, "_request_json", side_effect=responses) as request:
            result = fact_validator.verify_story_claims(
                self.story, source_allowlist=self.domains,
                run_date=date(2030, 4, 5), timezone_name="Asia/Shanghai",
                draft_text=self.draft_text,
            )
        return result, request

    def test_supported_claims_need_matching_quotes_from_two_allowlisted_publishers(self):
        result, request = self._run_backend()
        self.assertIs(result, True)
        self.assertEqual(request.call_count, 2)
        search_call, model_call = request.call_args_list
        self.assertEqual(search_call.args[0], fact_check_backend.TAVILY_SEARCH_URL)
        search_payload = search_call.args[1]
        self.assertEqual(search_payload["include_domains"], self.domains)
        self.assertEqual(search_payload["include_domains_mode"], "restrict")
        self.assertEqual(search_payload["topic"], "general")
        self.assertEqual(search_payload["include_raw_content"], "text")
        self.assertFalse(search_payload["include_answer"])
        self.assertEqual(model_call.args[0], fact_check_backend.OPENAI_CHAT_URL)
        self.assertEqual(model_call.args[1]["response_format"]["type"], "json_schema")

    def test_missing_api_credential_fails_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(fact_validator.ValidationFailure, "WESTCOTT_TAVILY_API_KEY"):
                fact_validator.verify_story_claims(
                    self.story, source_allowlist=self.domains, run_date=date(2030, 4, 5),
                    timezone_name="Asia/Shanghai", draft_text=self.draft_text,
                )

    def test_single_publisher_cannot_satisfy_independent_evidence_requirement(self):
        only_one = {"results": [self.search_response["results"][0], {
            "url": "https://news.example.com/copy", "title": "Same publisher mirror",
            "raw_content": self.text_two,
        }]}
        with patch.dict(os.environ, {
            "WESTCOTT_TAVILY_API_KEY": "test-search-key",
            "WESTCOTT_FACT_CHECK_OPENAI_API_KEY": "test-judge-key",
        }, clear=True), patch.object(fact_check_backend, "_request_json", return_value=only_one):
            with self.assertRaisesRegex(fact_validator.ValidationFailure, "fewer than two independent"):
                fact_validator.verify_story_claims(
                    self.story, source_allowlist=self.domains, run_date=date(2030, 4, 5),
                    timezone_name="Asia/Shanghai", draft_text=self.draft_text,
                )

    def test_unapproved_search_results_are_ignored(self):
        unapproved = {"results": [self.search_response["results"][0], {
            "url": "https://bad.example.net/article", "title": "Unapproved",
            "raw_content": self.text_two,
        }]}
        with patch.dict(os.environ, {
            "WESTCOTT_TAVILY_API_KEY": "test-search-key",
            "WESTCOTT_FACT_CHECK_OPENAI_API_KEY": "test-judge-key",
        }, clear=True), patch.object(fact_check_backend, "_request_json", return_value=unapproved):
            with self.assertRaisesRegex(fact_validator.ValidationFailure, "fewer than two independent"):
                fact_validator.verify_story_claims(
                    self.story, source_allowlist=self.domains, run_date=date(2030, 4, 5),
                    timezone_name="Asia/Shanghai", draft_text=self.draft_text,
                )

    def test_contradicted_or_unclear_model_verdict_is_rejected(self):
        with self.assertRaisesRegex(fact_validator.ValidationFailure, "did not support"):
            self._run_backend(model_response=self._model_response(verdict="unclear"))

    def test_quote_not_present_verbatim_in_source_is_rejected(self):
        with self.assertRaisesRegex(fact_validator.ValidationFailure, "not verbatim"):
            self._run_backend(model_response=self._model_response(
                quote_override="A fabricated quote absent from both original sources."
            ))

    def test_model_must_check_every_draft_sentence_and_reference_fact(self):
        with self.assertRaisesRegex(fact_validator.ValidationFailure, "omitted or duplicated"):
            self._run_backend(model_response=self._model_response(omit_draft=True))

    def test_model_cannot_change_the_reference_fact_text(self):
        with self.assertRaisesRegex(fact_validator.ValidationFailure, "changed the text"):
            self._run_backend(model_response=self._model_response(claim_override="Different statement."))

    def test_model_citations_must_use_distinct_publisher_urls(self):
        same_publisher_urls = [self.url_one, "https://news.example.com/other"]
        same_publisher_results = {"results": [
            self.search_response["results"][0],
            {"url": same_publisher_urls[1], "title": "Same publisher other page", "raw_content": self.text_two},
        ]}
        model = self._model_response(evidence_urls=same_publisher_urls)
        with patch.dict(os.environ, {
            "WESTCOTT_TAVILY_API_KEY": "test-search-key",
            "WESTCOTT_FACT_CHECK_OPENAI_API_KEY": "test-judge-key",
        }, clear=True), patch.object(fact_check_backend, "_request_json",
                                    side_effect=[same_publisher_results, model]):
            with self.assertRaisesRegex(fact_validator.ValidationFailure, "two independent"):
                fact_validator.verify_story_claims(
                    self.story, source_allowlist=self.domains, run_date=date(2030, 4, 5),
                    timezone_name="Asia/Shanghai", draft_text=self.draft_text,
                )


if __name__ == "__main__":
    unittest.main()
