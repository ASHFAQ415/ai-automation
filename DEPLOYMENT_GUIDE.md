# Hermes Platform — Production Deployment & Process Supervision Guide

This guide details the two-stage deployment architecture for the Hermes Autonomous AI Software Agency platform per Section 10 and Section 14A.5 of the Master Specification.

---

## Stage 1: Local Windows Deployment (NSSM & Supervisor)

### 1.1 Process Supervision Architecture
On Windows, crash resilience requires active process supervision to ensure automatic restart and persistence of ongoing jobs.

The platform provides two supervisor options:
1. **NSSM (Non-Sucking Service Manager)**: Installs Hermes as a native Windows Service that boots on system startup and restarts automatically upon unhandled crashes.
2. **Built-in Python Supervisor (`backend/scripts/supervise_hermes.py`)**: A continuous process monitor that polls `/api/health/live` and `/api/health/ready`, restarting the FastAPI application upon unexpected termination or degraded readiness.

### 1.2 Distinguishing Process State vs Application Health
* **Liveness Probe (`GET /api/health/live`)**:
  - Confirms the Python/FastAPI process is alive (`PROCESS_RUNNING`).
  - Returns `200 OK` with JSON `{"status": "PROCESS_RUNNING"}`.
* **Readiness Probe (`GET /api/health/ready`)**:
  - Confirms application health (`APPLICATION_HEALTHY`).
  - Verifies:
    - SQLite database read/write access.
    - SQLite WAL mode is active (`PRAGMA journal_mode = wal`).
    - Projects storage directory is writable.
  - Returns `200 OK` when healthy, or `503 Service Unavailable` if degraded.

### 1.3 NSSM Windows Service Setup Instructions
To register Hermes as a permanent Windows Service:
```cmd
REM 1. Download nssm.exe and place in C:\tools\nssm\ or add to PATH
REM 2. Install Hermes as a Windows Service:
nssm install HermesBackend "D:\New folder (3)\ai automation\backend\.venv\Scripts\python.exe" "D:\New folder (3)\ai automation\backend\scripts\supervise_hermes.py --port 8000"

REM 3. Configure working directory:
nssm set HermesBackend AppDirectory "D:\New folder (3)\ai automation\backend"

REM 4. Configure automatic restart on crash:
nssm set HermesBackend AppRestartDelay 3000
nssm set HermesBackend AppStdout "D:\New folder (3)\ai automation\backend\hermes_service.log"
nssm set HermesBackend AppStderr "D:\New folder (3)\ai automation\backend\hermes_service_err.log"

REM 5. Start the service:
nssm start HermesBackend
```

### 1.4 Ollama & Local GPU LLM Service Setup
Hermes leverages local NVIDIA GPU acceleration (RTX 3050) via Ollama running `qwen3:4b`:
1. **Built-in Auto-Start**:
   `backend/run_server.py` and `backend/scripts/supervise_hermes.py` automatically detect if port `11434` is open. If closed, they automatically spawn `ollama.exe serve` in the background.
2. **Dedicated NSSM Service (Optional for 24/7 Autonomy)**:
   To ensure Ollama stays alive independently of the application process:
   ```cmd
   nssm install OllamaDaemon "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" "serve"
   nssm set OllamaDaemon AppDirectory "%LOCALAPPDATA%\Programs\Ollama"
   nssm set OllamaDaemon AppRestartDelay 2000
   nssm start OllamaDaemon
   ```
3. **Verify Local Model**:
   ```cmd
   ollama list
   REM Ensure qwen3:4b is pulled:
   ollama run qwen3:4b "Respond with READY"
   ```

### 1.5 Multi-Tier Hybrid LLM Resilience Architecture
To eliminate single points of failure during project generation:
- **Tier 1 (Primary)**: Local Ollama `qwen3:4b` (port 11434, RTX 3050 GPU VRAM). Fast 3-second connect timeout before failing over.
- **Tier 2 (Cloud Fallback)**: Groq Cloud API with model cascade:
  1. `openai/gpt-oss-120b` (Default: supports 4,096 output tokens without hitting OTPM rate limits).
  2. `openai/gpt-oss-20b` (Secondary high-throughput fallback).
  3. `qwen/qwen3.8-27b` (Clamped to 1,000 output tokens to strictly adhere to Groq rate tiers).
- **Tier 3 (Deterministic Heuristic Fallback)**: If both local and cloud LLMs are unreachable, Hermes falls back to certified template synthesis and AST syntax validation, guaranteeing that deliverables are generated and pass verification gates without aborting.

---

## Stage 2: Hosted Linux VPS Deployment (Production Scalability)

When commercial volume justifies moving to dedicated cloud hosting:

