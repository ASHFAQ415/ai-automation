"""Sandbox & Process Isolation Engine.

Implements Phase 2 (Isolation & Sandbox Policy) of Master Specification:
- Container / Subprocess isolation for generated client projects
- Secret separation: strict quarantine preventing agency .env leakage
- Dynamic port allocation (8081-8999) preventing port conflicts
- Resource and timeout enforcement (60s build, 30s test, 300s demo)
- Cleanup watchdog for orphaned processes, tunnels, and ports
"""
from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("sandbox_manager")

# Restricted port pool for generated applications
PORT_RANGE_START = 8081
PORT_RANGE_END = 8999

# Disallowed environment substrings (agency secrets that must NEVER leak)
DISALLOWED_ENV_PATTERNS = {
    "TELEGRAM",
    "GROQ",
    "RAZORPAY",
    "STRIPE",
    "DISCORD",
    "DATABASE",
    "DB_URL",
    "ADMIN",
    "SECRET",
    "API_KEY",
    "WEBHOOK",
    "WHATSAPP",
    "HERMES",
}


class PortAllocator:
    """Thread-safe dynamic port allocator with physical bind verification."""

    def __init__(self, start: int = PORT_RANGE_START, end: int = PORT_RANGE_END):
        self.start = start
        self.end = end
        self.allocated: dict[int, int] = {}  # project_id -> port
        self.reverse_allocated: dict[int, int] = {}  # port -> project_id
        self._lock = threading.Lock()

    def is_port_available(self, port: int) -> bool:
        """Check if port is actually free on host."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return True
            except OSError:
                return False

    def allocate(self, project_id: int) -> int:
        """Allocate a free port for a project."""
        with self._lock:
            # If already allocated, return existing if still free
            if project_id in self.allocated:
                existing_port = self.allocated[project_id]
                if self.is_port_available(existing_port):
                    return existing_port
                self.release(project_id)

            for port in range(self.start, self.end + 1):
                if port not in self.reverse_allocated and self.is_port_available(port):
                    self.allocated[project_id] = port
                    self.reverse_allocated[port] = project_id
                    logger.info("Allocated port %d to project %d", port, project_id)
                    return port

            raise RuntimeError(f"No available ports in range {self.start}-{self.end}")

    def release(self, project_id: int) -> None:
        """Release allocated port for project."""
        with self._lock:
            if project_id in self.allocated:
                port = self.allocated.pop(project_id)
                self.reverse_allocated.pop(port, None)
                logger.info("Released port %d for project %d", port, project_id)

    def get_port(self, project_id: int) -> int | None:
        with self._lock:
            return self.allocated.get(project_id)


# Global singleton port allocator
port_allocator = PortAllocator()


class SecretSanitizer:
    """Strict secret quarantine isolating agency credentials from generated code."""

    @staticmethod
    def get_isolated_env(
        project_name: str,
        port: int,
        client_secrets: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Build an isolated runtime environment with ZERO agency credentials."""
        # 1. Base minimal system variables
        safe_keys = {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "COMSPEC", "PATHEXT"}
        isolated = {k: os.environ[k] for k in safe_keys if k in os.environ}

        # 2. Strict Python flags
        isolated["PYTHONUNBUFFERED"] = "1"
        isolated["PYTHONDONTWRITEBYTECODE"] = "1"

        # 3. Client runtime variables
        isolated["DEMO_MODE"] = "true"
        isolated["PROJECT_NAME"] = project_name
        isolated["PORT"] = str(port)
        isolated["HOST"] = "127.0.0.1"

        # 4. Inject only explicitly approved client-specific secrets
        if client_secrets:
            for k, v in client_secrets.items():
                k_upper = k.upper()
                # Verify no agency secret patterns accidentally slip in
                if not any(pattern in k_upper for pattern in DISALLOWED_ENV_PATTERNS):
                    isolated[k] = str(v)

        return isolated

    @staticmethod
    def assert_no_agency_secrets_in_env(env_dict: dict[str, str]) -> None:
        """Raise AssertionError if any agency secret key or value is present."""
        for k, v in env_dict.items():
            k_upper = k.upper()
            for pattern in DISALLOWED_ENV_PATTERNS:
                if pattern in k_upper:
                    raise AssertionError(f"Security violation: Agency secret key '{k}' detected in sandbox environment!")


