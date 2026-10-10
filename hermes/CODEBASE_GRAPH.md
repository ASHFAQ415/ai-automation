# 🗺️ Hermes Agent — Codebase Graph
> **Agent Navigation Map**: Read this file to instantly know which file to edit for any task.
> Every node links to its source. Every edge shows the dependency direction.

---

## 1. Top-Level Turn Loop (How Every Message is Processed)

```mermaid
flowchart TD
    USER([User Input])
    CLI[cli.py\nHermesCLI REPL]
    GATEWAY[gateway/run.py\nMessaging Gateway]
    RUN[run_agent.py\nAIAgent.run_turn]
    LOOP[agent/conversation_loop.py\nconversation_loop]
    PROMPT[agent/prompt_builder.py\nbuild_system_prompt]
    API[agent/chat_completion_helpers.py\nchat_completion]
    TOOLS[model_tools.py\nhandle_function_call]
    COMPRESS[agent/context_compressor.py\nContextCompressor]
    STATE[hermes_state.py\nSessionDB]

    USER -->|typed message| CLI
    USER -->|Telegram/Discord/Slack| GATEWAY
    CLI --> RUN
    GATEWAY --> RUN
    RUN --> LOOP
    LOOP --> PROMPT
    LOOP --> COMPRESS
    PROMPT -->|system prompt| API
    API -->|LLM response| TOOLS
    TOOLS -->|tool results| LOOP
    LOOP --> STATE
    STATE -->|persisted session| LOOP
```

---

## 2. System Prompt Assembly

```mermaid
flowchart LR
    PB[agent/prompt_builder.py]
    SOUL["~/.hermes/SOUL.md\nPersona"]
    AGENTS["AGENTS.md\nProject rules"]
    MEMORY["~/.hermes/memories/\nMEMORY.md + USER.md"]
    SKILLS[skills/\nOn-demand skill blocks]
    ENV[agent/coding_context.py\nGit/workspace snapshot]
    SCHEMA[toolsets.py\n_HERMES_CORE_TOOLS schemas]

    SOUL --> PB
    AGENTS --> PB
    MEMORY --> PB
    SKILLS --> PB
    ENV --> PB
    SCHEMA --> PB
    PB -->|~85KB system prompt| LLM([LLM API])
```

**To change agent persona** → edit `~/.hermes/SOUL.md`
**To change tool schemas** → edit `toolsets.py` or tool file in `tools/`
**To change workspace context** → edit `agent/coding_context.py`

---

## 3. Provider Resolution Chain

```mermaid
flowchart TD
    CFG["config.yaml\nmodel.provider"]
    MODELS[hermes_cli/models.py\nresolve_model]
    CATALOG[hermes_cli/models_catalog_static.py\nCANONICAL_PROVIDERS]
    META[agent/model_metadata.py\nget_context_length]

    CFG -->|provider name| MODELS
    MODELS -->|look up| CATALOG
    MODELS -->|context window| META

    GROQ["custom provider\nGroq API\ngsk_077L..."]
    OC_FREE["opencode-free\nNO KEY NEEDED\ndeepseek-v4-flash-free"]
    OLLAMA["local Ollama\nlocalhost:11434"]
    NOUS["nous provider\nPortal login\nFree + paid"]

    MODELS --> GROQ
    MODELS -.->|fallback 1| GROQ
    MODELS -.->|fallback 2| GROQ
    MODELS -.->|fallback 3| OLLAMA
    MODELS -.->|keyless| OC_FREE
    MODELS -.->|portal| NOUS

    style OC_FREE fill:#2d5a27,color:#fff
    style OLLAMA fill:#2d5a27,color:#fff
    style GROQ fill:#1a3a5c,color:#fff
```

**To change primary model** → edit `config.yaml` → `model.default`
**To add a provider** → edit `hermes_cli/models_catalog_static.py`
**To fix model metadata/context** → edit `agent/model_metadata.py`

---

## 4. Tool Registry & Discovery

