@echo off
setlocal
rem Terminal launcher. In Claude Desktop, open this folder from Code using Local.
cd /d "%~dp0" || exit /b 1
where.exe node >nul 2>nul
if errorlevel 1 (
  echo Node.js was not found. Install Node.js LTS and reopen this file.
  pause
  exit /b 1
)
where.exe claude >nul 2>nul
if errorlevel 1 (
  echo Claude Code was not found. Install Claude Code and reopen this file.
  pause
  exit /b 1
)
call node "tools/desktop-doctor.mjs" --quiet
if errorlevel 1 exit /b %errorlevel%
call claude --agent westcott-director
exit /b %errorlevel%
