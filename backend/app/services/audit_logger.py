"""Immutable Audit Logger & Cryptographic Integrity Verification Engine.

Per Phase 9 / Section 14A.6 of Master Specification:
- Cryptographically chained audit ledger (tamper-evident SHA-256 hash chaining)
- Records actor, action, target, before/after state transitions, and timestamps
- Verifies blockchain-style audit ledger integrity
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditEntry

logger = logging.getLogger("audit_logger")

GENESIS_HASH = "0" * 64

# C-3 Fix: serialize all audit writes to prevent hash-chain race condition
# Two concurrent events reading the same previous_hash would fork the chain permanently.
_audit_lock = asyncio.Lock()


def canonical_json(data: Any) -> str:
    """Produce deterministic, sort-keyed JSON string."""
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    try:
        return json.dumps(data, sort_keys=True, default=str)
    except Exception:
        return str(data)


def format_timestamp(dt: datetime) -> str:
    """Produce deterministic UTC timestamp string matching database roundtrip format."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def compute_entry_hash(
    timestamp_str: str,
    actor_type: str,
    actor_id: str,
    action: str,
    target_type: str,
    target_id: str,
    before_state_str: str,
    after_state_str: str,
    previous_hash: str,
) -> str:
    """Calculate SHA-256 digest linking this entry cryptographically to the prior entry."""
    payload = (
        f"{timestamp_str}|{actor_type}|{actor_id}|{action}|"
        f"{target_type}|{target_id}|{before_state_str}|{after_state_str}|{previous_hash}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def log_audit_event(
    db: AsyncSession,
    actor_type: str,
    actor_id: str,
    action: str,
    target_type: str,
    target_id: str | int,
    before_state: Any = None,
    after_state: Any = None,
) -> AuditEntry:
    """Record an immutable, hash-chained audit event.

    C-3 Fix: Serialized under _audit_lock to prevent concurrent writes from
    forking the hash chain (both reading same previous_hash).
    """
    async with _audit_lock:
        # 1. Fetch previous entry to obtain chaining hash
        last_entry_res = await db.execute(
            select(AuditEntry).order_by(desc(AuditEntry.id)).limit(1)
        )
        last_entry = last_entry_res.scalar_one_or_none()
        previous_hash = last_entry.entry_hash if last_entry else GENESIS_HASH

        now = datetime.now(timezone.utc)
        now_str = format_timestamp(now)
        target_id_str = str(target_id)
        before_str = canonical_json(before_state)
        after_str = canonical_json(after_state)

        entry_hash = compute_entry_hash(
            timestamp_str=now_str,
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id_str,
            before_state_str=before_str,
            after_state_str=after_str,
            previous_hash=previous_hash,
        )

        entry = AuditEntry(
            timestamp=now,
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id_str,
            before_state=before_str,
            after_state=after_str,
            previous_hash=previous_hash,
            entry_hash=entry_hash,
        )
        db.add(entry)
        await db.commit()
        await db.refresh(entry)

        logger.info(
            "Audit event recorded: action=%s actor=%s target=%s:%s (Hash: %s...)",
            action, actor_id, target_type, target_id_str, entry_hash[:12]
        )
        return entry


async def verify_audit_integrity(db: AsyncSession) -> dict[str, Any]:
    """Verify cryptographic integrity of the entire audit chain.

    Ensures no record has been inserted, updated, or removed out-of-band.
    """
    res = await db.execute(select(AuditEntry).order_by(AuditEntry.id.asc()))
    records = res.scalars().all()

    expected_prev = GENESIS_HASH
    corruptions = []

    for entry in records:
        # Check chaining consistency
        if entry.previous_hash != expected_prev:
            corruptions.append({
                "entry_id": entry.id,
                "error": f"Previous hash mismatch. Expected {expected_prev[:12]}, got {entry.previous_hash[:12]}",
            })

        # Check self-hash computation
        recomputed = compute_entry_hash(
            timestamp_str=format_timestamp(entry.timestamp),
            actor_type=entry.actor_type,
            actor_id=entry.actor_id,
            action=entry.action,
            target_type=entry.target_type,
            target_id=entry.target_id,
            before_state_str=entry.before_state or "",
            after_state_str=entry.after_state or "",
            previous_hash=entry.previous_hash,
        )

        if recomputed != entry.entry_hash:
            corruptions.append({
                "entry_id": entry.id,
                "error": f"Entry hash tamper detected. Expected {recomputed[:12]}, recorded {entry.entry_hash[:12]}",
            })

        expected_prev = entry.entry_hash

    is_valid = len(corruptions) == 0
    return {
        "valid": is_valid,
        "total_records": len(records),
        "corruptions": corruptions,
        "last_hash": expected_prev,
    }
