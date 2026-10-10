"""Tests for Phase 2 & Phase 3: Persistent Memory, Tool Registry, and ReAct Agent."""
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, patch

from app.database import AsyncSessionLocal, init_db
from app.models import Lead, ConversationMemory
from app.services.memory_service import clear_history, format_history_for_prompt, get_history, record_message
from app.services.agent_tools import (
    execute_tool,
    tool_get_daily_brief,
    tool_get_lead_details,
    tool_get_revenue_summary,
    tool_get_system_telemetry,
    tool_query_leads,
)
from app.services.react_agent import _extract_final_answer, _parse_action_and_input, run_react_agent


@pytest.mark.asyncio
async def test_conversation_memory_crud():
    await init_db()
    session_id = "test_sess_42"
    # Clean previous
    await clear_history(session_id)

    # 1. Record user message
    mid1 = await record_message(session_id=session_id, role="user", content="Hello Hermes!")
    assert mid1 > 0

    # 2. Record assistant message with tool calls
    mid2 = await record_message(
        session_id=session_id,
        role="assistant",
        content="Checking telemetry...",
        tool_calls=[{"tool": "get_system_telemetry", "args": {}}],
        tool_results=[{"tool": "get_system_telemetry", "result": {"local_llm_status": "ONLINE"}}],
    )
    assert mid2 > mid1

    # 3. Retrieve history
    hist = await get_history(session_id=session_id, limit=5)
    assert len(hist) == 2
    assert hist[0]["role"] == "user"
    assert hist[0]["content"] == "Hello Hermes!"
    assert hist[1]["role"] == "assistant"
    assert hist[1]["tool_calls"][0]["tool"] == "get_system_telemetry"

    # 4. Format history for prompt
    formatted = await format_history_for_prompt(session_id=session_id)
    assert "User: Hello Hermes!" in formatted
    assert "Assistant: Checking telemetry..." in formatted

    # 5. Clear history
    deleted = await clear_history(session_id)
    assert deleted == 2
    assert len(await get_history(session_id)) == 0


@pytest.mark.asyncio
async def test_agent_tools_execution():
    await init_db()

    # 1. Test telemetry
    telemetry = await tool_get_system_telemetry()
    assert "local_llm_status" in telemetry
    assert "hardware" in telemetry
    assert "RTX 3050" in telemetry["hardware"]

    # 2. Test revenue summary
    rev = await tool_get_revenue_summary()
    assert "total_collected_inr" in rev
    assert "currency" in rev
    assert rev["currency"] == "INR"

    # 3. Test daily brief
    brief = await tool_get_daily_brief()
    assert "timestamp" in brief
    assert "hot_deals" in brief

    # 4. Test query_leads via dispatcher
    leads_res = await execute_tool("query_leads", {"limit": 3})
    assert "leads" in leads_res
    assert isinstance(leads_res["leads"], list)

    # 5. Test unknown tool handling
    err = await execute_tool("non_existent_tool_xyz")
    assert "error" in err


def test_react_agent_parsers():
    # 1. Test action & input parsing
    sample_text = """Thought: I should query the leads database to see recent status.
Action: query_leads
Action Input: {"status": "HOT", "limit": 2}
"""
    action, args = _parse_action_and_input(sample_text)
    assert action == "query_leads"
    assert args == {"status": "HOT", "limit": 2}

    # 2. Test action with markdown fences in input
    sample_fence = """Thought: Checking details.
Action: get_lead_details
Action Input: ```json
{"lead_id": 12}
```
"""
    action, args = _parse_action_and_input(sample_fence)
    assert action == "get_lead_details"
    assert args == {"lead_id": 12}

    # 3. Test Final Answer extraction
    sample_answer = """Thought: I have the metrics now.
Final Answer: All systems are operational on RTX 3050 with 0 stuck builds.
"""
    ans = _extract_final_answer(sample_answer)
    assert ans == "All systems are operational on RTX 3050 with 0 stuck builds."


@pytest.mark.asyncio
async def test_react_agent_multi_step_loop():
    await init_db()
    session_id = "test_loop_99"
    await clear_history(session_id)

    # Simulate 2 steps: Step 1 calls get_system_telemetry, Step 2 gives Final Answer
    step1_output = """Thought: Ashfaq wants system telemetry. I will query the telemetry tool.
Action: get_system_telemetry
Action Input: {}
"""
    step2_output = """Thought: I received the system metrics.
Final Answer: Local Ollama is *ONLINE* on NVIDIA RTX 3050.
"""

    mock_llm_responses = [step1_output, step2_output]
    call_index = 0

    async def mock_call_llm(*args, **kwargs):
        nonlocal call_index
        resp = mock_llm_responses[min(call_index, len(mock_llm_responses) - 1)]
        call_index += 1
        return resp

    statuses = []
    async def mock_status(msg: str):
        statuses.append(msg)

    with patch("app.services.react_agent.call_llm", side_effect=mock_call_llm):
        result = await run_react_agent(
            session_id=session_id,
            user_message="How is the GPU and AI engine doing?",
            on_status=mock_status,
        )

    assert "Local Ollama is *ONLINE*" in result
    assert any("get_system_telemetry" in s for s in statuses)

    # Verify conversation memory contains both turns
    history = await get_history(session_id)
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[1]["role"] == "assistant"
    assert history[1]["tool_calls"] is not None
    assert history[1]["tool_calls"][0]["tool"] == "get_system_telemetry"
