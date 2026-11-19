$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) { throw 'Run python -m venv .venv and install requirements.lock.txt first.' }
& '.\.venv\Scripts\python.exe' -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8766 --workers 1 --no-access-log