```mermaid
flowchart TD
    DISC[model_tools.py\ndiscover_builtin_tools]
    REG[tools/registry.py\n_TOOL_REGISTRY]
    GATE{check_fn\ngate}
    SCHEMA[tool schemas\nJSON sent to LLM]

    DISC -->|scan tools/ dir| REG
    REG -->|check_fn| GATE
    GATE -->|available| SCHEMA
    GATE -->|not available| DROP([hidden from LLM])

    WEB[tools/web_tools.py\nweb_search + web_extract]
    FILE[tools/file_tools.py\nread_file + write_file + patch]
    TERM[tools/terminal_tool.py\nterminal + process_manage]
    BROWSER[tools/browser_tool.py\nbrowser_navigate/click/type...]
    MEMORY[tools/memory_tool.py\nmemory]
    TTS[tools/tts_tool.py\ntext_to_speech]
    STT[tools/transcription_tools.py\nSTT + Groq whisper]
    SKILL[tools/skills_tool.py\nskills_list + skill_view]
    DELEGATE[tools/delegate_tool.py\ndelegate_task]
    MCP[tools/mcp_tool.py\nMCP server tools]
    CRON[tools/cronjob_tools.py\ncronjob_manage]
    CODE[tools/code_execution_tool.py\nexecute_code]
    VISION[tools/vision_tools.py\nvision_analyze + image_generate]
    TODO[tools/todo_tool.py\ntodo_list]

    REG --> WEB
    REG --> FILE
    REG --> TERM
    REG --> BROWSER
    REG --> MEMORY
    REG --> TTS
    REG --> STT
    REG --> SKILL
    REG --> DELEGATE
    REG --> MCP
    REG --> CRON
    REG --> CODE
    REG --> VISION
    REG --> TODO
```

---

## 5. Tool → File Mapping (Edit Guide)

```mermaid
flowchart LR
    subgraph WEB_TOOLS["Web & Search"]
        WT[web_search\nweb_extract]
        WF[tools/web_tools.py\ntools/web_tools_extract.py]
        WBCK[web backend\nconfig.yaml web.backend]
        WT --> WF --> WBCK
    end

    subgraph FILE_TOOLS["File Operations"]
        FT[read_file\nwrite_file\npatch\nsearch_files]
        FF[tools/file_tools.py\ntools/file_operations.py\ntools/patch_parser.py]
        FT --> FF
    end

    subgraph BROWSER_TOOLS["Browser Automation"]
        BT[browser_navigate\nbrowser_click\nbrowser_type\nbrowser_snapshot]
        BF[tools/browser_tool.py\ntools/browser_tool_session.py\ntools/browser_tool_cdp.py]
        BT --> BF
    end

    subgraph MEMORY_TOOLS["Memory & Session"]
        MT[memory\nsession_search\ntodo_list]
        MF[tools/memory_tool.py\ntools/session_search_tool.py\ntools/todo_tool.py]
        MT --> MF
    end

    subgraph VOICE_TOOLS["Voice / Audio"]
        VT[text_to_speech\nSTT]
        VF[tools/tts_tool.py\ntools/tts_tool_local.py\ntools/transcription_tools.py]
        VT --> VF
    end

    subgraph TERMINAL_TOOLS["Terminal & Code"]
        TT[terminal\nexecute_code\nprocess_manage]
        TF[tools/terminal_tool.py\ntools/code_execution_tool.py\ntools/process_registry.py]
        TT --> TF
    end

    subgraph AGENT_TOOLS["Agent & Skills"]
        AT[delegate_task\nskills_list\nskill_view\ncronjob_manage]
        AF[tools/delegate_tool.py\ntools/skills_tool.py\ntools/cronjob_tools.py]
        AT --> AF
    end
```

---

## 6. Configuration Loading Chain

```mermaid
flowchart TD
    RAW["~/.hermes/config.yaml\nUser config"]
    DEF[hermes_cli/config_defaults.py\nDEFAULT_CONFIG]
    LOAD[hermes_cli/config.py\nload_config + merge]
    ENV_LOAD[hermes_cli/env_loader.py\n.env secrets]
    DOTENV["~/.hermes/.env\nGROQ_API_KEY etc"]
    PROFILE[hermes_constants.py\nget_hermes_home]

    PROFILE --> RAW
    DEF --> LOAD
    RAW --> LOAD
    DOTENV --> ENV_LOAD
    ENV_LOAD --> LOAD
    LOAD -->|merged config dict| APP([All modules])
```

**To change a default setting** → edit `hermes_cli/config_defaults.py`
**To change your live settings** → edit `~/.hermes/config.yaml`
**To add a secret key** → edit `~/.hermes/.env`

---

## 7. Subscription Gate Bypass Map

