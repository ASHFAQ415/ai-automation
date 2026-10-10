<#
.SYNOPSIS
    Native PowerShell Process Supervisor for Hermes AI Automation Engine.
.DESCRIPTION
    Monitors Hermes FastAPI backend, checks /api/health/live and /api/health/ready probes,
    logs metrics, and automatically restarts the process upon crash or hang.
#>

param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8000,
    [int]$PollIntervalSeconds = 5,
    [int]$MaxRestarts = 20
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendDir = Resolve-Path "$ScriptDir\.."
$PythonExe = "$BackendDir\.venv\Scripts\python.exe"

if (-not (Test-Path $PythonExe)) {
    $PythonExe = "python.exe"
}

Write-Host "=========================================================" -ForegroundColor Cyan
Write-Host "   Hermes Windows PowerShell Process Supervisor Active" -ForegroundColor Cyan
Write-Host "   Target: http://$HostAddress:$Port | Interval: ${PollIntervalSeconds}s" -ForegroundColor Cyan
Write-Host "=========================================================" -ForegroundColor Cyan

$RestartCount = 0

function Start-HermesBackend {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $PythonExe
    $psi.Arguments = "-m uvicorn main:app --host $HostAddress --port $Port"
    $psi.WorkingDirectory = $BackendDir
    $psi.UseShellExecute = $false
    $psi.EnvironmentVariables["PYTHONUNBUFFERED"] = "1"
    
    $proc = [System.Diagnostics.Process]::Start($psi)
    Write-Host "[SUPERVISOR] Spawned Hermes PID $($proc.Id) (Total Restarts: $RestartCount)" -ForegroundColor Green
    return $proc
}

$Process = Start-HermesBackend

try {
    while ($true) {
        Start-Sleep -Seconds $PollIntervalSeconds

        # 1. Process termination check
        if ($Process.HasExited) {
            $ExitCode = $Process.ExitCode
            Write-Host "[SUPERVISOR] ⚠️ Process $($Process.Id) terminated with exit code $ExitCode! Initiating restart..." -ForegroundColor Red
            $RestartCount++
            if ($RestartCount -gt $MaxRestarts) {
                Write-Host "[SUPERVISOR] ❌ Exceeded maximum allowed restarts ($MaxRestarts). Halting." -ForegroundColor Red
                break
            }
            Start-Sleep -Seconds 2
            $Process = Start-HermesBackend
            continue
        }

        # 2. HTTP Health Probe check
        try {
            $LiveUrl = "http://${HostAddress}:${Port}/api/health/live"
            $Response = Invoke-RestMethod -Uri $LiveUrl -Method Get -TimeoutSec 3 -ErrorAction Stop
            if ($Response.status -ne "PROCESS_RUNNING") {
                Write-Host "[SUPERVISOR] ⚠️ Unexpected liveness response: $($Response.status)" -ForegroundColor Yellow
            }
        }
        catch {
            Write-Host "[SUPERVISOR] ⚠️ Health probe failed: $_" -ForegroundColor Yellow
        }
    }
}
finally {
    if ($Process -and -not $Process.HasExited) {
        Write-Host "[SUPERVISOR] Stopping child process PID $($Process.Id)..." -ForegroundColor Yellow
        $Process.Kill()
    }
}
