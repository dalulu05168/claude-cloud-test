#!/usr/bin/env python3
"""Fail-closed fact-validator adapter for claude_code_runner.py.

This file supplies input, structure, provenance, and freshness checks plus the
exact Runner receipt protocol. verify_story_claims() delegates to a separate
evidence backend that retrieves allowlisted sources and requires cited support.
Any backend error fails closed and cannot authorize publication.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fact_check_backend import FactCheckError, verify_story_claims as _verify_story_claims

MAX_DRAFT_BYTES = 200_000
MAX_RESEARCH_BYTES = 512_000
MAX_FUTURE_SKEW_SECONDS = 300
ROLE_MARKER = "===== 资讯助理 ====="
RFC3339_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")
RULESET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")


class ValidationFailure(Exception):
    """A safe-to-log validation failure; never include draft text or secrets."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _normalize_domain(value: Any) -> str:
    if not isinstance(value, str):
        raise ValidationFailure("research source allowlist contains a non-string value")
    domain = value.strip().lower().rstrip(".")
    if (not domain or len(domain) > 253 or "://" in domain or "/" in domain
            or "@" in domain or not re.fullmatch(r"[a-z0-9.-]+", domain)
            or domain.startswith(".") or ".." in domain):
        raise ValidationFailure("research source allowlist contains an invalid hostname")
    labels = domain.split(".")
    if len(labels) < 2 or any(
        not 1 <= len(label) <= 63
        or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
        for label in labels
    ):
        raise ValidationFailure("research source allowlist contains an invalid hostname")
    try:
        ipaddress.ip_address(domain)
    except ValueError:
        pass
    else:
        raise ValidationFailure("research source allowlist must contain hostnames, not IP addresses")
    if domain == "localhost" or domain.endswith((".localhost", ".local", ".internal")):
        raise ValidationFailure("research source allowlist cannot contain a local hostname")
    return domain


def _safe_http_url(value: Any) -> str:
    if not isinstance(value, str) or not value or re.search(r"[\s<>\"\x00-\x1f]", value):
        raise ValidationFailure("a story contains an invalid URL")
    try:
        parsed = urlparse(value)
        host = parsed.hostname
        _ = parsed.port
    except ValueError as exc:
        raise ValidationFailure("a story contains a malformed URL") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not host or parsed.username or parsed.password:
        raise ValidationFailure("a story URL must be HTTP(S) without embedded credentials")
    host = host.lower().rstrip(".")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ValidationFailure("a story URL resolves to a local hostname")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValidationFailure("a story URL contains a non-public IP address")
    return value


def _allowed_host(url: str, domains: list[str]) -> bool:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def verify_story_claims(story: dict[str, Any], *, source_allowlist: list[str],
                        run_date: date, timezone_name: str, draft_text: str) -> bool:
    """Check every draft sentence and declared fact against live independent sources.

    Only fixed HTTPS search/model endpoints are contacted. Search-result URLs are
    not fetched by this process; source domains, exact quotes, evidence count,
    and per-claim verdicts are checked locally before this function can return.
    """
    try:
        return _verify_story_claims(
            story, source_allowlist=source_allowlist, run_date=run_date,
            timezone_name=timezone_name, draft_text=draft_text,
        )
    except FactCheckError as exc:
        raise ValidationFailure(str(exc)) from exc