```mermaid
flowchart TD
    NOUS_SUB[hermes_cli/nous_subscription.py\nget_nous_subscription_features]
    
    subgraph WEB_GATE["Web Search Gate"]
        WG{Has Firecrawl\nmanaged?}
        WG -->|No| DDG[DuckDuckGo\nweb.backend: duckduckgo]
        WG -->|No key| KEYLESS[Keyless ring\nexa/parallel/firecrawl]
    end

    subgraph TTS_GATE["TTS Gate"]
        TG{Has OpenAI\naudio?}
        TG -->|No| EDGE[edge-tts\nMicrosoft free]
    end

    subgraph STT_GATE["STT Gate"]
        SG{Has OpenAI\nwhisper?}
        SG -->|Groq key| GROQ_STT[Groq Whisper\nwhisper-large-v3-turbo]
    end

    subgraph BROWSER_GATE["Browser Gate"]
        BG{Has Browser\nUse cloud?}
        BG -->|No| LOCAL_PW[Local Playwright\nChromium]
    end

    NOUS_SUB --> WG
    NOUS_SUB --> TG
    NOUS_SUB --> SG
    NOUS_SUB --> BG

    style DDG fill:#2d5a27,color:#fff
    style EDGE fill:#2d5a27,color:#fff
    style GROQ_STT fill:#2d5a27,color:#fff
    style LOCAL_PW fill:#2d5a27,color:#fff
```

---

## 8. Memory & Compression Flow

```mermaid
flowchart TD
    HIST[Session history\ngrowing per turn]
    THRESHOLD{"40% of\ncontext full?"}
    PRUNE[Proactive prune\nDrop old tool results\nNO LLM needed]
    COMPRESS_LLM[LLM Summarize\nagent/context_compressor.py\nGroq gemma2-9b-it]
    LEAN[Lean tail\nKeep last 15 msgs verbatim]
    PERSIST[hermes_state.py\nSQLite sessions DB]

    HIST --> THRESHOLD
    THRESHOLD -->|32K tokens| PRUNE
    THRESHOLD -->|40% full| COMPRESS_LLM
    COMPRESS_LLM --> LEAN
    LEAN --> PERSIST
    PRUNE --> PERSIST

    MEM[tools/memory_tool.py\nPersistent facts]
    MEMFILE["~/.hermes/memories/\nMEMORY.md + USER.md"]
    MEM --> MEMFILE
    MEMFILE -->|injected in system prompt| HIST
```

---

## 9. Skills System

```mermaid
flowchart LR
    AGENT([Agent turn])
    SKILLS_TOOL[tools/skills_tool.py\nskills_list, skill_view]
    HUB[tools/skills_hub.py\nSkills Hub discovery]
    LOCAL["skills/\nBuiltin skills"]
    OPTIONAL["optional-skills/\nShipped, not active"]
    USER_SKILLS["~/.hermes/skills/\nYour custom skills"]
    PROMPT_INJECT[agent/prompt_builder.py\nInject on demand]

    AGENT --> SKILLS_TOOL
    SKILLS_TOOL --> HUB
    HUB --> LOCAL
    HUB --> OPTIONAL
    HUB --> USER_SKILLS
    LOCAL --> PROMPT_INJECT
    USER_SKILLS --> PROMPT_INJECT
    PROMPT_INJECT --> AGENT
```

**To create a skill** → create `skills/<category>/<name>/SKILL.md`
**To install a skill from hub** → `hermes skills install <name>`

---

## 10. Gateway / Messaging Platform

```mermaid
flowchart TD
    GW[gateway/run.py\nGateway facade]
    STARTUP[gateway/run_startup.py]
    INBOUND[gateway/run_inbound.py]
    TURN[gateway/run_turn.py\nPer-message turn]
    NOTIFY[gateway/run_notifications.py]
    SHUTDOWN[gateway/run_shutdown.py]

    GW --> STARTUP
    STARTUP --> INBOUND
    INBOUND --> TURN
    TURN --> NOTIFY
    NOTIFY --> SHUTDOWN

    TG[gateway/platforms/telegram.py]
    DC[plugins/platforms/discord/]
    SL[plugins/platforms/slack/]
    API_SRV[gateway/platforms/api_server.py\nHTTP API]

    TURN --> TG
    TURN --> DC
    TURN --> SL
    TURN --> API_SRV
```

---

## 11. Quick Edit Guide — What File for What Task

