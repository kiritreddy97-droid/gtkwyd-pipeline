# Second setup step: everything needed for FULL automation (auto.py).
# Run setup.ps1 first.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "== automation setup ==" -ForegroundColor Cyan

if (-not (Test-Path ".venv\Scripts\python.exe")) { throw "Run ./setup.ps1 first." }

# --- Python libs for YouTube upload --------------------------------------
Write-Host "Installing YouTube API libraries ..."
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

# --- Ollama (local script writer) ---------------------------------------
$ollama = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollama) {
    Write-Host "Installing Ollama ..."
    winget install -e --id Ollama.Ollama --accept-package-agreements --accept-source-agreements --disable-interactivity
    Write-Warning "Ollama installed. You may need to open a NEW terminal, then re-run this script."
} else {
    Write-Host "Ollama: $($ollama.Source)"
    $model = "llama3.2:3b"
    Write-Host "Pulling model $model (this is a few GB, one time) ..."
    & ollama pull $model
}

# --- folders -----------------------------------------------------------
foreach ($d in "scripts\ready","scripts\review","scripts\published",
               "scripts\_generated","scripts\shorts-ready","scripts\shorts-review",
               "scripts\_generated-shorts","assets\music\videos","assets\music\shorts") {
    New-Item -ItemType Directory -Force $d | Out-Null
}

Write-Host "`nAutomation libraries ready." -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  1. Follow YOUTUBE_SETUP.md to create client_secret.json"
Write-Host "  2. Authorise once:   .\.venv\Scripts\python.exe -m pipeline.youtube --auth"
Write-Host "  3. Test without uploading:   .\.venv\Scripts\python.exe auto.py --dry-run"
Write-Host "  4. Schedule it:   .\schedule-install.ps1"
