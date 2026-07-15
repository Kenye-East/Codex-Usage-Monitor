$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

if (-not (Test-Path '.venv\Scripts\python.exe')) {
    python -m venv .venv
}

$python = Join-Path $root '.venv\Scripts\python.exe'
& $python -m pip install -e '.[dev]'
& $python -m pip install pyinstaller
& $python -m PyInstaller --noconfirm --clean --onefile --noconsole --name Codex-Usage-Monitor --icon assets\openai-icon.ico --paths src --collect-data customtkinter --add-data "assets;assets" src\usage_overlay\main.py

if (-not (Test-Path 'dist\Codex-Usage-Monitor.exe')) {
    throw 'Codex-Usage-Monitor.exe was not produced.'
}
