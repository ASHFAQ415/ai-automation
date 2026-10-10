"""AutoGPT-inspired Block Architecture for Hermes.

Each Block is a self-contained, typed action unit with Pydantic input/output schemas.
Blocks are composable into DAGs and callable by Dots via the ReAct loop.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar, Type

from pydantic import BaseModel, ConfigDict

logger = logging.getLogger("blocks")


# ---------------------------------------------------------------------------
# Base schemas
# ---------------------------------------------------------------------------

class BlockSchema(BaseModel):
    """Base class for all Block input/output schemas."""
    model_config = ConfigDict(extra="allow")


class BlockCategory(str, Enum):
    DATABASE = "database"
    AI = "ai"
    NOTIFICATION = "notification"
    CODE = "code"
    WEB = "web"
    SKILL = "skill"
    CONTROL = "control"
    UTILITY = "utility"


# ---------------------------------------------------------------------------
# Execution metadata
# ---------------------------------------------------------------------------

class BlockStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class BlockExecutionResult:
    block_id: str
    block_name: str
    status: BlockStatus
    output: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    duration_ms: float = 0.0
    step: int = 0


# ---------------------------------------------------------------------------
# Abstract Block
# ---------------------------------------------------------------------------

class Block(ABC):
    """Abstract base for all Hermes Blocks.

    Inspired by AutoGPT's Block architecture — each block has:
    - Typed Pydantic input/output schemas
    - A single `run()` async method
    - Metadata for discovery and routing
    - A `test_input()` for validation

    Usage:
        block = QueryLeadsBlock()
        result = await block.run(QueryLeadsInput(limit=5))
    """

    # Subclasses MUST define these class vars
    id: ClassVar[str]           # e.g. "query_leads"
    name: ClassVar[str]         # e.g. "Query Leads"
    description: ClassVar[str]  # human-readable purpose
    category: ClassVar[BlockCategory]
    input_schema: ClassVar[Type[BlockSchema]]
    output_schema: ClassVar[Type[BlockSchema]]

    @abstractmethod
    async def run(self, input_data: BlockSchema) -> BlockSchema:
        """Execute the Block with typed input and return typed output."""
        ...

    def test_input(self) -> BlockSchema:
        """Return a safe default input for smoke-testing this Block."""
        return self.input_schema()

    def to_dict(self) -> dict[str, Any]:
        """Serialize block metadata for LLM tool descriptions."""
        try:
            schema = self.input_schema.model_json_schema()
            props = schema.get("properties", {})
        except Exception:
            props = {}

        params = []
        for param_name, param_meta in props.items():
            params.append({
                "name": param_name,
                "type": param_meta.get("type", "any"),
                "description": param_meta.get("description", ""),
                "required": param_name in schema.get("required", []),
            })

        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "category": self.category.value,
            "parameters": params,
        }

    def format_for_prompt(self) -> str:
        """Format this block as an LLM-readable tool description."""
        try:
            schema = self.input_schema.model_json_schema()
            props = schema.get("properties", {})
        except Exception:
            props = {}

        params_str = ", ".join(
            f"{k}: {v.get('type', 'any')}" for k, v in props.items()
        )
        return f"- `{self.id}({params_str})` — {self.description}"


# ---------------------------------------------------------------------------
# Block Registry
# ---------------------------------------------------------------------------

class BlockRegistry:
    """Singleton registry of all available Blocks."""
    _registry: dict[str, Block] = {}

    @classmethod
    def register(cls, block: Block) -> None:
        cls._registry[block.id] = block
        logger.debug("Registered block: %s", block.id)

    @classmethod
    def get(cls, block_id: str) -> Block | None:
        return cls._registry.get(block_id)

    @classmethod
    def all(cls) -> list[Block]:
        return list(cls._registry.values())

    @classmethod
    def all_by_category(cls, category: BlockCategory) -> list[Block]:
        return [b for b in cls._registry.values() if b.category == category]

    @classmethod
    def get_tools_description(cls) -> str:
        """Format all registered blocks as an LLM system prompt tool list."""
        lines = ["AVAILABLE TOOLS (Blocks):"]
        by_cat: dict[str, list[str]] = {}
        for block in cls._registry.values():
            cat = block.category.value.upper()
            by_cat.setdefault(cat, []).append(block.format_for_prompt())
        for cat, items in by_cat.items():
            lines.append(f"\n[{cat}]")
            lines.extend(items)
        return "\n".join(lines)

    @classmethod
    async def execute(
        cls,
        block_id: str,
        raw_input: dict[str, Any],
        step: int = 0,
    ) -> BlockExecutionResult:
        """Find and run a block by ID, wrapping with execution metadata."""
        import time

        block = cls.get(block_id)
        if not block:
            return BlockExecutionResult(
                block_id=block_id,
                block_name=block_id,
                status=BlockStatus.FAILED,
                error=f"Block '{block_id}' not found in registry",
                step=step,
            )

        t0 = time.perf_counter()
        try:
            typed_input = block.input_schema(**raw_input)
            result = await block.run(typed_input)
            duration = (time.perf_counter() - t0) * 1000
            return BlockExecutionResult(
                block_id=block.id,
                block_name=block.name,
                status=BlockStatus.COMPLETED,
                output=result.model_dump(),
                duration_ms=round(duration, 2),
                step=step,
            )
        except Exception as exc:
            duration = (time.perf_counter() - t0) * 1000
            logger.exception("Block '%s' failed: %s", block_id, exc)
            return BlockExecutionResult(
                block_id=block.id if hasattr(block, "id") else block_id,
                block_name=block.name if hasattr(block, "name") else block_id,
                status=BlockStatus.FAILED,
                error=str(exc),
                duration_ms=round(duration, 2),
                step=step,
            )


def register_block(block_instance: Block) -> Block:
    """Decorator helper to auto-register a block instance."""
    BlockRegistry.register(block_instance)
    return block_instance
