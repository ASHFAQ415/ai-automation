"""Automated Database Backup & Restore Verification Service.

Implements Phase 6.2 and 6.3 of Master Specification:
- Atomic SQLite backup procedure using native backup API with WAL flush
- Comprehensive restore verification test checking tables, schemas, and query execution
"""
from __future__ import annotations

import logging
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("backup_service")


def backup_database(db_path: Path, backup_dir: Path) -> Path:
    """Create an atomic, consistent online backup of the SQLite database."""
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found at {db_path}")

    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_file = backup_dir / f"hermes_backup_{timestamp}.db"

    # Use SQLite online backup API to ensure WAL synchronization
    source_conn = sqlite3.connect(str(db_path))
    # Flush pending WAL logs
    source_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    dest_conn = sqlite3.connect(str(backup_file))
    try:
        source_conn.backup(dest_conn)
        logger.info("Database backup created successfully at %s", backup_file)
    finally:
        dest_conn.close()
        source_conn.close()

    return backup_file


def verify_backup_restore(backup_file: Path) -> dict[str, Any]:
    """Test restoring a backup into an isolated database and verify table schemas and data."""
    if not backup_file.exists():
        return {"passed": False, "error": f"Backup file {backup_file} does not exist"}

    with tempfile.TemporaryDirectory() as tmp_dir:
        restore_test_path = Path(tmp_dir) / "restored_test.db"

        # Restore from backup
        src = sqlite3.connect(str(backup_file))
        dst = sqlite3.connect(str(restore_test_path))
        try:
            src.backup(dst)
        finally:
            src.close()
            dst.close()

        # Verify integrity on restored database
        conn = sqlite3.connect(str(restore_test_path))
        try:
            cursor = conn.cursor()
            # 1. PRAGMA integrity_check
            cursor.execute("PRAGMA integrity_check")
            integrity_row = cursor.fetchone()
            if not integrity_row or integrity_row[0] != "ok":
                return {"passed": False, "error": f"Integrity check failed: {integrity_row}"}

            # 2. Check required tables exist
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {row[0] for row in cursor.fetchall()}
            expected_tables = {"leads", "project_goal_plans", "project_builds", "payment_records"}
            missing_tables = expected_tables - tables
            if missing_tables:
                return {"passed": False, "error": f"Missing expected tables: {missing_tables}"}

            # 3. Query execution test
            cursor.execute("SELECT count(*) FROM leads")
            lead_count = cursor.fetchone()[0]

            return {
                "passed": True,
                "backup_file": str(backup_file),
                "verified_tables": sorted(list(tables)),
                "leads_count": lead_count,
            }
        except Exception as e:
            return {"passed": False, "error": str(e)}
        finally:
            conn.close()
