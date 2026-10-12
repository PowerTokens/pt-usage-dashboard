@echo off
rem pt-usage-dashboard one-click launcher
rem Usage:
rem   run.bat            ^(open the local web console - recommended^)
rem   run.bat web        ^(same as above^)
rem   run.bat cli        ^(old way: fetch + generate HTML from the command line^)
rem   run.bat cli zh     ^(old way, Chinese dashboard^)
rem Requires: Python 3.8+ (standard library only)
setlocal
set "DIR=%~dp0"
if defined PYTHON (set "PY=%PYTHON%") else (set "PY=python")
set "MODE=%~1"
if "%MODE%"=="" set "MODE=web"
set "LANG_CHOICE=%~2"
if "%LANG_CHOICE%"=="" set "LANG_CHOICE=en"

if /i "%MODE%"=="cli" goto :cli

echo ==^> Starting the local web console (localhost only, token never leaves this PC)
echo     Your browser should open automatically; if not, visit http://127.0.0.1:8777/
echo     Press Ctrl+C to stop.
"%PY%" "%DIR%serve.py"
goto :eof

:cli
echo ==^> [1/2] Fetch PowerTokens usage (needs config.json with _Bearer token)
if exist "%DIR%config.json" (
  "%PY%" "%DIR%scripts\fetch_pt_server.py"
) else (
  echo !! no config.json found - skip fetch, will use cached pt_usage_server*.json if any
  echo    ^(or just run 'run.bat' with no args to paste your token in the browser^)
)
echo ==^> [2/2] Generate dashboard (lang=%LANG_CHOICE%)
"%PY%" "%DIR%scripts\gen_dashboard.py" --lang %LANG_CHOICE% --out "%DIR%pt-usage-dashboard.html"
echo ==^> Done. Open in your browser:
echo     %DIR%pt-usage-dashboard.html
endlocal
