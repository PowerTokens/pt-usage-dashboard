@echo off
rem pt-usage-dashboard one-click: fetch data -> generate dashboard
rem Usage:
rem   run.bat          ^(English dashboard, default^)
rem   run.bat zh       ^(Chinese dashboard^)
rem Requires: Python 3.8+ (standard library only), config.json with your _Bearer token
setlocal
set "DIR=%~dp0"
if defined PYTHON (set "PY=%PYTHON%") else (set "PY=python")
if "%~1"=="" (set "LANG_CHOICE=en") else (set "LANG_CHOICE=%~1")

echo ==^> [1/2] Fetch PowerTokens usage (needs config.json with _Bearer token)
if exist "%DIR%config.json" (
  "%PY%" "%DIR%scripts\fetch_pt_server.py"
) else (
  echo !! no config.json found - skip fetch, will use cached pt_usage_server*.json if any
  echo    ^(copy config.example.json to config.json and paste your _Bearer cookie value^)
)

echo ==^> [2/2] Generate dashboard (lang=%LANG_CHOICE%)
"%PY%" "%DIR%scripts\gen_dashboard.py" --lang %LANG_CHOICE% --out "%DIR%pt-usage-dashboard.html"

echo ==^> Done. Open in your browser:
echo     %DIR%pt-usage-dashboard.html
endlocal
