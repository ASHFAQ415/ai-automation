"""Autonomous ReAct (Reason + Act) Agent Loop for Hermes.

Orchestrates multi-step reasoning, tool execution, persistent session memory,
and conversational synthesis powered by local Qwen 3 (4B) on RTX 3050.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Awaitable, Callable

from app.config import settings
from app.services.agent_tools import execute_tool, get_tools_prompt_description
from app.services.ai_engine import call_llm
from app.services.memory_service import format_history_for_prompt, record_message

logger = logging.getLogger("react_agent")

MAX_REACT_STEPS = 4

REACT_SYSTEM_PROMPT = """You are Hermes, the autonomous AI Engineering Director and Project Architect for Mohd Ashfaq.
You operate on an NVIDIA GeForce RTX 3050 running a dedicated local Qwen 3 (4B) model.
You manage the Autonomous AI Lead-to-Sales & Project Builder Engine.

{tools_description}

FORMAT INSTRUCTIONS:
To take an action using a tool, you MUST use the following exact format:
Thought: <explain your reasoning>
Action: <one of the available tool names>
Action Input: <valid JSON object containing the arguments>

Wait for the system to provide the Observation, then continue.
When you have collected all required information and are ready to answer Ashfaq, or if no tool is required, respond with:
Thought: <final reasoning>
Final Answer: <your complete, authoritative answer formatted in Telegram markdown>

RULES:
1. If the user asks about leads, projects, builds, telemetry, revenue, or actions (create, approve, build), ALWAYS use the appropriate tool first.
2. If the user asks a general engineering, architectural, or conversational question, you can provide a Final Answer directly.
3. Be concise, authoritative, professional, and clear. Format output using markdown (*bold*, `code`, bullet points).
4. NEVER invent lead IDs or project metrics. Always fetch them from tools.
"""


def _parse_action_and_input(text: str) -> tuple[str | None, dict[str, Any] | None]:
    """Extract tool name and JSON arguments from LLM output."""
    action_match = re.search(r"Action:\s*([a-zA-Z0-9_]+)", text)
    if not action_match:
        return None, None

    action = action_match.group(1).strip()

    # Find Action Input
    input_match = re.search(r"Action Input:\s*(\{.*\}|\[.*\]|.+)", text, re.DOTALL)
    args: dict[str, Any] = {}
    if input_match:
        raw_args = input_match.group(1).strip()
        # Clean potential markdown fences
        if "```" in raw_args:
            raw_args = re.sub(r"```(?:json)?", "", raw_args).strip()

        # Try JSON parsing
        try:
            parsed = json.loads(raw_args)
            if isinstance(parsed, dict):
                args = parsed
        except Exception:
            # Fallback regex extraction for simple key=val or single numeric argument
            num_match = re.search(r"\b(\d+)\b", raw_args)
            if num_match and ("lead" in action or "build" in action):
                key = "lead_id" if "lead" in action or action == "trigger_build" or action == "approve_plan" or action == "generate_invoice" else "build_id"
                args = {key: int(num_match.group(1))}

    return action, args


def _extract_final_answer(text: str) -> str | None:
    """Extract text following 'Final Answer:' if present."""
    match = re.search(r"Final Answer:\s*(.+)", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return None


async def run_react_agent(
    session_id: str,
    user_message: str,
    on_status: Callable[[str], Awaitable[None]] | None = None,
) -> str:
    """Execute autonomous ReAct loop with persistent memory and tool execution."""
    logger.info("Starting ReAct agent turn for session: %s | Message: %r", session_id, user_message[:100])

    # 1. Record incoming user turn in conversation memory
    await record_message(session_id=session_id, role="user", content=user_message)

    # 2. Retrieve recent dialog history
    history_context = await format_history_for_prompt(session_id=session_id, limit=6)

    # 3. Assemble base prompt
    system_prompt = REACT_SYSTEM_PROMPT.format(tools_description=get_tools_prompt_description())

    conversation_scratchpad = (
        f"PREVIOUS CONVERSATION:\n{history_context}\n\n"
        f"USER: {user_message}\n"
    )

    executed_tools: list[dict[str, Any]] = []
    executed_results: list[dict[str, Any]] = []
    final_response = ""

    # ReAct reasoning loop
    for step in range(1, MAX_REACT_STEPS + 1):
        prompt = (
            f"{conversation_scratchpad}\n"
            f"Step {step} Reasoning:\n"
        )

        try:
            raw_llm_out = await call_llm(prompt=prompt, system_prompt=system_prompt, max_tokens=800)
        except Exception as e:
            logger.exception("LLM call failed during ReAct step %d: %s", step, e)
            final_response = f"⚠️ Inference error encountered during step {step}: {str(e)[:150]}"
            break

        if not raw_llm_out:
            logger.warning("Empty response from LLM at step %d", step)
            final_response = "I was unable to complete reasoning for this request. Please try again."
            break

        # Check if model gave a Final Answer directly
        final_ans = _extract_final_answer(raw_llm_out)
        if final_ans:
            final_response = final_ans
            break

        # Check for tool action
        action, action_input = _parse_action_and_input(raw_llm_out)
        if not action:
            # If no action and no Final Answer tag, the model answered directly
            clean_out = raw_llm_out.replace("Thought:", "").strip()
            final_response = clean_out
            break

        # Notify callback of tool execution
        if on_status:
            tool_display = action.replace("_", " ").title()
            await on_status(f"⚙️ Running `{action}`...")

        executed_tools.append({"tool": action, "args": action_input})

        # Execute tool
        tool_result = await execute_tool(action, action_input)
        executed_results.append({"tool": action, "result": tool_result})

        obs_str = json.dumps(tool_result, ensure_ascii=False)
        if len(obs_str) > 1500:
            obs_str = obs_str[:1500] + "... [truncated]"

        # Append to scratchpad for next iteration
        conversation_scratchpad += (
            f"\nThought: {_extract_thought(raw_llm_out)}\n"
            f"Action: {action}\n"
            f"Action Input: {json.dumps(action_input)}\n"
            f"Observation: {obs_str}\n"
        )

    # If loop exhausted without explicit final answer, synthesize final answer
    if not final_response:
        summary_prompt = (
            f"{conversation_scratchpad}\n"
            "Synthesize a clear, authoritative Final Answer for Ashfaq based on the observations above."
        )
        try:
            final_response = await call_llm(prompt=summary_prompt, system_prompt=system_prompt, max_tokens=600)
            final_ans = _extract_final_answer(final_response)
            if final_ans:
                final_response = final_ans
        except Exception as e:
            final_response = f"Completed operations. Result: {json.dumps(executed_results[-1] if executed_results else {})}"

    # 4. Record assistant turn in conversation memory
    await record_message(
        session_id=session_id,
        role="assistant",
        content=final_response,
        tool_calls=executed_tools if executed_tools else None,
        tool_results=executed_results if executed_results else None,
    )

    return final_response


def _extract_thought(text: str) -> str:
    """Extract the Thought segment from the text."""
    m = re.search(r"Thought:\s*(.*?)(?=\nAction:|\nFinal Answer:|$)", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return "Executing action..."
