@echo off
rem dspet launcher: installs deps on first run, then starts the pet (no console).
cd /d "%~dp0"

rem Prefer the Python installed under LOCALAPPDATA; fall back to PATH.
set "PYDIR=%LOCALAPPDATA%\Programs\Python\Python312"
if exist "%PYDIR%\pythonw.exe" (
    set "PYW=%PYDIR%\pythonw.exe"
    set "PY=%PYDIR%\python.exe"
) else (
    set "PYW=pythonw"
    set "PY=python"
)

rem Skip the dependency check once it has passed (see .deps-ok).
if exist ".deps-ok" goto run

echo dspet: checking dependencies ...
"%PY%" -c "import PySide6, psutil, requests" >nul 2>nul
if errorlevel 1 (
    echo dspet: installing dependencies, please wait a minute ...
    "%PY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo dspet: install failed. Read the error above, or report it at
        echo        https://github.com/088uiop/dspet/issues
        pause
        exit /b 1
    )
)
> .deps-ok echo ok

:run
start "" "%PYW%" "dspet.py"
