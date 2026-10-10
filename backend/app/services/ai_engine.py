"""Multi-tier AI Engine: Local Ollama (Qwen) -> Groq API -> Heuristics."""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger("ai_engine")


async def call_llm(prompt: str, system_prompt: str | None = None, max_tokens: int = 4096) -> str:
    """Call LLM with fallback: Ollama (Local Qwen) -> Groq Multi-Model API."""
    # Tier 1: Local Ollama (Qwen on RTX 3050)
    try:
        payload = {
            "model": settings.OLLAMA_MODEL,
            "prompt": f"{system_prompt}\n\n{prompt}" if system_prompt else prompt,
            "stream": False,
            "options": {
                "temperature": 0.2,
                "num_predict": max_tokens,
            }
        }
        ollama_base = str(settings.OLLAMA_BASE_URL).replace("/v1", "").rstrip("/")
        timeout_cfg = httpx.Timeout(connect=3.0, read=90.0, write=10.0, pool=5.0)
        async with httpx.AsyncClient(timeout=timeout_cfg) as client:
            resp = await client.post(f"{ollama_base}/api/generate", json=payload)

            if resp.status_code == 200:
                result = resp.json().get("response", "").strip()
                if result:
                    logger.info("Generated via Local Ollama (%s)", settings.OLLAMA_MODEL)
                    return result
    except Exception as e:
        logger.warning("Local Ollama failed: %s. Falling back to Groq Cloud API.", e)

    # Tier 2: Groq Cloud API with Multi-Model Fallback Ladder
    if settings.GROQ_API_KEY:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        headers = {
            "Authorization": f"Bearer {settings.GROQ_API_KEY}",
            "Content-Type": "application/json"
        }
        # Multi-model Groq fallback cascade
        models_to_try = [settings.GROQ_MODEL]
        for m in ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"]:
            if m not in models_to_try:
                models_to_try.append(m)

        for model in models_to_try:
            try:
                # qwen3.8-27b has a strict 1000 OTPM rate limit on Groq
                token_limit = min(max_tokens, 1000) if "qwen" in model.lower() else min(max_tokens, 4096)
                payload = {
                    "model": model,
                    "messages": messages,
                    "temperature": 0.2,
                    "max_tokens": token_limit,
                }
                async with httpx.AsyncClient(timeout=45.0) as client:
                    resp = await client.post("https://api.groq.com/openai/v1/chat/completions", json=payload, headers=headers)
                    if resp.status_code == 200:
                        data = resp.json()
                        choice = data["choices"][0]["message"]
                        result = choice.get("content", "").strip()
                        if not result and choice.get("reasoning"):
                            result = choice["reasoning"].strip()
                        if result:
                            logger.info("Generated via Groq API (%s)", model)
                            return result
                    elif resp.status_code == 429:
                        logger.warning("Groq rate limit on %s (429), trying next fallback model...", model)
                    else:
                        logger.warning("Groq model %s returned HTTP %s: %s", model, resp.status_code, resp.text[:120])
            except Exception as e:
                logger.warning("Groq model %s failed: %s", model, e)

    # Tier 3: Return empty string if both fail
    logger.error("All AI LLM tiers failed.")
    return ""


def clean_json_response(raw: str) -> dict[str, Any]:
    """Strip markdown code blocks and parse JSON (handles objects and arrays)."""
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    
    try:
        data = json.loads(raw)
        if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
            return data[0]
        return data
    except Exception:
        # Try finding json object bracket bounds
        start_obj = raw.find("{")
        end_obj = raw.rfind("}")
        if start_obj != -1 and end_obj != -1 and end_obj > start_obj:
            try:
                return json.loads(raw[start_obj:end_obj+1])
            except Exception:
                pass
        
        # Try finding json array bracket bounds
        start_arr = raw.find("[")
        end_arr = raw.rfind("]")
        if start_arr != -1 and end_arr != -1 and end_arr > start_arr:
            try:
                arr = json.loads(raw[start_arr:end_arr+1])
                if isinstance(arr, list) and len(arr) > 0 and isinstance(arr[0], dict):
                    return arr[0]
                return arr
            except Exception:
                pass
        # BUG #6 FIX: bare `raise` re-raises the LAST inner exception (from array parse),
        # not the original json.JSONDecodeError. Raise a descriptive ValueError instead.
        raise ValueError(f"Could not extract valid JSON from LLM response. Raw content: {raw[:300]!r}")