| Task | File(s) to edit |
|------|----------------|
| Change AI model | `~/.hermes/config.yaml` → `model.default` |
| Add API key | `~/.hermes/.env` |
| Change agent persona/behavior | `~/.hermes/SOUL.md` |
| Fix web search | `tools/web_tools.py` + `config.yaml web.backend` |
| Fix browser | `tools/browser_tool.py` + `tools/browser_tool_session.py` |
| Fix TTS | `tools/tts_tool.py` + `tools/tts_tool_local.py` (edge-tts) |
| Fix STT | `tools/transcription_tools.py` + `tools/transcription_cloud.py` |
| Fix file read/write | `tools/file_tools.py` + `tools/file_operations.py` |
| Fix terminal | `tools/terminal_tool.py` |
| Add a new tool | New file in `tools/`, register in `tools/registry.py` |
| Change compression | `config.yaml compression.*` + `agent/context_compressor.py` |
| Change system prompt | `agent/prompt_builder.py` |
| Fix provider/model | `hermes_cli/models.py` + `hermes_cli/models_catalog_static.py` |
| Add a platform | `gateway/platforms/<name>.py` |
| Change default config | `hermes_cli/config_defaults.py` |
| Fix cron jobs | `cron/scheduler.py` + `cron/jobs.py` |
| Fix skills | `tools/skills_tool.py` + `tools/skills_hub.py` |
| Fix memory | `tools/memory_tool.py` + `tools/memory_tool_store.py` |
| Fix subagent/delegate | `tools/delegate_tool.py` + `tools/async_delegation.py` |
| Fix MCP servers | `tools/mcp_tool.py` + `tools/mcp_tool_lifecycle.py` |
| Change session DB | `hermes_state.py` + siblings `hermes_state_*.py` |

---

## 12. Free-Tier Component Map (Our Setup)

```mermaid
flowchart LR
    subgraph FREE ["Zero-Cost Stack (Our Config)"]
        GROQ_LLM["Groq API\nllama-3.3-70b-versatile\nFree with key"]
        OC["opencode-free\ndeepseek-v4-flash-free\nNo key needed"]
        OLLAMA_L["Local Ollama\nllama3.2\nFully offline"]
        DDG["DuckDuckGo\nddgs v9.16.0\nKeyless search"]
        EDGE["edge-tts v7.2.7\nMicrosoft free TTS"]
        GROQ_STT["Groq Whisper\nwhisper-large-v3-turbo\nFree STT"]
        PW["Playwright Chromium\nLocal browser\nNo cloud cost"]
        SQLITE["SQLite\nLocal memory DB"]
        SKILLS_L["Local YAML skills\nNo network"]
    end

    HERMES([Hermes Agent]) --> GROQ_LLM
    HERMES -.->|fallback| OC
    HERMES -.->|fallback| OLLAMA_L
    HERMES --> DDG
    HERMES --> EDGE
    HERMES --> GROQ_STT
    HERMES --> PW
    HERMES --> SQLITE
    HERMES --> SKILLS_L
```

---

## 13. File Size Reference (Largest = Most Complex)

| File | Size | Role |
|------|------|------|
| `plugins/platforms/discord/adapter.py` | 360 KB | Discord platform adapter |
| `plugins/platforms/slack/adapter.py` | 349 KB | Slack platform adapter |
| `gateway/run.py` | 291 KB | Gateway facade (15 siblings) |
| `agent/context_compressor.py` | 283 KB | Context compression engine |
| `agent/conversation_compression.py` | 220 KB | Conversation compression |
| `gateway/platforms/api_server.py` | 219 KB | HTTP API server |
| `cli.py` | 216 KB | Main CLI (12 mixin siblings) |
| `hermes_cli/config_defaults.py` | 190 KB | All default config values |
| `agent/agent_runtime_helpers.py` | 178 KB | Agent runtime utilities |
| `hermes_cli/config.py` | 176 KB | Config load/merge/validate |
| `agent/prompt_builder.py` | 99 KB | System prompt assembly |
| `tools/file_operations.py` | 77 KB | File read/write/patch |
| `tools/terminal_tool.py` | ~60 KB | Terminal execution |
| `tools/browser_tool.py` | ~50 KB | Browser automation |

> **Facade + siblings pattern**: Every large file is a facade. Siblings are `<stem>_<topic>.py`.
> Find code by topic: `grep -rn "def funcname" tools/terminal_tool*.py`

---

*Generated: 2026-09-14 | Read brain.md for the complete setup guide.*
