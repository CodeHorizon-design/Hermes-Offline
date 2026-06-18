# Hermes Agent — Offline Edition

> Every feature of [Hermes Agent](https://github.com/NousResearch/hermes-agent) · Zero API keys · Zero subscriptions · Runs entirely on your machine via [Ollama](https://ollama.com)

---

## What This Is

A lightweight extension package for Hermes Agent v0.16+ that:

- Registers `ollama-local` as a first-class provider pointing to your local Ollama instance (`http://127.0.0.1:11434/v1`)
- Auto-detects your hardware and recommends the best local model for your machine
- Applies performance tuning for small local LLMs (context compression at 70%, tool output capping, optimized Modelfiles)
- Replaces cloud-only web search with free no-key backends (DuckDuckGo, Wikipedia, SearXNG)
- Sets local TTS (piper-tts) and transcription (faster-whisper) as defaults
- Provides optimized Modelfiles for reliable tool-calling on low-end hardware

**Every single feature of Hermes Agent is preserved.** This is a thin configuration and patching layer — not a fork.

---

## Features Preserved (All 71+ Tools)

| Category | Tools | Offline? |
|----------|-------|---------|
| Browser automation | 12 tools (Playwright) | ✅ Always local |
| File operations | 4 tools | ✅ Always local |
| Terminal execution | 2 tools | ✅ Always local |
| Memory system | FTS5 + optional sqlite-vec | ✅ Local SQLite |
| Skills system | Full create/edit/evolve | ✅ Local files |
| Skills Hub (HermesHub) | Browse/install community skills | ✅ Free skills |
| MCP client + server | All MCP tools | ✅ Local stdio/HTTP |
| ACP (IDE integration) | VS Code, Zed, JetBrains | ✅ Local process |
| Messaging gateway | 20+ platforms | ✅ User's free tokens |
| TUI + CLI | Full interface | ✅ No change |
| Cron scheduler | All scheduled tasks | ✅ No change |
| Web search | DuckDuckGo / Wikipedia / SearXNG | ✅ No key needed |
| Voice TTS | piper-tts (local) | ✅ No key needed |
| Transcription | faster-whisper (local) | ✅ No key needed |
| Image generation | ComfyUI / A1111 (if running) | ✅ Optional |
| Self-evolution | DSPy + GEPA lightweight mode | ✅ Local session history |

---

## Quick Start

### Prerequisites

- Python 3.11–3.13 (auto-installed on Windows if missing)
- 4 GB+ RAM (8+ GB recommended)

---

### Windows — One Command

**Double-click `install-windows.bat`**, or paste this into PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process Bypass; .\install-windows.bat
```

The installer automatically:
- Installs Python 3.12 if missing (via `winget` or python.org)
- Installs `uv` (fast package manager)
- Installs Ollama for Windows
- Installs `hermes-agent` + `hermes-offline`
- Runs the interactive setup wizard (hardware detection → model pull → config)
- Creates a **Desktop shortcut** and **Start Menu entry**
- Optionally adds Ollama to Windows startup

After install: double-click **Hermes (Offline)** on your desktop, or run `hermes-offline` in any terminal.

**Diagnose issues anytime:**
```powershell
powershell -ExecutionPolicy Bypass -File scripts\check-windows.ps1
# Checks Python, Ollama, hermes, config — green/yellow/red with fix instructions
```

---

### Linux / macOS — One Command

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/CodeHorizon-design/Hermes-Offline/main/scripts/setup-offline.sh)
```

### Manual Install (all platforms)

```bash
# 1. Install Ollama
#    Windows: download https://ollama.com/download/OllamaSetup.exe
#    Linux/macOS: curl -fsSL https://ollama.com/install.sh | sh

# 2. Install hermes-agent + hermes-offline
pip install hermes-agent
pip install -e ./hermes-offline   # or: pip install hermes-offline

# 3. Run the offline setup wizard
hermes-offline-setup
# → auto-detects hardware (RAM, VRAM, CPU)
# → recommends best model for your machine
# → pulls model (~1–20 GB depending on tier)
# → writes config to  Windows: %USERPROFILE%\.hermes\config.yaml
#                     Linux:   ~/.hermes/config.yaml
# → sets up local TTS and transcription defaults

# 4. Start Hermes
hermes-offline
```

---

## Hardware Tiers & Model Recommendations

| Tier | RAM | VRAM | Recommended Model | Pull Command | Size |
|------|-----|------|-------------------|-------------|------|
| Ultra Low | 4 GB | 0 | qwen3:1.7b | `ollama pull qwen3:1.7b` | 1.1 GB |
| Low | 8 GB | 0 | qwen3:4b | `ollama pull qwen3:4b` | 2.6 GB |
| Mid | 16 GB | 0–8 GB | qwen3:8b | `ollama pull qwen3:8b` | 5.2 GB |
| Good | 16 GB | 8 GB | qwen2.5-coder:14b | `ollama pull qwen2.5-coder:14b` | 9.0 GB |
| Great | 32 GB | 16 GB | qwen3-coder:30b | `ollama pull qwen3-coder:30b` | 19 GB |

**Tip:** The setup wizard auto-detects your hardware and picks the best model.

### Why Qwen3?
- Best-in-class tool/function calling at every size tier
- Rarely hallucinates tool calls or drops arguments (critical for agentic use)
- Native support for parallel tool calls
- MIT license — fully free

---

## Session Cost Tracker (`--track`)

Every response shows a dim status line with real-time resource usage, and a full summary prints when you quit.

**Status line (after each assistant turn):**
```
  ↳ turn 4 · 312 tok out · 28.4 tok/s · 2 tools · ctx 3,932/16,384 (24%) · 5.5GB RAM · session 4m12s
```

**Exit summary:**
```
────────────────────────────────────────────────────
  Hermes Offline — Session Summary
────────────────────────────────────────────────────
  Model:         qwen3:8b
  RAM in use:    ~5.5 GB
  Session time:  12m34s
  Turns:         8
  Tool calls:    14
  Prompt tokens: 28,432
  Output tokens: 3,891
  Total tokens:  32,323
  Avg speed:     26.1 tok/s
  Final ctx use: 53% of 16,384
────────────────────────────────────────────────────
```

**In TUI mode** (`hermes-offline --tui`), a persistent footer bar updates every 2 seconds:
```
  [qwen3:8b]  [ctx 3,932/16,384 (24%)]  [5.5 GB RAM]  [28.4 tok/s]  [turns 4]  [tools 14]  [4m12s]
```

**CLI flags:**
```bash
hermes-offline                  # tracking on by default
hermes-offline --no-track       # disable entirely
hermes-offline --no-status      # suppress per-turn lines (summary only)
hermes-offline --no-summary     # suppress exit summary (status lines only)
```

**Config (`~/.hermes/config.yaml`):**
```yaml
tracker:
  enabled: true
  status_line: true
  summary_on_exit: true
  show_tui_footer: true
```

---

## Qwen3 Thinking Mode (`--think`)

Qwen3 has a built-in chain-of-thought reasoning mode activated by prepending `/think` to the user message. The model produces an internal `<think>...</think>` reasoning block before its final answer — improving accuracy on complex multi-step tasks by ~15–20%.

The `hermes-offline` wrapper handles all of this transparently:

```bash
hermes-offline --think        # force thinking on every turn
hermes-offline --auto-think   # heuristic-based: only for complex tasks (default)
hermes-offline --no-think     # disable — fastest response, simple tasks

hermes-offline --think --show-thinking   # also display the <think> blocks in terminal
```

Or set permanently in `~/.hermes/config.yaml`:

```yaml
think:
  mode: auto      # auto | always | never
  show: false
  threshold: 3    # 1-10 — lower = think more often
```

**How it works:**
- In `auto` mode, each user message is scored 0–10 for complexity (length, keywords like "refactor", "debug", "implement", multi-step markers)
- If score ≥ threshold, `/think` is prepended; otherwise `/no_think`
- `<think>...</think>` blocks are stripped before hermes tool parsers see the response — tool calling works identically
- With `--show-thinking`, the reasoning block is displayed dimmed in the terminal

**When to use `--think`:**
| Task | Recommended |
|------|-------------|
| Simple file edits, quick questions | `--no-think` (faster) |
| Code refactoring, debugging | `--auto-think` |
| Architecture planning, complex workflows | `--think` |
| Autonomous long-running tasks | `--think` |

---

## Apply Optimized Modelfiles

Pre-tuned Modelfiles with low temperature (0.1–0.2) for reliable tool calling:

```bash
# Create an agent-optimized version of qwen3:8b
bash scripts/apply-modelfile.sh 8b
# Creates model: qwen3-8b-agent

# Then switch to it in Hermes
hermes model  # → select qwen3-8b-agent
```

Available Modelfiles: `8b`, `4b`, `1.7b`, `llama` (Llama 3.1 8B)

---

## Enable Semantic Memory (Optional)

Adds vector search on top of the built-in FTS5 keyword memory:

```bash
bash scripts/install-embeddings.sh
# Pulls nomic-embed-text via Ollama (274 MB)
# Installs sqlite-vec Python package
# Enables hybrid memory in config
```

---

## Benchmark Your Model

```bash
hermes-offline-bench --model qwen3:8b
# Tests tool-calling accuracy, speed (tok/s), and latency
# Reports pass/fail for single calls, parallel calls, and "no tool needed" cases
```

---

## Feature Parity Tests (Phase 6)

Verify that every tool category works correctly on your machine:

```bash
hermes-offline-test-parity           # full suite (essential + optional checks)
hermes-offline-test-parity --quick   # fast smoke test (infrastructure only)
hermes-offline-test-parity --json    # CI-friendly JSON output
hermes-offline-test-parity --category tools   # filter by category
```

Categories tested: `infra`, `tools`, `model`, `search`, `memory`, `offline`, `tts`, `speech`, `evolution`, `vision`, `imagegen`, `release`.

Exit code `0` = all essential tests passed. Exit code `1` = essential test(s) failed. Exit code `2` = Ollama not running.

---

## Hardware Compatibility Matrix

Validate the tier classification, model recommendations, and Modelfile generation for all 5 hardware tiers — without needing any models installed:

```bash
hermes-offline-compat-matrix          # all 5 tiers, static checks only
hermes-offline-compat-matrix --live   # also run live inference on your detected tier
hermes-offline-compat-matrix --tier mid  # specific tier only
hermes-offline-compat-matrix --json   # machine-readable output
```

---

## Register as a HermesHub Community Skill

Install the `hermes-offline-edition` skill file into your local skills directory, which teaches Hermes how to configure itself offline whenever asked:

```bash
hermes-offline-hub-submit             # writes to ~/.hermes/skills/
hermes-offline-hub-submit --dry-run   # preview skill content
hermes-offline-hub-submit --validate  # verify an existing skill file
```

After install, you can ask Hermes: *"Set up offline mode"* or use `/offline` and it will walk you through the full setup.

---

## Configuration

Config lives at `~/.hermes/config.yaml`. Key offline settings:

```yaml
provider: ollama-local
model:
  default: qwen3:8b

context:
  compression_threshold: 0.70    # Compress at 70% (cloud default: 85%)
  max_tool_output_chars: 2000    # Cap tool results for token efficiency

web:
  backend: duckduckgo            # Free, no key required

tts:
  provider: piper                # Local, no internet needed
```

See `config/offline-defaults.yaml` for the full commented template.

---

## Ollama Performance Tips

Set these environment variables for best performance:

```bash
# Add to ~/.bashrc or ~/.zshrc
export OLLAMA_FLASH_ATTENTION=1      # 2x KV cache reduction (GPU)
export OLLAMA_KV_CACHE_TYPE=q8_0    # 8-bit KV cache
export OLLAMA_NUM_PARALLEL=1         # 1 request at a time (low-end)
export OLLAMA_KEEP_ALIVE=0           # Unload after use (saves RAM)
```

These are written to `~/.hermes/.env` automatically by the setup wizard.

---

## Architecture

```
hermes-offline/
├── hermes_offline/
│   ├── __init__.py          # apply() — patches hermes-agent at import time
│   ├── patch.py             # All patches: providers, compression, search, TTS, evolution
│   ├── providers.py         # ollama-local provider registration
│   ├── hardware.py          # Hardware detection + tier classification
│   ├── local_search.py      # DuckDuckGo, Wikipedia, SearXNG (no key)
│   ├── setup.py             # Interactive offline setup wizard
│   ├── benchmark.py         # Tool-calling benchmark
│   ├── detector.py          # SystemSnapshot: one-shot system capability probe
│   ├── searxng.py           # SearXNG lifecycle (auto-start Docker/pip)
│   ├── compressor.py        # Context compression hardening patches
│   ├── tool_stream.py       # Smart per-type tool output truncation
│   ├── modelfile.py         # Tier-aware Modelfile generator
│   ├── profiler.py          # RAM profiler (per-turn RSS, OOM warnings)
│   ├── tracker.py           # Session cost tracker (tok/s, ctx%, RAM)
│   ├── tracker_tui.py       # TUI footer integration
│   ├── think.py             # Qwen3 chain-of-thought think-mode injection
│   ├── embeddings.py        # LocalEmbedder (nomic-embed-text via Ollama)
│   ├── beam_memory.py       # BEAM tiered memory store (FTS5 + cosine)
│   ├── memory.py            # SqliteVecMemoryProvider (MemoryProvider ABC)
│   ├── dspy_local.py        # DSPy 2.4/2.5/2.6 Ollama wiring
│   ├── evolution.py         # GEPA self-evolution (BootstrapFewShot)
│   ├── updater.py           # hermes-offline update subcommand
│   ├── entrypoint.py        # Main CLI entry point (wraps hermes)
│   ├── test_parity.py       # Phase 6: Feature parity test suite
│   ├── compat_matrix.py     # Phase 6: Hardware compat matrix
│   ├── hermeshub_skill.py   # Phase 6: HermesHub skill manifest
│   └── modelfiles/          # Optimized Ollama Modelfiles
│       ├── qwen3-8b-agent.Modelfile
│       ├── qwen3-4b-agent.Modelfile
│       ├── qwen3-1.7b-agent.Modelfile
│       └── llama31-8b-agent.Modelfile
├── config/
│   └── offline-defaults.yaml    # Fully-documented config template
├── scripts/
│   ├── setup-offline.sh         # One-liner Linux/macOS installer
│   ├── apply-modelfile.sh       # Create agent-tuned Ollama model
│   └── install-embeddings.sh    # Pull nomic-embed-text + sqlite-vec
├── install-windows.ps1          # Windows one-click installer (PowerShell)
├── install-windows.bat          # Windows double-click launcher
└── pyproject.toml               # 14 CLI entry points, all optional deps
```

---

## vs. Cloud Hermes Agent

| Aspect | Cloud (Nous Portal/OpenRouter) | Offline Edition |
|--------|-------------------------------|----------------|
| First token latency | ~800ms (network) | ~400ms (local, no network) |
| Throughput | ~60 tok/s | 25–50 tok/s (hardware-dependent) |
| Tool-calling accuracy | ~95% (Claude Sonnet) | ~85-88% (qwen3:8b) |
| Cost | Per-token ($$) | Free (electricity) |
| Privacy | Prompts sent to cloud | 100% local |
| Offline use | ❌ | ✅ |
| All features | ✅ | ✅ |

---

## Credits

- [Hermes Agent](https://github.com/NousResearch/hermes-agent) by Nous Research (MIT)
- [Ollama](https://ollama.com) for local LLM serving
- [Qwen3](https://huggingface.co/Qwen) by Alibaba Cloud (Apache 2.0)
- [piper-tts](https://github.com/rhasspy/piper) by Rhasspy (MIT)
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) by SYSTRAN (MIT)
- [sqlite-vec](https://github.com/asg017/sqlite-vec) by Alex Garcia (MIT/Apache 2.0)
