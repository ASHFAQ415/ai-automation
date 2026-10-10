# 🧠 Hermes Agent — Brain.md
> **Ultimate Free-Tier Optimization Guide & Architecture Reference**
> Owner: MOHD ASHFAQ | Updated: 2026-09-14

---

## What Is Hermes?

`NousResearch/hermes-agent` is a multi-surface personal AI agent that runs the same core
agent loop across CLI, TUI, Desktop (Electron), Messaging Gateways (Telegram/Discord/Slack/20+ platforms),
Cron jobs, and VS Code/Zed via ACP.

The agent is **extended through plugins and skills**, never by growing the core.

---

## Architecture Map

```
run_agent.py                 <- AIAgent facade; turn loop in agent/turn_*.py
  model_tools.py             <- Tool orchestration, discover_builtin_tools()
  toolsets.py                <- TOOLSETS dict, _HERMES_CORE_TOOLS list (40+ tools)
  cli.py                     <- HermesCLI (REPL + slash dispatch)
  hermes_state.py            <- SessionDB facade (21 siblings)
  hermes_constants.py        <- get_hermes_home() -- profile-aware paths

agent/
  turn_*.py                  <- Per-phase loop logic
  prompt_builder.py          <- System prompt assembly (~85KB at full toolset)
  model_metadata.py          <- Context lengths, token estimation
  agent_init.py              <- Session/provider build
  context_compressor.py      <- Compression + proactive pruning
  conversation_loop.py       <- Main loop

tools/
  registry.py                <- Tool registry (check_fn gates availability)
  environments/              <- Terminal backends: local/docker/ssh/modal/daytona

hermes_cli/
  config_defaults.py         <- DEFAULT_CONFIG (2884 lines; master schema)
  config.py                  <- Config loading/merging/validation
  models.py                  <- Provider/model catalogs
  models_catalog_static.py   <- CANONICAL_PROVIDERS, OPENROUTER_MODELS, etc.
  nous_subscription.py       <- Feature gating (web/image/tts/stt/browser)

gateway/platforms/           <- One adapter per messaging platform
plugins/                     <- Memory, image_gen, platforms, kanban, model providers
skills/                      <- Built-in skills (loaded on demand)
optional-skills/             <- Shipped but not active by default
```

---

## Provider System

### Provider Resolution Chain

```
config.yaml model.provider:
  "nous"          -> Nous Portal (requires login; free + paid tiers)
  "opencode-free" -> OpenCode.ai keyless free tier  [NO KEY NEEDED]
  "openrouter"    -> OpenRouter (requires key; :free models cost $0)
  "ollama"        -> Local Ollama (http://localhost:11434)
  "groq"          -> Groq.com (requires GROQ_API_KEY; free: 8K TPM limit)
  "custom"        -> Any OpenAI-compat base_url  [what we use for Groq]
```

### Keyless / Zero-Cost Providers

| Provider | Models | Limit | Key? |
|----------|--------|-------|------|
| `opencode-free` | deepseek-v4-flash-free, mimo-v2.5-free, nemotron-3-ultra-free | Rate-limited | No |
| Groq free (custom) | llama-3.3-70b, mixtral-8x7b, gemma2-9b | 8K TPM / 100K TPD | Yes (yours) |
| OpenRouter :free | 50+ models | Rate-limited | Yes (free account) |
| Local Ollama | Any pulled model | PC RAM limit | No |

---

## The Critical Groq 413 Problem & Solution

### Root Cause
- Groq free tier: **8,000 Input Tokens Per Minute (ITPM)**
- Hermes full system prompt + all tool schemas: **~10,000-14,000 tokens**
- Result: Every request exceeds ITPM limit -> HTTP 413 "Payload Too Large"

### Fixes

1. Disable unused toolsets to shrink schemas
2. Use opencode-free as fallback (no TPM limit)
3. Set compression.threshold: 0.40 (compress early)
4. Enable proactive_prune_tokens: 32000
5. Set auxiliary.free_only: true (no paid aux calls)

```yaml
# Disable toolsets you don't need:
agent:
  disabled_toolsets:
    - "kanban"         # ~1,200 tokens saved
    - "ha"             # Home Assistant -- skip if not using HA
    - "computer_use"   # Vision-based desktop control -- skip
    - "image_generate" # Skip if no image gen needed
```

---

## Key Configuration Files

