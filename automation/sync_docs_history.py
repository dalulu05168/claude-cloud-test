#!/usr/bin/env python3
"""Refresh the private history index from one read-authorized Google document."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


DOCS_READONLY = "https://www.googleapis.com/auth/documents.readonly"
INDEXER = Path(__file__).resolve().parent.parent / "tools" / "docs-history.mjs"


def get_snapshot(document_id: str) -> dict:
    import google.auth
    import google_auth_httplib2
    import httplib2
    from googleapiclient.discovery import build

    credentials, _ = google.auth.default(scopes=[DOCS_READONLY])
    http = google_auth_httplib2.AuthorizedHttp(credentials, http=httplib2.Http(timeout=30))
    with build("docs", "v1", http=http, cache_discovery=False) as service:
        return service.documents().get(
            documentId=document_id, includeTabsContent=True,
            suggestionsViewMode="PREVIEW_WITHOUT_SUGGESTIONS",
        ).execute()


def sync(document_id: str, cache_dir: Path) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,150}", document_id):
        raise ValueError("invalid Google Docs document ID")
    node = shutil.which(os.environ.get("WESTCOTT_NODE_BIN", "node"))
    if node is None:
        raise RuntimeError("Node.js is unavailable")
    snapshot = get_snapshot(document_id)
    if snapshot.get("documentId") != document_id or not isinstance(snapshot.get("tabs"), list):
        raise RuntimeError("Google Docs did not return the expected tabbed document")
    cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix="westcott-history-") as folder:
        source = Path(folder) / "docs.json"
        source.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
        result = subprocess.run(
            [node, str(INDEXER), "update", str(source), str(cache_dir / "history-index.json")],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            check=True, timeout=30,
        )
    return json.loads(result.stdout)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--cache-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(sync(args.document_id, args.cache_dir)))
        return 0
    except Exception as exc:
        # Google errors may contain request details. Only report the error type.
        print(f"docs-history: refresh failed ({type(exc).__name__}); previous index retained", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