async def score_and_analyze_lead(lead: dict[str, Any]) -> dict[str, Any]:
    """Score and classify incoming lead (HOT, WARM, COLD)."""
    system_prompt = (
        "You are an expert B2B sales qualification AI. Evaluate this inbound lead.\n"
        "Score from 0 to 100 based on budget, urgency, requirement clarity, and business viability.\n"
        "Classify as:\n"
        "- 'HOT' (score >= 75): clear high budget, urgent need, concrete requirements\n"
        "- 'WARM' (score 45-74): interested, moderate budget or flexible timeline\n"
        "- 'COLD' (score < 45): vague, low budget, student project or spam\n\n"
        "Return ONLY a valid JSON object with keys:\n"
        "{\n"
        '  "score": 85,\n'
        '  "temperature": "HOT",\n'
        '  "reason": "Clear commercial intent with allocated budget and urgent timeline.",\n'
        '  "detected_needs": ["WhatsApp CRM", "Lead Qualification Bot"],\n'
        '  "recommended_deliverable": "Full-Stack Automation Suite",\n'
        '  "estimated_value": "₹35,000 - ₹50,000"\n'
        "}"
    )
    user_prompt = f"""
Lead Name: {lead.get('name')}
Company: {lead.get('company') or 'Not specified'}
Email: {lead.get('email') or 'Not specified'}
Phone: {lead.get('phone') or 'Not specified'}
Requirement: {lead.get('requirement')}
Budget: {lead.get('budget', 'Flexible')}
Timeline: {lead.get('timeline', 'Immediate')}
Source: {lead.get('source', 'website')}
"""
    raw = await call_llm(user_prompt, system_prompt, max_tokens=400)
    try:


        data = clean_json_response(raw)
        # Ensure score bounds
        score = int(data.get("score", 60))
        temp = "HOT" if score >= 75 else ("WARM" if score >= 45 else "COLD")
        data["score"] = score
        data["temperature"] = temp
        return data
    except Exception as e:
        logger.warning("Failed to parse AI scoring: %s. Using heuristic fallback.", e)
        # Heuristic fallback
        req = (lead.get("requirement") or "").lower()
        budget = (lead.get("budget") or "").lower()
        score = 50
        if any(w in req for w in ["urgent", "ready to pay", "build", "immediately", "crm", "automation", "scale"]):
            score += 25
        if any(w in budget for w in ["35", "50", "70", "lakh", "thousand", "k", "$"]):
            score += 20
        temp = "HOT" if score >= 75 else ("WARM" if score >= 45 else "COLD")
        return {
            "score": score,
            "temperature": temp,
            "reason": "Heuristic qualification based on commercial keywords and intent.",
            "detected_needs": ["Custom Business Automation"],
            "recommended_deliverable": "Full-Stack System",
            "estimated_value": lead.get("budget") or "₹25,000 - ₹50,000"
        }


