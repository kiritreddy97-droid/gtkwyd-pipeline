# One-time setup for the Get To Know What You Don't pipeline.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "== Get To Know What You Don't - pipeline setup ==" -ForegroundColor Cyan

# --- Python -------------------------------------------------------------------
$py = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
if (-not (Test-Path $py)) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -notmatch "WindowsApps") { $py = $cmd.Source }
    else { throw "Python 3.12 not found. Run:  winget install Python.Python.3.12  then reopen the terminal." }
}
Write-Host "Python: $py"

# --- venv + packages --------------------------------------------------------
if (-not (Test-Path ".venv")) { & $py -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip -q
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

# --- ffmpeg check ----------------------------------------------------------
$ff = Get-Command ffmpeg -ErrorAction SilentlyContinue
if (-not $ff) {
    Write-Warning "ffmpeg not on PATH. Run:  winget install Gyan.FFmpeg  then reopen the terminal."
} else {
    Write-Host ("ffmpeg: " + (& ffmpeg -version | Select-Object -First 1))
}

# --- Piper binary --------------------------------------------------------
if (-not (Test-Path "piper\piper.exe")) {
    Write-Host "Downloading Piper (offline TTS) ..."
    $ProgressPreference = "SilentlyContinue"
    $zip = "$env:TEMP\piper_win.zip"
    Invoke-WebRequest "https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_windows_amd64.zip" -OutFile $zip
    Expand-Archive $zip -DestinationPath $PSScriptRoot -Force
    Remove-Item $zip
}
Write-Host "Piper: piper\piper.exe"

# --- default voice ------------------------------------------------------
& .\.venv\Scripts\python.exe -m pipeline.voices --ensure

# --- config ------------------------------------------------------------
if (-not (Test-Path "config.toml")) {
    Copy-Item "config.example.toml" "config.toml"
    Write-Host "Created config.toml - open it and paste your Pexels + Pixabay API keys." -ForegroundColor Yellow
}

Write-Host "`nSetup complete." -ForegroundColor Green
Write-Host "Next:  edit config.toml (API keys), then  ./run.ps1 scripts/example-octopus.md"
