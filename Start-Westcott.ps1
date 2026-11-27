# Terminal launcher. In Claude Desktop, open this folder from Code using Local.
$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw 'Node.js LTS not found.' }
    if (-not (Get-Command claude -ErrorAction SilentlyContinue)) { throw 'Claude Code CLI not found.' }
    & node tools/desktop-doctor.mjs --quiet
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & claude @args
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
