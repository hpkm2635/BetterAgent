# PowerShell Start Script for BetterAgent (Windows)

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "   Starting BetterAgent Microservices     " -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

$ROOT_DIR = Get-Item -Path $PSScriptRoot\.. | Select-Object -ExpandProperty FullName
Set-Location $ROOT_DIR

# 0. Automatically stop any currently running instances
if (Test-Path "$PSScriptRoot\win_stop.ps1") {
    & "$PSScriptRoot\win_stop.ps1"
}

# 1. Determine Python Interpreter (.venv preferred)
if (Test-Path "$ROOT_DIR\.venv\Scripts\python.exe") {
    $pyExe = "$ROOT_DIR\.venv\Scripts\python.exe"
} elseif (Test-Path "$ROOT_DIR\.venv\bin\python.exe") {
    $pyExe = "$ROOT_DIR\.venv\bin\python.exe"
} else {
    $pyExe = "python"
}

# 2. Delegate to cross-platform orchestrator (runner.py)
if (Test-Path "$ROOT_DIR\runner.py") {
    & $pyExe "$ROOT_DIR\runner.py"
} else {
    Write-Host "[!] runner.py not found in project root." -ForegroundColor Red
}
