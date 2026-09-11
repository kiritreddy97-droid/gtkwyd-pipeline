# Wrapper: ./run.ps1 scripts/my-topic.md   [--no-stock] [--no-captions] [--list-voices]
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path ".venv\Scripts\python.exe")) { throw "Run ./setup.ps1 first." }
& .\.venv\Scripts\python.exe make_video.py @args
exit $LASTEXITCODE
