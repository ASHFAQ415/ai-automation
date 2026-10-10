"""Conversation Memory Service: Persistent multi-turn session storage and context retrieval."""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import desc, select

from app.database import AsyncSessionLocal
from app.models import ConversationMemory

logger = logging.getLogger("memory_service")


async def record_message(
    session_id: str,
    role: str,
    content: str,
    tool_calls: list[dict[str, Any]] | dict[str, Any] | None = None,
    tool_results: list[dict[str, Any]] | dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> int:
    """Persist a conversation turn into durable SQLite storage."""
    async with AsyncSessionLocal() as db:
        mem = ConversationMemory(
            session_id=str(session_id),
            role=role,
            content=content,
            tool_calls=json.dumps(tool_calls) if tool_calls else None,
            tool_results=json.dumps(tool_results) if tool_results else None,
            metadata_json=json.dumps(metadata) if metadata else None,
        )
        db.add(mem)
        await db.commit()
        await db.refresh(mem)
        return mem.id


async def get_history(session_id: str, limit: int = 10) -> list[dict[str, Any]]:
    """Retrieve chronologically ordered message history for a session."""
    async with AsyncSessionLocal() as db:
        stmt = (
            select(ConversationMemory)
            .where(ConversationMemory.session_id == str(session_id))
            .order_by(desc(ConversationMemory.created_at))
            .limit(limit)
        )
        result = await db.execute(stmt)
        rows = result.scalars().all()
        # Reverse to chronological order (oldest to newest)
        chronological = list(reversed(rows))
        history: list[dict[str, Any]] = []
        for r in chronological:
            history.append({
                "id": r.id,
                "role": r.role,
                "content": r.content,
                "tool_calls": json.loads(r.tool_calls) if r.tool_calls else None,
                "tool_results": json.loads(r.tool_results) if r.tool_results else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            })
        return history


async def format_history_for_prompt(session_id: str, limit: int = 8) -> str:
    """Format recent dialog history into a clean string for LLM prompting."""
    history = await get_history(session_id=session_id, limit=limit)
    if not history:
        return "No previous conversation history."

    lines: list[str] = []
    for item in history:
        role = item["role"].capitalize()
        content = item["content"].strip()
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


async def clear_history(session_id: str) -> int:
    """Clear conversation history for a given session."""
    async with AsyncSessionLocal() as db:
        stmt = (
            select(ConversationMemory)
            .where(ConversationMemory.session_id == str(session_id))
        )
        result = await db.execute(stmt)
        rows = result.scalars().all()
        count = len(rows)
        for r in rows:
            await db.delete(r)
        await db.commit()
        return count
