#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
mode="${1:-cli}"
case "$mode" in
  cli|web|tui) ;;
  *) echo "Usage: $0 [cli|web|tui]" >&2; exit 2 ;;
esac
server=tools/course-state.mjs
[[ -f "$server" ]] || { echo "Missing MCP server: $server" >&2; exit 1; }
command -v node >/dev/null || { echo 'Node.js not found.' >&2; exit 1; }
command -v npx >/dev/null || { echo 'npx not found. Install Node.js LTS with npm, or run node tools/self-test.mjs.' >&2; exit 1; }

package=@modelcontextprotocol/inspector@2.16.0
node_version="$(node -p 'process.versions.node')"
IFS=. read -r major minor patch <<< "$node_version"
if [[ ! "$node_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || ! ((
  (major == 22 && (minor > 23 || (minor == 23 && patch >= 4))) ||
  (major == 24 && (minor > 22 || (minor == 22 && patch >= 1))) ||
  major > 26 || (major == 26 && (minor > 12 || (minor == 12 && patch >= 1)))
)); then
  echo "MCP Inspector 2.16.0 requires Node.js 22.23.4+ in 22.x, 24.22.1+ in 24.x, or 26.12.1+ (found ${node_version}). Use node tools/self-test.mjs for offline diagnostics." >&2
  exit 1
fi

# An inherited catalog conflicts with an explicit stdio target in Web and TUI.
unset MCP_CATALOG_PATH
inspector_args=("$package" "--$mode" --cwd "$PWD")
if [[ "$mode" == cli ]]; then inspector_args+=(--method tools/list --format json); fi
exec npx "${inspector_args[@]}" -e "CLAUDE_PROJECT_DIR=$PWD" node "$server"