def _read_inputs(args: argparse.Namespace) -> tuple[Path, Path, bytes, bytes, str, dict[str, Any]]:
    if args.run_id != f"daily-{args.date}":
        raise ValidationFailure("run_id does not match the requested date")
    try:
        run_date = date.fromisoformat(args.date)
        if run_date.isoformat() != args.date:
            raise ValueError("non-canonical date")
        zone = ZoneInfo(args.timezone)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ValidationFailure("date or IANA timezone is invalid") from exc
    if not RULESET_RE.fullmatch(args.ruleset_version):
        raise ValidationFailure("ruleset version is malformed")
    if not 0 <= args.max_story_age_hours <= 720:
        raise ValidationFailure("max story age must be between 0 and 720 hours")

    draft_path = Path(args.draft).resolve(strict=True)
    research_path = Path(args.research).resolve(strict=True)
    if draft_path.name != "draft.md" or research_path.name != "research.json" or draft_path.parent != research_path.parent:
        raise ValidationFailure("draft and research must be the fixed files in one run directory")
    draft_bytes = draft_path.read_bytes()
    research_bytes = research_path.read_bytes()
    if not 0 < len(draft_bytes) <= MAX_DRAFT_BYTES or not 0 < len(research_bytes) <= MAX_RESEARCH_BYTES:
        raise ValidationFailure("draft or research record is empty or exceeds its safety limit")
    try:
        draft = draft_bytes.decode("utf-8").replace("\r\n", "\n")
        research = json.loads(research_bytes, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValidationFailure("draft or research record is not valid UTF-8/JSON") from exc
    if not isinstance(research, dict) or set(research) != {
        "schema_version", "task_date", "market_scope", "source_allowlist", "stories",
    }:
        raise ValidationFailure("research record does not match the supported schema")
    if research.get("schema_version") != "westcott-daily-research-v1" or research.get("task_date") != args.date:
        raise ValidationFailure("research record schema or task date does not match")
    if not isinstance(research.get("market_scope"), str) or not research["market_scope"].strip():
        raise ValidationFailure("research record has no market scope")

    raw_domains = research.get("source_allowlist")
    if not isinstance(raw_domains, list) or not raw_domains:
        raise ValidationFailure("research record has no source allowlist")
    domains = [_normalize_domain(item) for item in raw_domains]
    if len(domains) != len(set(domains)):
        raise ValidationFailure("research source allowlist contains duplicates")
    research["source_allowlist"] = sorted(domains)
    return draft_path, research_path, draft_bytes, research_bytes, draft, research


def validate(args: argparse.Namespace) -> dict[str, str]:
    draft_path, research_path, draft_bytes, research_bytes, draft, research = _read_inputs(args)
    run_date = date.fromisoformat(args.date)
    zone = ZoneInfo(args.timezone)
    stories = research.get("stories")
    if not isinstance(stories, list) or not stories or len(stories) > 10:
        raise ValidationFailure("research record story count is outside the supported range")
    blocks = draft.split(ROLE_MARKER)
    if blocks[0].strip() or len(blocks) != len(stories) + 1:
        raise ValidationFailure("draft story blocks do not match research record")

    required_story_fields = {
        "country", "title", "source_url", "source_title", "published_at",
        "image_url", "verified_facts",
    }
    now = datetime.now(zone)
    for index, (story, block) in enumerate(zip(stories, blocks[1:]), start=1):
        if not isinstance(story, dict) or set(story) != required_story_fields:
            raise ValidationFailure(f"story {index} does not match the required research fields")
        for key in ("country", "title", "source_title"):
            if not isinstance(story[key], str) or not story[key].strip():
                raise ValidationFailure(f"story {index} is missing {key}")
        source_url = _safe_http_url(story["source_url"])
        if not _allowed_host(source_url, research["source_allowlist"]):
            raise ValidationFailure(f"story {index} source is outside the source allowlist")
        _safe_http_url(story["image_url"])
        if not isinstance(story["verified_facts"], list) or not story["verified_facts"] or any(
            not isinstance(fact, str) or not fact.strip() for fact in story["verified_facts"]
        ):
            raise ValidationFailure(f"story {index} has no source-backed fact notes")

        published_value = story["published_at"]
        if not isinstance(published_value, str) or not RFC3339_RE.fullmatch(published_value):
            raise ValidationFailure(f"story {index} published_at is not RFC 3339")
        try:
            published_at = datetime.fromisoformat(published_value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValidationFailure(f"story {index} published_at is invalid") from exc
        if published_at.tzinfo is None:
            raise ValidationFailure(f"story {index} published_at has no timezone")
        local_published = published_at.astimezone(zone)
        if local_published.date() > run_date:
            raise ValidationFailure(f"story {index} publication date is after task date")
        if (local_published - now).total_seconds() > MAX_FUTURE_SKEW_SECONDS:
            raise ValidationFailure(f"story {index} publication time is in the future")
        if (now - local_published).total_seconds() > args.max_story_age_hours * 3600:
            raise ValidationFailure(f"story {index} is outside the configured freshness window")

        paragraphs = [part.strip() for part in re.split(r"\n[ \t]*\n(?:[ \t]*\n)*", block.strip()) if part.strip()]
        if len(paragraphs) != 4 or not paragraphs[-1].startswith(("配图：", "配图:")):
            raise ValidationFailure(f"draft block {index} has an invalid structure")
        if paragraphs[-1].startswith("配图："):
            image_footer = paragraphs[-1][len("配图："):].strip()
        else:
            image_footer = paragraphs[-1][len("配图:"):].strip()
        if image_footer != story["image_url"] or story["country"] not in paragraphs[0] or story["title"] not in paragraphs[0]:
            raise ValidationFailure(f"draft block {index} does not match its research metadata")

        prose = "\n".join(paragraphs[:-1])
        if verify_story_claims(story, source_allowlist=research["source_allowlist"],
                               run_date=run_date, timezone_name=args.timezone,
                               draft_text=prose) is not True:
            raise ValidationFailure(f"independent source verification failed for story {index}")

    # Do not issue a receipt if any input changed while the independent check ran.
    if draft_path.read_bytes() != draft_bytes or research_path.read_bytes() != research_bytes:
        raise ValidationFailure("draft or research changed during validation")
    return {
        "status": "passed",
        "run_id": args.run_id,
        "ruleset_version": args.ruleset_version,
        "draft_sha256": hashlib.sha256(draft_bytes).hexdigest(),
        "research_sha256": hashlib.sha256(research_bytes).hexdigest(),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draft", required=True)
    parser.add_argument("--research", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--timezone", required=True)
    parser.add_argument("--max-story-age-hours", required=True, type=int)
    parser.add_argument("--ruleset-version", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        receipt = validate(parse_args(argv))
    except (ValidationFailure, OSError, ValueError) as exc:
        print(f"fact-validator: rejected ({exc})", file=sys.stderr)
        return 2
    except Exception:
        # Keep unexpected details out of scheduled-task logs; never emit a receipt.
        print("fact-validator: unexpected failure; no receipt issued", file=sys.stderr)
        return 2
    print(json.dumps(receipt, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
