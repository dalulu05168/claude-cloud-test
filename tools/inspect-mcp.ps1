param(
    [ValidateSet('web', 'cli', 'tui')]
    [string]$Mode = 'cli'
)
$ErrorActionPreference = 'Stop'
$projectDir = Split-Path -Parent $PSScriptRoot
$server = 'tools/course-state.mjs'
if (-not (Test-Path -LiteralPath (Join-Path $projectDir $server) -PathType Leaf)) { throw "Missing MCP server: $server" }
if (-not (Get-Command node -CommandType Application -ErrorAction SilentlyContinue)) { throw 'Node.js not found.' }
# On Windows, prefer the native npx.cmd shim so PowerShell's script policy
# doesn't prevent invocation through npm's alternative npx.ps1 shim.
$npxCommand = Get-Command npx -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $npxCommand) { throw 'npx not found. Install Node.js LTS with npm, or run node tools/self-test.mjs.' }

$package = '@modelcontextprotocol/inspector@2.16.0'
$versionText = & node -p 'process.versions.node'
if ($LASTEXITCODE -ne 0) { throw 'Unable to determine the Node.js version.' }
$nodeVersion = [version]'0.0.0'
if (-not [version]::TryParse($versionText, [ref]$nodeVersion) -or -not (
    ($nodeVersion.Major -eq 22 -and $nodeVersion -ge [version]'22.23.4') -or
    ($nodeVersion.Major -eq 24 -and $nodeVersion -ge [version]'24.22.1') -or
    $nodeVersion -ge [version]'26.12.1'
)) {
    throw "MCP Inspector 2.16.0 requires Node.js 22.23.4+ in 22.x, 24.22.1+ in 24.x, or 26.12.1+ (found $versionText). Use node tools/self-test.mjs for offline diagnostics."
}

$inspectorArgs = @($package, "--$Mode", '--cwd', $projectDir)
if ($Mode -eq 'cli') { $inspectorArgs += @('--method', 'tools/list', '--format', 'json') }
$inspectorArgs += @('-e', "CLAUDE_PROJECT_DIR=$projectDir", 'node', $server)

# An inherited catalog conflicts with an explicit stdio target in Web and TUI.
$inspectorCatalog = $env:MCP_CATALOG_PATH
Push-Location -LiteralPath $projectDir
try {
    $env:MCP_CATALOG_PATH = $null
    & $npxCommand.Source @inspectorArgs
    exit $LASTEXITCODE
} finally {
    $env:MCP_CATALOG_PATH = $inspectorCatalog
    Pop-Location
}