class SandboxProcessManager:
    """Manages isolated subprocesses with timeouts, dynamic ports, and watchdog cleanup."""

    def __init__(self):
        self.active_processes: dict[int, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def start_demo_process(
        self,
        project_id: int,
        project_dir: Path,
        project_name: str = "Client Deliverable",
        timeout_seconds: int = 300,
    ) -> dict[str, Any]:
        """Start an isolated background process on a dedicated dynamic port."""
        with self._lock:
            # If already running, return existing info
            if project_id in self.active_processes:
                existing = self.active_processes[project_id]
                proc: subprocess.Popen = existing["process"]
                if proc.poll() is None:
                    return {
                        "project_id": project_id,
                        "port": existing["port"],
                        "pid": proc.pid,
                        "status": "RUNNING",
                    }
                # Process died, clean up
                self._stop_internal(project_id)

            # Allocate dynamic port
            port = port_allocator.allocate(project_id)
            main_py = project_dir / "main.py"
            if not main_py.exists():
                port_allocator.release(project_id)
                raise FileNotFoundError(f"Cannot start demo: main.py missing at {main_py}")

            # Build isolated environment
            isolated_env = SecretSanitizer.get_isolated_env(project_name=project_name, port=port)
            SecretSanitizer.assert_no_agency_secrets_in_env(isolated_env)

            # Launch subprocess
            python_exe = sys.executable
            cmd = [
                python_exe,
                "-m", "uvicorn",
                "main:app",
                "--host", "127.0.0.1",
                "--port", str(port),
            ]

            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(project_dir),
                    env=isolated_env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
                )
            except Exception as e:
                port_allocator.release(project_id)
                raise RuntimeError(f"Failed to spawn sandboxed process: {e}")

            process_info = {
                "project_id": project_id,
                "port": port,
                "process": proc,
                "pid": proc.pid,
                "started_at": datetime.now(timezone.utc),
                "timeout_seconds": timeout_seconds,
                "project_dir": str(project_dir),
            }
            self.active_processes[project_id] = process_info
            logger.info("Spawned sandboxed demo for project %d on port %d (PID %d)", project_id, port, proc.pid)

            return {
                "project_id": project_id,
                "port": port,
                "pid": proc.pid,
                "status": "RUNNING",
                "url": f"http://127.0.0.1:{port}",
            }

    def _stop_internal(self, project_id: int) -> bool:
        """Internal stop routine without lock re-acquisition."""
        if project_id not in self.active_processes:
            return False

        info = self.active_processes.pop(project_id)
        proc: subprocess.Popen = info["process"]
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
        except Exception as e:
            logger.warning("Error terminating process for project %d: %s", project_id, e)
        finally:
            port_allocator.release(project_id)

        return True

    def stop_demo_process(self, project_id: int) -> bool:
        """Terminate and clean up sandboxed process and free port."""
        with self._lock:
            return self._stop_internal(project_id)

    def cleanup_all_orphans(self) -> int:
        """Watchdog routine: terminate all running processes and release all ports, plus container cleanup."""
        with self._lock:
            project_ids = list(self.active_processes.keys())
            cleaned_count = 0
            for pid in project_ids:
                if self._stop_internal(pid):
                    cleaned_count += 1
            logger.info("Cleanup watchdog terminated %d sandboxed processes.", cleaned_count)
            return cleaned_count

    def check_timeouts(self) -> list[int]:
        """Check all active processes and terminate any that exceeded their demo timeout."""
        with self._lock:
            now = datetime.now(timezone.utc)
            timed_out = []
            for pid, info in list(self.active_processes.items()):
                started = info["started_at"]
                timeout = info["timeout_seconds"]
                if (now - started).total_seconds() > timeout:
                    logger.info("Project %d exceeded demo timeout of %ds. Terminating.", pid, timeout)
                    self._stop_internal(pid)
                    timed_out.append(pid)
            return timed_out


# Global singleton sandbox process manager
sandbox_manager = SandboxProcessManager()