| File | Purpose |
|------|---------|
| `~/.hermes/config.yaml` | Main config -- all behavior settings |
| `~/.hermes/.env` | Secrets -- API keys ONLY |
| `~/.hermes/logs/agent.log` | INFO+ runtime log |
| `~/.hermes/logs/errors.log` | WARNING+ error log |
| `d:\New folder (3)\hermes\` | Source code root |

---

## Current Optimized Free-Tier Config

Location: `C:\Users\MOHD ASHFAQ\AppData\Local\hermes\config.yaml`

```yaml
model:
  provider: custom
  base_url: https://api.groq.com/openai/v1
  api_key: <GROQ_API_KEY from .env>
  default: llama-3.3-70b-versatile

agent:
  max_turns: null          # Unlimited turns -- no mid-task cutoff

fallback_providers:
  - provider: custom
    base_url: https://api.groq.com/openai/v1
    model: gemma2-9b-it    # Smaller = fewer tokens = survives 8K TPM
  - provider: custom
    base_url: http://localhost:11434/v1
    model: llama3.2        # Local Ollama (if running)

web:
  search_backend: duckduckgo
  backend: duckduckgo
  keyless_fallback: true

stt:
  provider: groq
  groq:
    model: whisper-large-v3-turbo   # Free with Groq key

tts:
  provider: edge-tts                # Microsoft Edge TTS -- completely free

browser:
  cloud_provider: local             # Local Playwright (installed)

auxiliary:
  free_only: true
  openrouter_model: "nvidia/nemotron-3-ultra-550b-a55b:free"

compression:
  enabled: true
  threshold: 0.40
  proactive_prune_tokens: 32000
  tail_mode: lean
```

---

## Completely Free Tool Stack

| Capability | Tool | Cost | Key? |
|-----------|------|------|------|
| LLM inference | Groq llama-3.3-70b | Free | Yes (yours) |
| LLM backup | opencode-free | Free | No |
| Local LLM | Ollama | Free | No |
| Web search | DuckDuckGo (ddgs) | Free | No |
| Web extract | Keyless ring (exa/parallel/firecrawl) | Free | No |
| Text-to-speech | edge-tts (Microsoft) | Free | No |
| Speech-to-text | Groq Whisper | Free | Yes (yours) |
| Browser | Local Playwright Chromium | Free | No |
| Memory | Local SQLite | Free | No |
| Skills | Local YAML files | Free | No |
| Cron jobs | Local scheduler | Free | No |
| Terminal | Local shell | Free | No |
| File ops | Local filesystem | Free | No |

---

## Subscription Gates & Bypasses

`hermes_cli/nous_subscription.py` gates these features behind Nous Portal OR direct creds:

| Feature | Default gate | Free bypass |
|---------|-------------|-------------|
| Web search/extract | Firecrawl managed | web.backend: duckduckgo [done] |
| TTS | OpenAI Audio managed | tts.provider: edge-tts [done] |
| STT | OpenAI Audio managed | stt.provider: groq [done] |
| Browser | Browser Use managed | browser.cloud_provider: local [done] |
| Image gen | FAL managed | image_gen.provider: none |
| Video gen | FAL managed | Not needed |
| Modal execution | Modal subscription | Use local terminal backend [done] |

ALL gates are bypassed by our current config.

---

## Useful Commands

```bash
# Activate environment
cd "d:\New folder (3)\hermes"
.venv\Scripts\activate

# Start chat
hermes

# One-shot query
hermes -q "your question here"

# Check system health
hermes doctor

# View/set model
hermes model
hermes model set groq:llama-3.3-70b-versatile

# View logs
hermes logs
hermes logs --follow

# Web search settings
hermes tools

# Memory commands
hermes memory list
hermes memory search "keyword"

# Skills
hermes skills list
hermes skills install <skill-name>

# Sessions
hermes sessions
hermes -c   # continue last session

# Manual context compression (inside chat):
# /compress

