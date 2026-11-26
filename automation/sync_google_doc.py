#!/usr/bin/env python3
"""Publish a validated Westcott Markdown draft as one formatted Google Doc per run.

Authentication is provided by Google Application Default Credentials on the
scheduler/CI host (for example Workload Identity or GOOGLE_APPLICATION_CREDENTIALS).
No credential is read from the repository or written to logs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Iterable

LOG = logging.getLogger("westcott.docs_publisher")
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
GOOGLE_DOC_MIME = "application/vnd.google-apps.document"
SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def _inline_runs(paragraph, text: str) -> None:
    """Render **bold** and *italic* inline syntax without interpreting HTML."""
    token_re = re.compile(r"(\*\*.+?\*\*|__.+?__|(?<!\*)\*[^*]+\*(?!\*))")
    pos = 0
    for match in token_re.finditer(text):
        if match.start() > pos:
            paragraph.add_run(text[pos:match.start()])
        token = match.group(0)
        if token.startswith("**") or token.startswith("__"):
            run = paragraph.add_run(token[2:-2])
            run.bold = True
        else:
            run = paragraph.add_run(token[1:-1])
            run.italic = True
        pos = match.end()
    if pos < len(text):
        paragraph.add_run(text[pos:])


def build_docx(markdown: str, title: str, path: Path) -> None:
    """Convert a conservative Markdown subset into a styled DOCX."""
    try:
        from docx import Document
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.shared import Inches, Pt
    except ImportError as exc:  # dependency is installed on the external publisher host
        raise RuntimeError("python-docx is required; install automation/requirements-docs.txt") from exc

    document = Document()
    section = document.sections[0]
    section.top_margin = Inches(0.72)
    section.bottom_margin = Inches(0.72)
    section.left_margin = Inches(0.8)
    section.right_margin = Inches(0.8)
    normal = document.styles["Normal"]
    normal.font.name = "Aptos"
    normal.font.size = Pt(10.5)

    title_para = document.add_heading(title, level=0)
    title_para.alignment = WD_ALIGN_PARAGRAPH.LEFT

    in_code = False
    for raw in markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.rstrip()
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if not line.strip():
            continue
        if in_code:
            paragraph = document.add_paragraph(style="No Spacing")
            run = paragraph.add_run(line)
            run.font.name = "Consolas"
            run.font.size = Pt(9)
            continue

        heading = re.match(r"^(#{1,3})\s+(.+?)\s*#*\s*$", line)
        if heading:
            level = len(heading.group(1))
            paragraph = document.add_heading(level=level)
            _inline_runs(paragraph, heading.group(2))
            continue

        bullet = re.match(r"^\s*[-*+]\s+(.+)$", line)
        if bullet:
            paragraph = document.add_paragraph(style="List Bullet")
            _inline_runs(paragraph, bullet.group(1))
            continue

        numbered = re.match(r"^\s*\d+[.)]\s+(.+)$", line)
        if numbered:
            paragraph = document.add_paragraph(style="List Number")
            _inline_runs(paragraph, numbered.group(1))
            continue

        paragraph = document.add_paragraph()
        _inline_runs(paragraph, line)

    # Minimal document metadata helps operators identify provenance after download.
    document.core_properties.title = title
    document.core_properties.subject = "Westcott validated daily content"
    document.core_properties.author = "Westcott content system"
    document.save(path)


def _quote_drive_query(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def find_existing(drive, folder_id: str, run_id: str) -> list[dict]:
    folder = _quote_drive_query(folder_id)
    run = _quote_drive_query(run_id)
    query = (
        f"'{folder}' in parents and "
        f"appProperties has {{ key='westcott_run_id' and value='{run}' }} and trashed=false"
    )
    found: list[dict] = []
    token = None
    while True:
        response = drive.files().list(
            q=query,
            pageToken=token,
            pageSize=100,
            fields="nextPageToken,files(id,name,mimeType,webViewLink,appProperties,parents)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        found.extend(response.get("files", []))
        token = response.get("nextPageToken")
        if not token:
            return found


def _load_and_check(args) -> tuple[str, bytes, str]:
    draft = Path(args.input).resolve(strict=True)
    manifest_path = Path(args.manifest).resolve(strict=True)
    if draft.name != "draft.md" or manifest_path.name != "manifest.json" or draft.parent != manifest_path.parent:
        raise ValueError("draft and manifest must be the fixed files in the same run directory")
    content_bytes = draft.read_bytes()
    if len(content_bytes) > 2_000_000:
        raise ValueError("draft exceeds the 2 MB publisher safety limit")
    markdown = content_bytes.decode("utf-8-sig")
    if not markdown.strip():
        raise ValueError("draft is empty")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be a JSON object")
    if manifest.get("run_id") != args.run_id or manifest.get("task_date") != args.date:
        raise ValueError("manifest run_id/task_date does not match publisher arguments")
    state = manifest.get("status", manifest.get("state"))
    if state not in {"ready_for_docs", "docs_saving", "delivery_pending"}:
        raise ValueError("manifest is not in a Google Docs publishable state")
    validation = manifest.get("validation", {})
    stage_validation = manifest.get("stages", {}).get("validation", {})
    validation_passed = (
        validation.get("status") == "passed"
        or stage_validation.get("status") == "succeeded"
    )
    if not validation_passed:
        raise ValueError("manifest validation status is not passed")

    digest = hashlib.sha256(content_bytes).hexdigest()
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("manifest has no artifact integrity records")
    draft_record = artifacts.get("draft")
    research_record = artifacts.get("research")
    if (not isinstance(draft_record, dict) or draft_record.get("path") != "draft.md"
            or type(draft_record.get("size_bytes")) is not int
            or draft_record.get("sha256") != digest
            or draft_record.get("size_bytes") != len(content_bytes)):
        raise ValueError("draft differs from its manifest integrity record")

    research = draft.parent / "research.json"
    try:
        research_bytes = research.read_bytes()
    except OSError as exc:
        raise ValueError("research record is missing or unreadable") from exc
    if len(research_bytes) > 512_000:
        raise ValueError("research record exceeds the publisher safety limit")
    research_digest = hashlib.sha256(research_bytes).hexdigest()
    if (not isinstance(research_record, dict) or research_record.get("path") != "research.json"
            or type(research_record.get("size_bytes")) is not int
            or research_record.get("sha256") != research_digest
            or research_record.get("size_bytes") != len(research_bytes)):
        raise ValueError("research record differs from its manifest integrity record")
    return markdown, content_bytes, digest


def _build_drive_service():
    try:
        import google.auth
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError("Google API libraries are required; install automation/requirements-docs.txt") from exc
    credentials, _ = google.auth.default(scopes=SCOPES)
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def publish(args) -> dict:
    if len(args.run_id) > 124:
        raise ValueError("run_id is too long for the Drive appProperties idempotency marker")
    markdown, content_bytes, digest = _load_and_check(args)
    drive = _build_drive_service()
    existing = find_existing(drive, args.folder_id, args.run_id)
    if len(existing) > 1:
        raise RuntimeError("multiple Google Docs already carry this run_id; refusing to choose or modify one")
    if existing:
        doc = existing[0]
        stored_hash = (doc.get("appProperties") or {}).get("westcott_content_sha256")
        if stored_hash != digest:
            raise RuntimeError(
                "a Google Doc already exists for this run_id but its recorded source hash differs; "
                "refusing to overwrite. Review the document and create a new run_id for revised content."
            )
        return {
            "run_id": args.run_id,
            "status": "saved",
            "document_id": doc["id"],
            "url": doc.get("webViewLink") or f"https://docs.google.com/document/d/{doc['id']}/edit",
            "content_sha256": digest,
            "idempotent": True,
        }

    try:
        from googleapiclient.http import MediaFileUpload
    except ImportError as exc:
        raise RuntimeError("google-api-python-client is required; install automation/requirements-docs.txt") from exc

    with tempfile.TemporaryDirectory(prefix="westcott-docx-") as temp_dir:
        docx_path = Path(temp_dir) / "westcott-draft.docx"
        build_docx(markdown, args.title, docx_path)
        metadata = {
            "name": args.title,
            "mimeType": GOOGLE_DOC_MIME,
            "parents": [args.folder_id],
            "appProperties": {
                "westcott_run_id": args.run_id,
                "westcott_content_sha256": digest,
                "westcott_ruleset_version": _ruleset_version(args.manifest),
            },
            "description": f"Automated Westcott daily content; run_id={args.run_id}",
        }
        media = MediaFileUpload(str(docx_path), mimetype=DOCX_MIME, resumable=True)
        created = drive.files().create(
            body=metadata,
            media_body=media,
            fields="id,name,mimeType,webViewLink,appProperties,parents",
            supportsAllDrives=True,
        ).execute()

    if created.get("mimeType") != GOOGLE_DOC_MIME:
        # Do not delete a file automatically; retain it for operator inspection.
        raise RuntimeError("Drive upload completed but returned a non-Google-Docs MIME type")
    return {
        "run_id": args.run_id,
        "status": "saved",
        "document_id": created["id"],
        "url": created.get("webViewLink") or f"https://docs.google.com/document/d/{created['id']}/edit",
        "content_sha256": digest,
        "idempotent": False,
    }


def _ruleset_version(manifest_path: str) -> str:
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    return str(manifest.get("ruleset_version", "unknown"))[:124]


def parse_args(argv: Iterable[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="validated Markdown draft path")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--date", required=True, help="business date YYYY-MM-DD")
    parser.add_argument("--title", required=True)
    parser.add_argument("--folder-id", required=True, help="authorized Google Drive folder ID")
    parser.add_argument("--manifest", required=True, help="validated run manifest JSON")
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    logging.basicConfig(level=os.getenv("WESTCOTT_LOG_LEVEL", "INFO"), stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        result = publish(parse_args(argv))
        # run-daily.sh consumes exactly one JSON object from stdout.
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0
    except Exception as exc:
        LOG.error("Google Docs publish failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
