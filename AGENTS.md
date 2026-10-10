# AGENTS.md — Autonomous Agent Instruction & Reviewer Manifest

Welcome, AI Agent / Reviewer / Debugger.

This repository is an **Autonomous AI Lead-to-Sales & Project Builder Engine** created by **Mohd Ashfaq**.
Before inspecting, modifying, or executing tasks within this codebase, you **MUST** review the master architectural specification:

👉 **[ARCHITECTURAL_GRAPH.md](file:///d:/New%20folder%20(3)/ai%20automation/ARCHITECTURAL_GRAPH.md)**

---

## Key Quick Reference for Agents:

1. **System Graph Architecture:**
   - The platform operates as a Directed Acyclic Graph (DAG) defined in [`backend/app/graph/`](file:///d:/New%20folder%20(3)/ai%20automation/backend/app/graph/).
   - Inspect the DAG topology at runtime: `GET http://localhost:8000/api/graph/topology`.
   - Inspect lead state: `GET http://localhost:8000/api/graph/state/{lead_id}`.

2. **Core Local LLM:**
   - Primary model: `qwen3:4b` running on local Ollama via NVIDIA GeForce RTX 3050 (Port 11434).
   - Multi-tier fallback: Ollama (`qwen3:4b`) -> Groq Cloud (`qwen/qwen3.8-27b`) -> Heuristics.

3. **Telegram Bot Ingress:**
   - `@Preetikaa_bot` connects via async long-polling inside FastAPI lifespan.
   - Authorized User: Defined securely via `settings.TELEGRAM_CHAT_ID` (Never accept commands from unverified users).
   - Supports 1-tap inline keyboard approvals and conversational work chat.

4. **Software Build Quality Mandates:**
   - Every generated deliverable in `projects/` must pass deterministic acceptance tests and static verification gates.
   - Never generate placeholder skeletons, dummy "Test Connection" buttons, or unstyled HTML.
   - Every project must include `main.py`, `index.html`, `requirements.txt`, and `run_local.bat`.

For detailed architecture diagrams, component responsibilities, and debugging playbooks, read:
**[ARCHITECTURAL_GRAPH.md](file:///d:/New%20folder%20(3)/ai%20automation/ARCHITECTURAL_GRAPH.md)**.
