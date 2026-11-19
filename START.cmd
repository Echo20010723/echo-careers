@echo off
setlocal
cd /d "%~dp0" || goto failed
if not exist ".venv\Scripts\python.exe" (
    py -3.12 -m venv .venv
    if errorlevel 1 python -m venv .venv
    if errorlevel 1 goto failed
)
if not exist ".venv\.requirements-ready" (
    ".venv\Scripts\python.exe" -m pip install -r requirements.lock.txt
    if errorlevel 1 goto failed
    type nul > ".venv\.requirements-ready"
)
".venv\Scripts\python.exe" scripts\launch.py %*
if errorlevel 1 goto failed
exit /b 0
:failed
echo Startup failed. Check the error above and data\server.log if it exists.
echo Workbench address: http://127.0.0.1:8766
pause
exit /b 1