# Switch to keyless opencode-free if Groq 413s:
# hermes model set opencode-free:deepseek-v4-flash-free
```

---

## Free Model Recommendations

### For Long Tasks (Big Context)
1. Groq `llama-3.3-70b-versatile` -- 128K ctx, fast, smart. [WARNING: 8K TPM]
2. opencode-free `nemotron-3-ultra-free` -- no key, large, slower
3. opencode-free `deepseek-v4-flash-free` -- no key, fast, good reasoning
4. Local Ollama `qwen2.5:7b` or `llama3.2` -- unlimited, offline

### For Quick Tasks (Low Token Count)
1. Groq `gemma2-9b-it` -- tiny, fast, fits in 8K TPM
2. Groq `mixtral-8x7b-32768` -- 32K context, free
3. opencode-free `mimo-v2.5-free` -- no key, small but capable

### For Auxiliary Tasks (Compression, Titles, Memory rewrites)
- Set `auxiliary.free_only: true`
- Set `auxiliary.openrouter_model: "nvidia/nemotron-3-ultra-550b-a55b:free"`
- OR point auxiliary at Groq gemma2-9b-it (very cheap per-call)

---

## Architecture Invariants (NEVER Break)

1. **Per-conversation prompt caching is sacred** -- don't mutate system prompt mid-conversation
2. **Core is a narrow waist** -- new capabilities go in plugins/skills, NOT core
3. **Never hardcode `~/.hermes`** -- use `get_hermes_home()` from hermes_constants
4. **Strict role alternation** -- never two same-role messages in a row
5. **Tests via `scripts/run_tests.sh`** -- never bare `pytest`
6. **Config in config.yaml, secrets in .env** -- never swapped

---

## Plugin System

Plugins live in `plugins/` (built-in) or `~/.hermes/plugins/` (user):

```
plugins/
  memory/            # Memory backends (openviking, honcho)
  context_engine/    # Alternative context engines
  platforms/         # Discord, Slack, Feishu, Matrix, etc.
  kanban/            # Kanban board integration
  image_gen/         # Image generation backends
```

---

## Skills System

Skills are YAML+Markdown files teaching domain knowledge. They are injected on demand -- not
every turn -- so they don't waste tokens unless the agent needs them.

```
skills/               <- Built-in skills
optional-skills/      <- Available but not active by default
~/.hermes/skills/     <- Your custom skills
```

---

## Memory System

Built-in persistent memory stored locally in SQLite:
- Auto: agent saves facts after conversations
- Manual: `/memory add "fact"`, `/memory list`, `/memory search "query"`
- Limit: memory.memory_char_limit: 2200 chars (~800 tokens)
- Location: `~/.hermes/` (profile-aware)

---

## Context Compression

When history fills the context window (trigger: 40% full by our config):

1. Proactive pruning -- trim old bulky tool results (no LLM)
2. LLM summarization -- compress middle into rolling summary
3. Lean tail mode -- keep only recent exchanges verbatim

Key config:
```yaml
compression:
  threshold: 0.40           # Trigger at 40% (early = fewer 413 errors)
  proactive_prune_tokens: 32000
  tail_mode: lean
  protect_last_n: 20
```

---

## Environment Setup (Fresh Install)

```bash
# 1. Clone
git clone https://github.com/NousResearch/hermes-agent.git
cd hermes-agent

# 2. Create venv and install
python -m venv .venv
.venv\Scripts\activate
pip install uv
uv pip install -e ".[all]"

# 3. Install free tool deps
pip install duckduckgo-search
pip install edge-tts
pip install playwright
playwright install chromium

# 4. Configure (edit these files)
# C:\Users\<YOU>\AppData\Local\hermes\config.yaml
# C:\Users\<YOU>\AppData\Local\hermes\.env

# 5. Verify
hermes doctor
hermes -q "hello"
```

---

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| HTTP 413 on Groq | ITPM limit exceeded | Disable unused toolsets; switch to opencode-free |
| ModuleNotFoundError | venv not active | Run `.venv\Scripts\activate` |
| Encoding errors | Windows cp1252 | Run `chcp 65001` or `set PYTHONIOENCODING=utf-8` |
| Agent stops mid-task | max_turns set | Set `agent.max_turns: null` |
| Slow first response | MCP discovery | Set `mcp_discovery_timeout: 0.5` |
| Browser not working | Playwright missing | `playwright install chromium` |
| TTS not working | edge-tts missing | `pip install edge-tts` |
| Model refuses tool calls | Wrong model | Use llama-3.3-70b or qwen2.5 series |

---

## Key Source Locations for Customization

| What | Where |
|------|-------|
| New tool | `tools/<name>.py` (auto-discovered by registry.py) |
| New skill | `skills/<category>/<name>/SKILL.md` |
| New platform | `gateway/platforms/<name>.py` |
| Provider auth | `hermes_cli/auth.py` |
| Tool availability gate | `tools/registry.py` (check_fn) |
| System prompt | `agent/prompt_builder.py` + `skills/SOUL.md` |
| Memory backend | `plugins/memory/` |

---

*This file is a living reference. Update it when you change configs or discover new patterns.*