### 2.1 VPS Infrastructure Stack
* **OS**: Ubuntu 22.04 / 24.04 LTS
* **Database**: PostgreSQL 16 with daily `pg_dump` snapshots
* **Reverse Proxy & Ingress**: Cloudflare Named Tunnel (`cloudflared`) with zero exposed open ports
* **Process Supervisor**: Linux `systemd`

### 2.2 Systemd Unit File (`/etc/systemd/system/hermes.service`)
```ini
[Unit]
Description=Hermes Autonomous Software Agency Platform
After=network.target postgresql.service

[Service]
Type=simple
User=hermes
WorkingDirectory=/opt/hermes/backend
EnvironmentFile=/opt/hermes/backend/.env
# Note: Use --workers 1 when Telegram bot runs in long-polling mode to prevent Telegram 409 Conflict errors
ExecStart=/opt/hermes/backend/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1
Restart=always
RestartSec=3s
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
```

### 2.3 Cloudflare Named Tunnel Setup
```bash
# 1. Authenticate Cloudflare Tunnel
cloudflared tunnel login

# 2. Create Named Tunnel
cloudflared tunnel create hermes-agency

# 3. Configure Tunnel Route (/etc/cloudflared/config.yml)
tunnel: <TUNNEL_UUID>
credentials-file: /etc/cloudflared/<TUNNEL_UUID>.json

ingress:
  - hostname: agency.yourdomain.com
    service: http://localhost:8000
  - service: http_status:404

# 4. Route DNS and run tunnel as a service
cloudflared tunnel route dns hermes-agency agency.yourdomain.com
sudo cloudflared service install
```

### 2.4 Disaster Recovery & Data Backup Strategy
* **Automated Daily Backups**: Scheduled at 02:00 UTC via cron:
  ```bash
  0 2 * * * /opt/hermes/backend/scripts/backup.sh > /var/log/hermes_backup.log 2>&1
  ```
* **Restore Verification**:
  Every backup is validated using `verify_backup_restore()` to ensure table schemas and records remain intact before archiving.

---

## Stage 3: Podman Container Isolation on Windows (WSL2 Backend)

For enterprise-grade container isolation of generated builds without root privileges:

### 3.1 Prerequisites & Installation
1. **Enable WSL2 (Windows Subsystem for Linux)**:
   ```powershell
   wsl --install
   wsl --set-default-version 2
   ```
2. **Install Podman Desktop**:
   Install via Windows Package Manager:
   ```powershell
   winget install RedHat.Podman-Desktop
   ```
   Or download the official Windows installer from [podman-desktop.io](https://podman-desktop.io).

3. **Initialize and Start the Podman Machine**:
   Open PowerShell or Windows Terminal:
   ```powershell
   podman machine init --cpus 2 --memory 4096 --disk-size 20
   podman machine start
   ```

4. **Verify Container Engine Accessibility**:
   ```powershell
   podman --version
   podman info
   ```

### 3.2 Hermes ContainerManager Behavior
Once `podman` is available on the system PATH:
- **Automatic Detection**: Hermes `ContainerManager` detects the active runtime and delegates build execution, syntax verification, and test suites into ephemeral rootless containers.
- **Resource Sandboxing**: CPU caps (1.5 cores), memory limits (1.5 GB), process caps (`--pids-limit 100`), and tmpfs scratch space (`/tmp:rw,noexec,nosuid,size=256m`) are strictly enforced.
- **Host Isolation**: Zero volume mounts to agency `.env`, SQLite databases, or other project directories.
- **Graceful Fallback**: If Podman is not running, Hermes falls back to its deterministic host sandbox managed by `SecretSanitizer` and `SandboxProcessManager`.

---

## Stage 4: Production Verification & Smoke Testing Playbook

After starting the service via NSSM, Systemd, or `backend/run_server.py`, run these verification checks:

### 4.1 Probe Health Endpoints
```powershell
# 1. Check Liveness (FastAPI process alive)
curl -i http://localhost:8000/api/health/live

# 2. Check Readiness (SQLite WAL mode + storage read/write)
curl -i http://localhost:8000/api/health/ready
```
*Expected: Both return `HTTP 200 OK`.*

### 4.2 Verify LLM Availability
```powershell
# Check local Ollama daemon
curl -i http://127.0.0.1:11434/api/tags
```
*If Ollama is offline or unavailable, the backend automatically transitions to Groq Cloud (`openai/gpt-oss-120b`).*

### 4.3 Verify Telegram Bot Ingress
1. Open Telegram and message `@Preetikaa_bot`.
2. Send `/status` or `/leads`.
3. Confirm bot returns system status and lead inventory.

### 4.4 Verify Web Dashboard & Authentication
1. Open `http://localhost:8000` in a browser.
2. Confirm the dashboard loads live metrics, lead pipeline, and graph topology.
3. Test triggering a project build via the web UI ("Build Deliverable" button).
4. Inspect the generated deliverable in `projects/` and check that it passes the 13 enterprise verification gates.


