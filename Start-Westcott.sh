#!/usr/bin/env bash
# Terminal launcher. In Claude Desktop, open this folder from Code using Local.
set -Eeuo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
command -v node >/dev/null || { echo 'Node.js LTS not found.' >&2; exit 69; }
command -v claude >/dev/null || { echo 'Claude Code CLI not found.' >&2; exit 69; }
node tools/desktop-doctor.mjs --quiet
exec claude "$@"