async def generate_project_plan(lead: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any]:
    """Generate technical goals, architecture, and step-by-step implementation plan with strict domain fidelity."""
    system_prompt = (
        "You are an elite Chief Software Architect and Automation Engineer.\n"
        "Generate a complete, highly specialized, production-grade implementation plan for building a custom client deliverable.\n\n"
        "DOMAIN FIDELITY ARCHITECTURAL RULES:\n"
        "1. DOMAIN EXTRACTION: You MUST tailor the plan's title, deliverable, and core features directly to the client requirement:\n"
        "   - If requirement is a SCHOOL TIMETABLE / SCHEDULE:\n"
        "     * Title: '[Client/School] Timetable & Schedule Management System'\n"
        "     * Deliverable: 'Interactive Multi-Grade Timetable Grid & Resource Scheduler'\n"
        "     * Core Features: ['Dynamic Weekly Timetable Grid (Mon-Fri, Periods 1-7)', 'Teacher & Classroom Conflict Detection Engine', 'Grade/Class Switcher (Grade 10-A, 10-B, 9-A)', 'Live Slot Assignment Modal & CSV/Print Exporter']\n"
        "     * Tech Stack: ['FastAPI', 'Pydantic v2', 'Vanilla JS (ES6+)', 'Glassmorphism CSS', 'CSV/Print Engine']\n"
        "   - If requirement is a RESTAURANT POS / ORDERING:\n"
        "     * Title: '[Client] Smart Restaurant POS & Kitchen Display System'\n"
        "     * Deliverable: 'Real-Time Order & Table Billing Terminal'\n"
        "     * Core Features: ['Visual Table Floor Plan & Status', 'Touch Menu with Category Filter', 'Live Ticket Calculation with Taxes', 'Receipt Generation & Kitchen Dispatch']\n"
        "   - If requirement is a CRM / LEAD PIPELINE:\n"
        "     * Title: '[Client] Enterprise CRM & Sales Pipeline Engine'\n"
        "     * Deliverable: 'Interactive Kanban Deal Pipeline & Contact Hub'\n"
        "     * Core Features: ['Multi-Stage Deal Board (Lead, Qualified, Proposal, Won, Lost)', 'Deal Value Tracking & Metrics', 'Instant Inbound Alerts', 'CSV Data Exporter']\n"
        "   - If requirement is E-COMMERCE / STOREFRONT:\n"
        "     * Title: '[Client] Digital Commerce & Inventory Storefront'\n"
        "     * Deliverable: 'Full-Stack Product Catalog & Cart Checkout System'\n"
        "     * Core Features: ['Product Grid with Filter & Stock Status', 'Real-Time Shopping Cart Drawer', 'Mock Order Checkout Flow', 'Admin Inventory Manager']\n"
        "   - If requirement is a TASK / KANBAN BOARD:\n"
        "     * Title: '[Client] Sprint & Task Orchestration Platform'\n"
        "     * Deliverable: 'Kanban Workflow & Task Tracker'\n"
        "     * Core Features: ['Custom Swimlanes (To Do, In Progress, Review, Done)', 'Priority Badges & Due Date Tracking', 'Subtask Checklists', 'Search & Filtering']\n"
        "2. NEVER output generic 'CRM / Automation Suite' if the client requirement is a Timetable, POS, or specialized domain application!\n"
        "3. Output MUST be ONLY valid JSON matching this schema:\n"
        "{\n"
        '  "title": "Exact Domain System Title",\n'
        '  "target_deliverable": "Domain Deliverable Name",\n'
        '  "core_features": ["Feature 1", "Feature 2", "Feature 3", "Feature 4"],\n'
        '  "tech_stack": ["FastAPI", "Pydantic", "Vanilla JS", "Glassmorphism CSS"],\n'
        '  "architecture_overview": "Detailed description of system components and data flow.",\n'
        '  "implementation_steps": [\n'
        '     {"step": 1, "title": "Scaffold & Config", "description": "Dependencies, runtime configs, and constants", "files": ["requirements.txt", "config.py"]},\n'
        '     {"step": 2, "title": "Domain Backend & APIs", "description": "Data models, seed records, and REST endpoints", "files": ["main.py"]},\n'
        '     {"step": 3, "title": "Interactive Domain UI", "description": "Glassmorphism control center with live JS bindings", "files": ["index.html"]},\n'
        '     {"step": 4, "title": "Documentation & Runner", "description": "README and local launcher script", "files": ["README.md", "run_local.bat"]}\n'
        '  ]\n'
        "}"
    )
    user_prompt = f"""
Client Name: {lead.get('name')}
Company: {lead.get('company') or 'Independent'}
Client Requirement: {lead.get('requirement')}
Budget: {lead.get('budget', 'Flexible')}
Timeline: {lead.get('timeline', 'Immediate')}
AI Analysis Summary: {json.dumps(analysis)}
"""
    raw = await call_llm(user_prompt, system_prompt, max_tokens=1500)
    try:
        return clean_json_response(raw)
    except Exception as e:
        logger.warning("Failed to parse AI plan: %s. Using domain-intelligent plan fallback.", e)
        req = (lead.get("requirement") or "").lower()
        client_name = lead.get("name", "Client")
        comp = lead.get("company") or client_name

        if any(k in req for k in ["timetable", "schedule", "school", "period", "class"]):
            return {
                "title": f"{comp} School Timetable Management System",
                "target_deliverable": "Enterprise School Timetable & Resource Scheduling Suite",
                "core_features": [
                    "Dynamic Timetable Grid with Mon-Fri Periods 1-7",
                    "Teacher & Classroom Conflict Detection Engine",
                    "Class Switcher (Grade 10-A, Grade 10-B, Grade 9-A)",
                    "Real-Time Slot Assignment Modal & CSV Exporter"
                ],
                "tech_stack": ["FastAPI", "Pydantic v2", "Vanilla JS (ES6+)", "Glassmorphism CSS", "Uvicorn"],
                "architecture_overview": "A high-performance timetable scheduling system with conflict detection, period matrix views, and instant data persistence.",
                "implementation_steps": [
                    {"step": 1, "title": "Scaffold & Config", "description": "FastAPI dependencies and environment configuration", "files": ["requirements.txt"]},
                    {"step": 2, "title": "Domain Backend & APIs", "description": "Timetable slots, conflict detection logic, and CSV exporter", "files": ["main.py"]},
                    {"step": 3, "title": "Interactive Domain UI", "description": "Weekly schedule matrix grid with period cards and assignment modals", "files": ["index.html"]},
                    {"step": 4, "title": "Documentation & Runner", "description": "README guide and one-click local startup bat", "files": ["README.md", "run_local.bat"]}
                ]
            }
        elif any(k in req for k in ["pos", "restaurant", "cafe", "food", "table"]):
            return {
                "title": f"{comp} Restaurant POS & Order Terminal",
                "target_deliverable": "Full-Stack Point of Sale & Table Billing System",
                "core_features": [
                    "Interactive Table Floor Plan",
                    "Touch Menu Catalog with Category Tabs",
                    "Real-Time Order Ticket & Bill Calculation",
                    "Receipt Printing & Daily Sales Summary"
                ],
                "tech_stack": ["FastAPI", "Pydantic", "Vanilla JS", "Glassmorphism CSS"],
                "architecture_overview": "Zero-latency point of sale terminal for table management, order tracking, and instant invoicing.",
                "implementation_steps": [
                    {"step": 1, "title": "Setup", "description": "Dependencies", "files": ["requirements.txt"]},
                    {"step": 2, "title": "POS Backend", "description": "Tables, menu items, and order billing API", "files": ["main.py"]},
                    {"step": 3, "title": "Terminal Frontend", "description": "Floor plan, touch menu, and active receipt ticket UI", "files": ["index.html"]},
                    {"step": 4, "title": "Runner", "description": "Run script and documentation", "files": ["README.md", "run_local.bat"]}
                ]
            }
        elif any(k in req for k in ["store", "ecommerce", "shop", "product", "cart"]):
            return {
                "title": f"{comp} E-Commerce Storefront & Inventory Hub",
                "target_deliverable": "Full-Stack Online Store & Inventory Manager",
                "core_features": [
                    "Product Showcase with Live Stock Badges",
                    "Slide-out Shopping Cart with Dynamic Subtotal",
                    "Order Checkout Simulation & Confirmation",
                    "Product Search, Filters, and CSV Inventory Export"
                ],
                "tech_stack": ["FastAPI", "Pydantic", "Vanilla JS", "Glassmorphism CSS"],
                "architecture_overview": "Responsive digital storefront backed by FastAPI CRUD endpoints and modern reactive UI.",
                "implementation_steps": [
                    {"step": 1, "title": "Setup", "description": "Dependencies", "files": ["requirements.txt"]},
                    {"step": 2, "title": "Store API", "description": "Products, cart calculations, and orders API", "files": ["main.py"]},
                    {"step": 3, "title": "Storefront UI", "description": "Product grid, shopping cart drawer, and checkout modal", "files": ["index.html"]},
                    {"step": 4, "title": "Runner", "description": "Run script and documentation", "files": ["README.md", "run_local.bat"]}
                ]
            }
        else:
            return {
                "title": f"{comp} Enterprise Operations & Workflow Suite",
                "target_deliverable": "Full-Stack Custom Business Operations Platform",
                "core_features": [
                    "Centralized Operational Records & Pipeline",
                    "Status Tracking & Priority Classification",
                    "Real-Time Search, Filtering, and Metrics",
                    "CSV Data Export & Audit Logging"
                ],
                "tech_stack": ["FastAPI", "Pydantic", "Vanilla JS", "Glassmorphism CSS"],
                "architecture_overview": "Modular business suite with high-throughput REST APIs and dark glassmorphic control center.",
                "implementation_steps": [
                    {"step": 1, "title": "Setup", "description": "Dependencies", "files": ["requirements.txt"]},
                    {"step": 2, "title": "Backend Services", "description": "REST APIs, database models, and exporter", "files": ["main.py"]},
                    {"step": 3, "title": "Dashboard UI", "description": "Interactive data table with modals and analytics cards", "files": ["index.html"]},
                    {"step": 4, "title": "Runner", "description": "Run script and documentation", "files": ["README.md", "run_local.bat"]}
                ]
            }


