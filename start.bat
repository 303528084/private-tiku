@echo off
chcp 65001 >nul
setlocal EnableExtensions
title private-tiku launcher

REM ===========================================================================
REM  private-tiku one-click launcher (Windows)
REM
REM  Why is every message below ASCII-only?
REM  cmd.exe parses a .bat line by line using the CURRENT code page, so non-ASCII
REM  text inside a batch file is a classic source of mojibake (and occasionally
REM  broken parsing) once "chcp 65001" is active. Keeping the script ASCII makes
REM  it bullet-proof, while non-ASCII install paths (this repo lives under a
REM  Chinese path) are still fully supported because every path is quoted and
REM  derived from %~dp0 -- never from %CD%.
REM
REM  Why is there no "for /f ... in (`...`)" anywhere?
REM  cmd.exe strips the first and last quote of a command line that starts with a
REM  quote, so capturing the output of "%VENV_PY%" -c "..." breaks with
REM  "'C:\...\python.exe" -c "import' is not recognized". Everything that needs
REM  to capture or compare values lives in scripts/launcher.py; this file only
REM  checks exit codes.
REM
REM  Idempotent: re-running only fills in what is missing. It does not reinstall
REM  dependencies or rebuild the frontend unless they are actually stale.
REM ===========================================================================

REM ---- 1) repo root: %~dp0 is this script's own directory (trailing "\") ----
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
if not exist "%ROOT%\backend\app\main.py" (
  echo [ERROR] backend\app\main.py not found under "%ROOT%"
  goto :fail
)
cd /d "%ROOT%"

set "VENV=%ROOT%\.venv"
set "VENV_PY=%VENV%\Scripts\python.exe"
set "STAMP=%VENV%\.deps-stamp"
set "REQ=%ROOT%\backend\requirements.txt"
set "LAUNCHER=%ROOT%\scripts\launcher.py"

REM ---- 2) locate Python 3.11+ (prefer the py launcher) ----
set "BOOT_PY="
py -3 -c "import sys" >nul 2>&1
if not errorlevel 1 set "BOOT_PY=py -3"
if defined BOOT_PY goto :check_python_version
python -c "import sys" >nul 2>&1
if not errorlevel 1 set "BOOT_PY=python"
:check_python_version
if not defined BOOT_PY goto :no_python
%BOOT_PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 goto :old_python

REM ---- 3) reuse or create the virtual environment ----
if exist "%VENV_PY%" goto :venv_ready
echo [1/6] creating virtual environment .venv ...
%BOOT_PY% -m venv "%VENV%"
if errorlevel 1 goto :fail
goto :venv_done
:venv_ready
echo [1/6] reusing existing virtual environment .venv
:venv_done
if not exist "%VENV_PY%" goto :fail

REM ---- 4) dependencies: reinstall only when requirements.txt content changed ----
echo [2/6] checking backend dependencies ...
"%VENV_PY%" "%LAUNCHER%" deps --requirements "%REQ%" --stamp "%STAMP%"
if errorlevel 1 goto :fail

REM ---- 5) frontend bundle ----
if exist "%ROOT%\frontend\dist\index.html" goto :dist_ready
echo [3/6] frontend bundle missing, building ...
where npm >nul 2>&1
if errorlevel 1 goto :no_node
pushd "%ROOT%\frontend"
if exist package-lock.json (call npm ci) else (call npm install)
if errorlevel 1 goto :npm_fail
call npm run build
if errorlevel 1 goto :npm_fail
popd
goto :dist_done
:npm_fail
popd
goto :fail
:dist_ready
echo [3/6] frontend bundle found
:dist_done

REM ---- 6) .env ----
if exist "%ROOT%\.env" goto :env_ready
copy /y "%ROOT%\.env.example" "%ROOT%\.env" >nul
echo [4/6] created .env from .env.example
echo        To enable the access gate, set TIKU_ACCESS_TOKEN in .env and restart.
goto :env_done
:env_ready
echo [4/6] reusing existing .env
:env_done

REM ---- 7) port / readiness / browser / uvicorn are handled by launcher.py ----
echo [5/6] checking port and service ...
echo [6/6] starting uvicorn ^(press Ctrl+C to stop^)
echo.

"%VENV_PY%" "%LAUNCHER%" serve
set "EXIT_CODE=%ERRORLEVEL%"
goto :end

:no_python
echo [ERROR] Python 3.11+ not found.
echo         Install it from https://www.python.org/downloads/
echo         and tick "Add python.exe to PATH" during setup.
goto :fail

:old_python
echo [ERROR] Python 3.11+ is required, but this is what was found:
%BOOT_PY% -V
goto :fail

:no_node
echo [ERROR] frontend\dist is missing and npm was not found.
echo         Install Node.js 18+ and re-run this script, or build manually:
echo             cd frontend ^&^& npm install ^&^& npm run build
goto :fail

:fail
set "EXIT_CODE=1"

:end
if "%EXIT_CODE%"=="0" goto :done
echo.
echo Startup failed. See the messages above.
pause
:done
endlocal & exit /b %EXIT_CODE%
