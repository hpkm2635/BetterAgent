# PowerShell Start Script for BetterAgent (Windows)

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "   Starting BetterAgent Microservices     " -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

$ROOT_DIR = Get-Item -Path $PSScriptRoot\.. | Select-Object -ExpandProperty FullName
Set-Location $ROOT_DIR

# 0. Automatically stop any currently running instances to prevent duplicate processes
if (Test-Path "$PSScriptRoot\win_stop.ps1") {
    & "$PSScriptRoot\win_stop.ps1"
}

# Ensure logs and bin directories exist
if (-not (Test-Path "logs")) {
    New-Item -ItemType Directory -Path "logs" | Out-Null
}
if (-not (Test-Path "bin")) {
    New-Item -ItemType Directory -Path "bin" | Out-Null
}

$PIDS = @()

# 1. Start Docker or Native Windows NATS Server
if (Get-Command docker -ErrorAction SilentlyContinue) {
    Write-Host "[1/5] Starting Docker containers..." -ForegroundColor Yellow
    docker compose -f deploy/docker-compose.yml up -d
} elseif (Test-Path "$ROOT_DIR\bin\nats-server.exe") {
    Write-Host "[1/5] Starting native NATS Server (port 4222)..." -ForegroundColor Yellow
    $natsProc = Start-Process -FilePath "$ROOT_DIR\bin\nats-server.exe" -ArgumentList "-js", "-l", "$ROOT_DIR\logs\nats_server.log" -WindowStyle Hidden -PassThru
    $PIDS += $natsProc.Id
    Write-Host "     -> Native NATS Server PID: $($natsProc.Id) 🟢" -ForegroundColor Green
} else {
    Write-Host "[1/5] Neither Docker nor bin/nats-server.exe found. Running in offline fallback mode..." -ForegroundColor DarkGray
}

# Wait for NATS to become ready (port 4222) before starting dependent services
$natsReady = $false
for ($i = 0; $i -lt 20; $i++) {
    try {
        $tcp = New-Object System.Net.Sockets.TcpClient
        $tcp.Connect("127.0.0.1", 4222)
        $tcp.Close()
        $natsReady = $true
        break
    } catch {}
    Start-Sleep -Milliseconds 500
}
if (-not $natsReady) {
    Write-Host "     [!] NATS not ready after 10s, Python services may fail to connect" -ForegroundColor Yellow
}

# 2. Build and Start Go Core
Write-Host "[2/5] Building & Starting Go Core (betteragent_core.exe)..." -ForegroundColor Green
Push-Location "$ROOT_DIR\core"
go build -o "$ROOT_DIR\bin\betteragent_core.exe" ./cmd/main.go
Pop-Location

$goLog = "$ROOT_DIR\logs\betteragent_core.log"
$goLogErr = "$ROOT_DIR\logs\betteragent_core_err.log"
$goProc = Start-Process -FilePath "$ROOT_DIR\bin\betteragent_core.exe" -WorkingDirectory $ROOT_DIR -RedirectStandardOutput $goLog -RedirectStandardError $goLogErr -WindowStyle Hidden -PassThru
$PIDS += $goProc.Id
Write-Host "     -> Go Core PID: $($goProc.Id)" -ForegroundColor Gray

# 3. Start Python Memory Service directly (exact PID tracking)
Write-Host "[3/5] Starting Python Memory Service..." -ForegroundColor Green
$pyExe = "$ROOT_DIR\venv_win\Scripts\python.exe"
if (-not (Test-Path $pyExe)) {
    $pyExe = "python"
}
$memLogStdout = "$ROOT_DIR\logs\memory_service_stdout.log"
$memLogStderr = "$ROOT_DIR\logs\memory_service_stderr.log"
$memProc = Start-Process -FilePath $pyExe -ArgumentList "-u", "-m", "services.memory.main" -WorkingDirectory $ROOT_DIR -RedirectStandardOutput $memLogStdout -RedirectStandardError $memLogStderr -WindowStyle Hidden -PassThru
$PIDS += $memProc.Id
Write-Host "     -> Memory Service PID: $($memProc.Id)" -ForegroundColor Gray

# 4. Start Python Cognitive Service directly (exact PID tracking)
Write-Host "[4/5] Starting Python Cognitive Service..." -ForegroundColor Green
$cogLogStdout = "$ROOT_DIR\logs\cognitive_service_stdout.log"
$cogLogStderr = "$ROOT_DIR\logs\cognitive_service_stderr.log"
$cogProc = Start-Process -FilePath $pyExe -ArgumentList "-u", "-m", "services.cognitive.main" -WorkingDirectory $ROOT_DIR -RedirectStandardOutput $cogLogStdout -RedirectStandardError $cogLogStderr -WindowStyle Hidden -PassThru
$PIDS += $cogProc.Id
Write-Host "     -> Cognitive Service PID: $($cogProc.Id)" -ForegroundColor Gray

# Save PIDs to file
$PIDS | Out-File -FilePath "$ROOT_DIR\logs\run.pid" -Encoding ascii

# 5. Display MSFConsole-style ASCII Art icon from icon/ directory
$iconDir = "$ROOT_DIR\icon"
if (Test-Path $iconDir) {
    $iconFiles = Get-ChildItem -Path $iconDir -File | Where-Object { $_.Length -gt 0 }
    if ($iconFiles.Count -gt 0) {
        $selectedIcon = Get-Random -InputObject $iconFiles
        $artLines = Get-Content -Path $selectedIcon.FullName
        Write-Host ""
        $colors = @("Cyan", "Green", "Yellow", "Magenta")
        $colorIdx = 0
        foreach ($line in $artLines) {
            if ($line -match "^--+" -or $line -match "asciiart\.website") { continue }
            $curColor = $colors[$colorIdx % $colors.Count]
            Write-Host "   $line" -ForegroundColor $curColor
            $colorIdx++
        }
        Write-Host ""
    }
}

Write-Host "  ____  _____ _____ _____ _____ ____       _     ____ _____ _   _ _____ " -ForegroundColor Cyan
Write-Host " | __ )| ____|_   _|_   _| ____|  _ \     / \   / ___| ____| \ | |_   _|" -ForegroundColor Cyan
Write-Host " |  _ \|  _|   | |   | | |  _| | |_) |   / _ \ | |  _|  _| |  \| | | |  " -ForegroundColor Green
Write-Host " | |_) | |___  | |   | | | |___|  _ <   / ___ \| |_| | |___| |\  | | |  " -ForegroundColor Green
Write-Host " |____/|_____| |_|   |_| |_____|_| \_\ /_/   \_\____|_____|_| \_| |_|  " -ForegroundColor Yellow
Write-Host ""
Write-Host "=========================================================================" -ForegroundColor DarkGray
Write-Host " 🟢 All BetterAgent microservices launched successfully!" -ForegroundColor Green
Write-Host " 📌 Running PIDs stored in logs/run.pid" -ForegroundColor Gray
Write-Host " 📄 Output logs stored in logs/ directory" -ForegroundColor Gray
Write-Host " 🛑 Use .\scripts\win_stop.ps1 to stop all services." -ForegroundColor Yellow
Write-Host "=========================================================================" -ForegroundColor DarkGray