async def generate_file_content(file_name: str, file_desc: str, project_plan: dict[str, Any], lead: dict[str, Any]) -> str:
    """Generate complete, non-placeholder, enterprise-grade functional code for a file."""
    system_prompt = (
        "You are an elite Lead Enterprise Software Architect & Senior Full-Stack Engineer.\n"
        "You build top-notch, fully scalable, production-grade commercial software products for enterprise clients.\n\n"
        "STRICT PRODUCTION MANDATES:\n"
        "1. Write FULLY COMPLETE, FUNCTIONAL, HIGH-QUALITY CODE. NEVER generate placeholder text, dummy landing cards, or 'Test Connection' buttons.\n"
        "2. DOMAIN FIDELITY RULE (CRITICAL):\n"
        "   - You MUST tailor your data structures, APIs, and UI directly to the specific client requirement:\n"
        "     * If requirement is a SCHOOL TIMETABLE / SCHEDULE:\n"
        "       - Data Model: TimetableSlot (id, day [Monday..Friday], period [1..7], time_range, class_group [Grade 10-A, Grade 10-B], subject, teacher, room, color_type).\n"
        "       - Backend Routes: GET /api/timetable?class_group=..., POST /api/timetable, DELETE /api/timetable/{id}, GET /api/conflicts, GET /api/export/csv, GET /api/health.\n"
        "       - Frontend UI: A true Weekly Schedule Grid! (Columns: Mon-Fri, Rows: Periods 1-7 with times, lunch break bar, colored subject chips with teacher and room, class group dropdown filter, 'Add Period' modal, conflict alerts, and CSV export).\n"
        "       - NEVER use a generic 'Item' or 'Task' table with 'assignee' when the client asked for a School Timetable!\n"
        "     * If requirement is a TO-DO LIST / TASK MANAGER:\n"
        "       - Data Model: Task (id, title, category, priority, due_date, status, subtasks).\n"
        "       - Frontend UI: Kanban or Task board with status columns and checkboxes.\n"
        "     * If requirement is a CRM / LEAD SYSTEM:\n"
        "       - Data Model: Lead/Deal (id, contact_name, company, value, stage, source, notes).\n"
        "       - Frontend UI: Deal Pipeline Kanban with stages (New, Qualified, Proposal, Won, Lost).\n"
        "     * If requirement is E-COMMERCE / STORE:\n"
        "       - Data Model: Product (id, name, price, category, stock, image_url, description).\n"
        "       - Frontend UI: Product catalog grid with category filters, shopping cart drawer, and checkout.\n"
        "     * If requirement is RESTAURANT POS:\n"
        "       - Data Model: Table, MenuItem, Order, OrderItem, Bill.\n"
        "       - Frontend UI: Floor plan table selector, item menu grid, live order ticket with tax calculation.\n"
        "3. If generating HTML/Frontend (index.html):\n"
        "   - Provide complete, modern glassmorphism styling with dark theme, Outfit font, responsive flex/grid layouts.\n"
        "   - Include working, clean ES6 JavaScript that connects to the exact backend routes defined in main.py.\n"
        "   - Ensure all interactive modals, forms, filters, and export buttons function out-of-the-box.\n"
        "4. If generating Backend (Python / FastAPI / main.py):\n"
        "   - Implement complete FastAPI application with CORS middleware, Pydantic v2 schemas, REST endpoints, and pre-seeded realistic domain records.\n"
        "   - Ensure `uvicorn.run('main:app', host='0.0.0.0', port=8080, reload=False)` runs smoothly and serves `index.html` at `/`.\n"
        "5. Output ONLY clean, executable raw code for the file without markdown code fences or explanatory conversational text."
    )
    user_prompt = f"""
Project Title: {project_plan.get('title')}
Target Deliverable: {project_plan.get('target_deliverable')}
Client: {lead.get('name')} ({lead.get('company') or 'Enterprise Client'})
Client Requirement: {lead.get('requirement')}
Core Features: {json.dumps(project_plan.get('core_features', []))}
Tech Stack: {json.dumps(project_plan.get('tech_stack', []))}

FILE TO GENERATE: {file_name}
PURPOSE: {file_desc}

Generate the complete, production-grade, domain-accurate code for {file_name} now:
"""
    raw = await call_llm(user_prompt, system_prompt, max_tokens=4096)
    raw = raw.strip()
    # Strip any accidental markdown fences if LLM included them
    if raw.startswith("```"):
        lines = raw.splitlines()
        # Remove opening fence e.g. ```python or ```html
        if lines[0].startswith("```"):
            lines = lines[1:]
        # Remove closing fence
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()

    # Pre-flight check: verify Python files compile cleanly
    if file_name.endswith(".py") and raw:
        import py_compile
        import tempfile
        from pathlib import Path
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as tf:
                tf.write(raw)
                tmp_path = tf.name
            py_compile.compile(tmp_path, doraise=True)
        except Exception as e:
            logger.warning("Generated Python code for %s failed syntax compilation (%s). Discarding for fallback.", file_name, e)
            raw = ""
        finally:
            if tmp_path:
                Path(tmp_path).unlink(missing_ok=True)

    # Pre-flight check: verify HTML completeness (no truncated files or unclosed scripts)
    elif file_name.endswith(".html") and raw:
        raw_lower = raw.lower().strip()
        if not raw_lower.endswith("</html>"):
            logger.warning("Generated HTML for %s was truncated (missing </html>). Discarding for fallback.", file_name)
            raw = ""
        elif raw_lower.count("<script") > raw_lower.count("</script>"):
            logger.warning("Generated HTML for %s has unclosed <script> tags. Discarding for fallback.", file_name)
            raw = ""

    return raw
