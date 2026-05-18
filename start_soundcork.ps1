param(
    [string]$HostName = $(if ($env:SOUNDCORK_HOST) { $env:SOUNDCORK_HOST } else { "0.0.0.0" }),
    [int]$Port = $(if ($env:SOUNDCORK_PORT) { [int]$env:SOUNDCORK_PORT } else { 8000 })
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppDir = Join-Path $RepoRoot "soundcork"
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    throw "Virtualenv Python not found at $Python. Create it first with: py -3.12 -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt"
}

Write-Host ("Starting SoundCork at http://{0}:{1}" -f $HostName, $Port)
Push-Location $AppDir
try {
    & $Python -m fastapi run main.py --host $HostName --port $Port
}
finally {
    Pop-Location
}
