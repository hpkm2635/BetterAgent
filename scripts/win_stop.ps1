param(
    [switch]$KillDockerDesktop
)

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "   Stopping BetterAgent Microservices     " -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

if ($KillDockerDesktop) {
    Write-Host "Force killing Docker Desktop application and resetting WSL2..." -ForegroundColor Red
    taskkill /F /IM "Docker Desktop.exe" /T 2>$null
    taskkill /F /IM "com.docker.backend.exe" /T 2>$null
    taskkill /F /IM "com.docker.proxy.exe" /T 2>$null
    wsl --shutdown 2>$null
}

$ROOT_DIR = Get-Item -Path $PSScriptRoot\.. | Select-Object -ExpandProperty FullName
Set-Location $ROOT_DIR

$PID_FILE = "$ROOT_DIR\logs\run.pid"

# 1. Stop PIDs recorded in run.pid
if (Test-Path $PID_FILE) {
    $pidsToStop = Get-Content -Path $PID_FILE
    foreach ($pidNum in $pidsToStop) {
        $pidNum = $pidNum.Trim()
        if ($pidNum -and (Get-Process -Id $pidNum -ErrorAction SilentlyContinue)) {
            Write-Host "Stopping process PID: $pidNum ..." -ForegroundColor Yellow
            Stop-Process -Id $pidNum -Force -ErrorAction SilentlyContinue
        }
    }
    Remove-Item -Path $PID_FILE -Force -ErrorAction SilentlyContinue
    Write-Host "PIDs terminated and logs/run.pid removed." -ForegroundColor Green
} else {
    Write-Host "No logs/run.pid file found. Looking for active go/python microservices..." -ForegroundColor DarkGray
}

# 2. Clean up any orphaned microservices by process name / command line
Get-Process -Name "betteragent_core" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Get-Process -Name "nats-server" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

# 3. Clean up orphaned Go & Python microservices by WMI command line match
try {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { 
        $_.CommandLine -like "*services.*" -or 

        $_.CommandLine -like "*runner.py*" -or 
        $_.CommandLine -like "*cmd/main.go*" -or 
        $_.CommandLine -like "*betteragent_core*" -or 
        ($_.Name -eq "main.exe" -and $_.ExecutablePath -like "*go-build*")
    } | ForEach-Object {
        Write-Host "Stopping orphaned microservice PID: $($_.ProcessId) ($($_.Name)) ..." -ForegroundColor Yellow
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
} catch {}

# 4. Stop Docker Compose dependencies if present
if (Get-Command docker -ErrorAction SilentlyContinue) {
    Write-Host "Stopping Docker containers..." -ForegroundColor Yellow
    docker compose -f deploy/docker-compose.yml --env-file .env stop -t 5
}

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host " All BetterAgent services stopped cleanly. " -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Cyan
