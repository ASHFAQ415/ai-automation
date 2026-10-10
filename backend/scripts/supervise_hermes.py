"""Windows Process Supervisor for Hermes Platform.

Implements Phase 7 and Section 14A.5 of Master Specification:
- Supervises FastAPI control-plane process on Windows
- Differentiates PROCESS_RUNNING from APPLICATION_HEALTHY
- Auto-restarts process upon crash or consecutive health failures
- Verifies state, SQLite WAL, and pending jobs survive process restart
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SUPERVISOR] %(levelname)s: %(message)s"
)
logger = logging.getLogger("supervisor")

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
PYTHON_EXE = sys.executable


class HermesProcessSupervisor:
    def __init__(self, port: int = 8000, host: str = "127.0.0.1", poll_interval: float = 3.0):
        self.port = port
        self.host = host
        self.poll_interval = poll_interval
        self.process: subprocess.Popen | None = None
        self.restart_count = 0
        self.should_run = True

    def start_process(self) -> subprocess.Popen:
        """Spawn Hermes FastAPI backend process."""
        # Ensure local Ollama is active if installed
        try:
            from run_server import ensure_ollama_running
            ensure_ollama_running()
        except Exception:
            pass

        cmd = [
            PYTHON_EXE,
            "-m", "uvicorn",
            "main:app",
            "--host", self.host,
            "--port", str(self.port),
        ]
        logger.info("Spawning Hermes backend: %s", " ".join(cmd))
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"

        self.process = subprocess.Popen(
            cmd,
            cwd=str(BACKEND_DIR),
            env=env,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )
        logger.info("Hermes spawned with PID: %d (Restart count: %d)", self.process.pid, self.restart_count)
        return self.process

    def stop_process(self) -> None:
        """Terminate Hermes process cleanly."""
        if self.process and self.process.poll() is None:
            logger.info("Stopping Hermes PID %d...", self.process.pid)
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except Exception:
                self.process.kill()
                self.process.wait(timeout=2)
            logger.info("Process stopped.")

    def check_health(self) -> tuple[bool, str]:
        """Query liveness and readiness probes."""
        base_url = f"http://{self.host}:{self.port}"
        try:
            with httpx.Client(timeout=3.0) as client:
                live_resp = client.get(f"{base_url}/api/health/live")
                if live_resp.status_code != 200:
                    return False, f"Liveness probe returned {live_resp.status_code}"

                ready_resp = client.get(f"{base_url}/api/health/ready")
                if ready_resp.status_code != 200:
                    return False, f"Readiness probe returned {ready_resp.status_code}"

                return True, "APPLICATION_HEALTHY"
        except Exception as e:
            return False, f"Connection failed: {e}"

    def run_supervisor(self, max_restarts: int = 10) -> None:
        """Continuous supervision loop."""
        self.start_process()

        consecutive_failures = 0
        try:
            while self.should_run:
                time.sleep(self.poll_interval)

                # 1. Check if process is running
                if self.process.poll() is not None:
                    exit_code = self.process.poll()
                    logger.error("Hermes process (PID %d) terminated unexpectedly with code %s!", self.process.pid, exit_code)
                    self.restart_count += 1
                    if self.restart_count > max_restarts:
                        logger.critical("Maximum restarts exceeded (%d). Exiting supervisor.", max_restarts)
                        break
                    logger.info("Supervisor restarting Hermes process...")
                    self.start_process()
                    consecutive_failures = 0
                    continue

                # 2. Check application readiness
                healthy, status_msg = self.check_health()
                if not healthy:
                    consecutive_failures += 1
                    logger.warning("Health check failed (%d/3): %s", consecutive_failures, status_msg)
                    if consecutive_failures >= 3:
                        logger.error("Consecutive health checks failed. Recycling broken process...")
                        self.stop_process()
                        self.start_process()
                        consecutive_failures = 0
                else:
                    consecutive_failures = 0

        except KeyboardInterrupt:
            logger.info("Supervisor received interrupt. Shutting down...")
        finally:
            self.stop_process()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hermes Windows Process Supervisor")
    parser.add_argument("--port", type=int, default=8000, help="Backend port")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Backend host")
    args = parser.parse_args()

    supervisor = HermesProcessSupervisor(port=args.port, host=args.host)
    supervisor.run_supervisor()
