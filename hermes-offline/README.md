# Hermes Agent — Offline Edition

> **Every feature of [Hermes Agent](https://github.com/NousResearch/hermes-agent) · Zero API keys · Zero subscriptions · Runs entirely on your machine**

Hermes Offline is a thin extension package that re-targets Hermes Agent at a local [Ollama](https://ollama.com) instance instead of cloud APIs. All 71+ built-in tools, the memory system, skills hub, MCP, TUI, cron scheduler, and ACP server work exactly as before — no features removed, no cloud required.

---

## Table of Contents

1. [What's included](#whats-included)
2. [Requirements](#requirements)
3. [Hardware tiers & model recommendations](#hardware-tiers--model-recommendations)
4. [Installation — Windows](#installation--windows)
5. [Installation — Linux](#installation--linux)
6. [Installation — macOS](#installation--macos)
7. [Manual installation (all platforms)](#manual-installation-all-platforms)
8. [Post-install verification](#post-install-verification)
9. [First run](#first-run)
10. [Optional features](#optional-features)
11. [Configuration reference](#configuration-reference)
12. [CLI reference](#cli-reference)
13. [Thinking mode (Qwen3)](#thinking-mode-qwen3)
14. [Session cost tracker](#session-cost-tracker)
15. [Optimized Modelfiles](#optimized-modelfiles)
16. [Semantic memory](#semantic-memory)
17. [Self-evolution (DSPy)](#self-evolution-dspy)
18. [Ollama performance tuning](#ollama-performance-tuning)
19. [Updating](#updating)
20. [Troubleshooting](#troubleshooting)
21. [vs. Cloud Hermes Agent](#vs-cloud-hermes-agent)
22. [Architecture](#architecture)
23. [Credits](#credits)

---

## What's included

| Category | Works offline? | Notes |
|----------|---------------|-------|
| All 71+ built-in tools | ✅ Always | File, terminal, browser (Playwright), memory, skills, MCP, etc. |
| Browser automation (12 tools) | ✅ Always | Playwright runs locally |
| Web search | ✅ No key needed | DuckDuckGo · Wikipedia · SearXNG (self-hosted) |
| Memory — keyword (FTS5) | ✅ Always | SQLite built-in |
| Memory — semantic (vector) | ✅ Optional | `sqlite-vec` + `nomic-embed-text` |
| Voice TTS | ✅ Optional | `piper-tts` — fully local |
| Voice transcription | ✅ Optional | `faster-whisper` — fully local |
| Image generation | ✅ Optional | ComfyUI or Automatic1111 if running |
| Skills system + HermesHub | ✅ Always | Local files + free community skills |
| MCP client / server | ✅ Always | Local stdio / HTTP |
| ACP (IDE integration) | ✅ Always | VS Code, Zed, JetBrains |
| Messaging gateway | ✅ Always | 20+ platforms, user's own tokens |
| TUI + CLI | ✅ Always | Identical to cloud edition |
| Cron scheduler | ✅ Always | Local process |
| Self-evolution (DSPy) | ✅ Optional | Lightweight mode, local session history |
| Session cost tracker | ✅ Always | tok/s, RAM, context%, per-turn stats |
| Chain-of-thought thinking | ✅ Always | Qwen3 `/think` mode |

---

## Requirements

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.11 – 3.13 | Auto-installed on Windows if missing |
| Ollama | Latest | Local LLM server — [ollama.com](https://ollama.com) |
| RAM | 4 GB minimum | 8 GB+ recommended |
| Disk | 2 – 20 GB | For the model (size depends on tier) |
| OS | Windows 10+, Linux, macOS 12+ | |

> **No API keys, no accounts, no subscriptions required.**

---

## Hardware tiers & model recommendations

The setup wizard auto-detects your hardware and recommends the best model. Here is the full matrix:

| Tier | RAM | VRAM | Recommended model | Pull command | Download size |
|------|-----|------|-------------------|-------------|--------------|
| Ultra Low | 4 GB | 0 | `qwen3:1.7b` | `ollama pull qwen3:1.7b` | 1.1 GB |
| Low | 8 GB | 0 | `qwen3:4b` | `ollama pull qwen3:4b` | 2.6 GB |
| Mid | 16 GB | 0–8 GB | `qwen3:8b` | `ollama pull qwen3:8b` | 5.2 GB |
| Good | 16 GB | 8 GB | `qwen2.5-coder:14b` | `ollama pull qwen2.5-coder:14b` | 9.0 GB |
| Great | 32 GB+ | 16 GB+ | `qwen3-coder:30b` | `ollama pull qwen3-coder:30b` | 19 GB |

**Why Qwen3?** Best tool/function-calling accuracy at every size tier, native parallel tool call support, rarely hallucinates tool calls, MIT license.

---

## Installation — Windows

### Option A: Double-click installer (recommended)

1. [Download or clone this repository](https://github.com/CodeHorizon-design/Hermes-Offline)
2. Open the downloaded folder
3. **Double-click `install-windows.bat`**

The installer will:
- Install Python 3.12 if missing (via `winget` or python.org — no admin required)
- Install `uv` (fast package manager)
- Install Ollama for Windows (via `winget` or direct download)
- Install `hermes-agent` and `hermes-offline`
- Run the interactive setup wizard (hardware detection → model recommendation → model pull → config write)
- Create a **Desktop shortcut** and **Start Menu entry**
- Optionally add Ollama to Windows startup

After install: double-click **Hermes (Offline)** on your Desktop, or open any terminal and run:
```
hermes-offline
```

---

### Option B: PowerShell one-liner

Open **PowerShell** (not Command Prompt) and paste:

```powershell
Set-ExecutionPolicy -Scope Process Bypass; irm https://raw.githubusercontent.com/CodeHorizon-design/Hermes-Offline/main/install-windows.ps1 | iex
```

This runs the same full installer as Option A.

---

### Option C: Step-by-step manual (Windows)

**Step 1 — Install Python 3.12**

Check if you already have a compatible version:
```powershell
python --version
```
If it shows `3.11`, `3.12`, or `3.13` you're good. Otherwise:
```powershell
winget install --id Python.Python.3.12 --accept-source-agreements
```
Or download from [python.org/downloads](https://www.python.org/downloads/). During install, tick **"Add Python to PATH"**.

**Step 2 — Install uv** (optional but faster)
```powershell
irm https://astral.sh/uv/install.ps1 | iex
```

**Step 3 — Install Ollama**
```powershell
winget install --id Ollama.Ollama --accept-source-agreements
```
Or download [OllamaSetup.exe](https://ollama.com/download/windows) and run it.

After install, start the server:
```powershell
ollama serve
```
Leave this window open (or Ollama runs as a system tray app automatically).

**Step 4 — Install hermes-agent and hermes-offline**
```powershell
pip install hermes-agent hermes-offline
```
Or with uv:
```powershell
uv pip install --system hermes-agent hermes-offline
```

**Step 5 — Run the setup wizard**
```powershell
hermes-offline-setup
```
This detects your hardware, recommends a model, pulls it, and writes `%USERPROFILE%\.hermes\config.yaml`.

**Step 6 — Start Hermes**
```powershell
hermes-offline
```

---

## Installation — Linux

### Option A: One-line installer

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/CodeHorizon-design/Hermes-Offline/main/scripts/setup-offline.sh)
```

This installs everything interactively: Python check, uv, Ollama, hermes-agent, hermes-offline, optional tools (piper-tts, faster-whisper, sqlite-vec), and runs the setup wizard.

---

### Option B: Step-by-step manual (Linux)

**Step 1 — Check Python**
```bash
python3 --version
```
Needs to be 3.11, 3.12, or 3.13. On Ubuntu/Debian:
```bash
sudo apt update && sudo apt install python3.12 python3.12-pip -y
```
On Fedora/RHEL:
```bash
sudo dnf install python3.12 -y
```
On Arch:
```bash
sudo pacman -S python
```

**Step 2 — Install uv** (recommended)
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc   # or restart your terminal
```

**Step 3 — Install Ollama**
```bash
curl -fsSL https://ollama.com/install.sh | sh
```
Start the server (or it starts automatically as a systemd service):
```bash
ollama serve &
```

**Step 4 — Install packages**
```bash
uv pip install --system hermes-agent hermes-offline
# or without uv:
pip3 install hermes-agent hermes-offline
```

**Step 5 — Run setup wizard**
```bash
hermes-offline-setup
```

**Step 6 — Start**
```bash
hermes-offline
```

---

## Installation — macOS

### Option A: One-line installer

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/CodeHorizon-design/Hermes-Offline/main/scripts/setup-offline.sh)
```

---

### Option B: Step-by-step manual (macOS)

**Step 1 — Install Python 3.12**

Check if you have a compatible version:
```bash
python3 --version
```
If not (or if it's the Apple-provided 3.9):
```bash
brew install python@3.12
```
No Homebrew? Install it first:
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

**Step 2 — Install uv**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Step 3 — Install Ollama**

Download the macOS app from [ollama.com/download](https://ollama.com/download) and drag it to Applications, or:
```bash
brew install ollama
ollama serve &
```

**Step 4 — Install packages**
```bash
uv pip install --system hermes-agent hermes-offline
```

**Step 5 — Run setup wizard**
```bash
hermes-offline-setup
```

**Step 6 — Start**
```bash
hermes-offline
```

**Apple Silicon (M1/M2/M3/M4) note:** Ollama uses Metal GPU acceleration automatically. You get excellent performance even at 16 GB RAM (qwen3:8b runs at 30–50 tok/s on M2 16 GB).

---

## Manual installation (all platforms)

If you prefer full control without the installer scripts:

```bash
# 1. Install Ollama
#    Linux/macOS: curl -fsSL https://ollama.com/install.sh | sh
#    Windows:     download https://ollama.com/download/windows

# 2. Start Ollama
ollama serve

# 3. Pull a model matching your hardware (see tier table above)
ollama pull qwen3:8b

# 4. Install the Python packages
pip install hermes-agent hermes-offline

# 5. Run the setup wizard (writes config, applies offline defaults)
hermes-offline-setup

# 6. Start
hermes-offline
```

### Installing from source

```bash
git clone https://github.com/CodeHorizon-design/Hermes-Offline.git
cd Hermes-Offline/hermes-offline

pip install hermes-agent
pip install -e .           # editable install — changes take effect immediately

hermes-offline-setup
hermes-offline
```

### Installing with all optional features

```bash
pip install hermes-offline[all]
# Includes: piper-tts, faster-whisper, sqlite-vec, dspy-ai
```

Or install optional groups individually:
```bash
pip install hermes-offline[tts]        # piper-tts (local voice output)
pip install hermes-offline[whisper]    # faster-whisper (local transcription)
pip install hermes-offline[embeddings] # sqlite-vec (semantic memory)
pip install hermes-offline[evolution]  # dspy-ai (self-evolution)
```

---

## Post-install verification

After installation, run the parity test suite to confirm everything works on your machine:

```bash
hermes-offline-test-parity
```

For a quick smoke test only (30 seconds):
```bash
hermes-offline-test-parity --quick
```

For CI or scripting:
```bash
hermes-offline-test-parity --json
# Exit 0 = all essential tests passed
# Exit 1 = one or more essential tests failed
# Exit 2 = Ollama not running
```

Check the status of all components:
```bash
hermes-offline-status
```

Run the hardware compatibility matrix (validates model recommendations for all 5 tiers — no models needed):
```bash
hermes-offline-compat-matrix
```

---

## First run

```bash
hermes-offline              # interactive chat (default)
hermes-offline --tui        # full terminal UI with panels and footer
hermes-offline --think      # enable Qwen3 chain-of-thought reasoning
```

The first run applies all offline patches automatically. You'll see a status line after each response:
```
↳ turn 1 · 84 tok out · 28.4 tok/s · 1 tool · ctx 1,024/16,384 (6%) · 5.5GB RAM
```

---

## Optional features

### Local TTS (piper-tts)

Offline text-to-speech, no internet required after install:
```bash
pip install piper-tts
```
The setup wizard or patcher auto-detects piper and sets it as the TTS backend.

### Local transcription (faster-whisper)

Offline voice input:
```bash
pip install faster-whisper
```

### Semantic memory (sqlite-vec)

Adds vector search on top of keyword (FTS5) memory. Requires pulling the embedding model once (274 MB):
```bash
bash scripts/install-embeddings.sh
# Pulls nomic-embed-text, installs sqlite-vec, enables hybrid memory in config
```
Or manually:
```bash
pip install sqlite-vec
ollama pull nomic-embed-text
```
Then add to `~/.hermes/config.yaml`:
```yaml
memory:
  semantic_backend: sqlite_vec
```

### Local image generation

If you have [ComfyUI](https://github.com/comfyanonymous/ComfyUI) or [Automatic1111](https://github.com/AUTOMATIC1111/stable-diffusion-webui) running locally, hermes-offline auto-detects them at startup and registers them as image generation backends. No extra configuration needed.

### Self-hosted web search (SearXNG)

For best-quality web search with no tracking:
```bash
docker run -d -p 8080:8080 searxng/searxng
```
hermes-offline auto-detects SearXNG and uses it as the primary search backend. DuckDuckGo and Wikipedia are always available as fallbacks.

---

## Configuration reference

Config lives at:
- **Linux / macOS:** `~/.hermes/config.yaml`
- **Windows:** `%USERPROFILE%\.hermes\config.yaml`

The setup wizard writes this file automatically. Here is the full offline configuration with all available options:

```yaml
# ── Provider ──────────────────────────────────────────────────────────────────
provider: ollama-local
endpoint: http://127.0.0.1:11434/v1
api_key: ollama                      # any non-empty string works

# ── Model ─────────────────────────────────────────────────────────────────────
model:
  default: qwen3:8b                  # change to match your hardware tier
  # fallback: qwen3:4b               # used if default is not available

# ── Context ───────────────────────────────────────────────────────────────────
context:
  compression_threshold: 0.70        # compress at 70% full (cloud default: 85%)
  max_tool_output_chars: 2000        # cap large tool results to save tokens

# ── Web search ────────────────────────────────────────────────────────────────
web:
  backend: duckduckgo                # duckduckgo | wikipedia | searxng | auto
  searxng_url: http://127.0.0.1:8080 # only if running SearXNG locally

# ── TTS ───────────────────────────────────────────────────────────────────────
tts:
  provider: piper                    # piper | neutts | none

# ── Memory ────────────────────────────────────────────────────────────────────
memory:
  semantic_backend: sqlite_vec       # remove this line to use FTS5 only

# ── Session cost tracker ──────────────────────────────────────────────────────
tracker:
  enabled: true
  status_line: true                  # show usage line after each response
  summary_on_exit: true              # print summary when you quit
  show_tui_footer: true              # live footer in --tui mode

# ── Thinking mode (Qwen3 only) ────────────────────────────────────────────────
think:
  mode: auto                         # auto | always | never
  show: false                        # show <think> blocks in terminal
  threshold: 3                       # 1–10; lower = think more often

# ── Self-evolution ────────────────────────────────────────────────────────────
evolution:
  mode: disabled                     # disabled | lightweight
  evolve_every: 5                    # sessions between evolution runs
  auto_evolve: true
  population_size: 2
  eval_budget: 5
```

See `config/offline-defaults.yaml` for the fully documented template with comments on every option.

---

## CLI reference

### Core commands

```bash
hermes-offline                    # Start interactive offline session
hermes-offline --tui              # Full terminal UI
hermes-offline --think            # Force Qwen3 chain-of-thought every turn
hermes-offline --auto-think       # Heuristic-based thinking (default)
hermes-offline --no-think         # Disable thinking — fastest response
hermes-offline --show-thinking    # Print <think> blocks while reasoning
hermes-offline --no-track         # Disable session cost tracker
hermes-offline --no-status        # Suppress per-turn status line
hermes-offline --no-summary       # Suppress exit summary
hermes-offline --evolution-mode=lightweight   # Override evolution mode
```

### Subcommands

```bash
hermes-offline update             # Check for updates and upgrade
hermes-offline update --check     # Check only, don't upgrade
hermes-offline update --dry-run   # Show what would change
hermes-offline evolve             # Run DSPy self-evolution on session history
hermes-offline evolve --reset     # Clear evolved program and start fresh
hermes-offline evolve --dry-run   # Show what evolution would produce
```

### Utility commands

```bash
hermes-offline-setup              # Re-run the interactive setup wizard
hermes-offline-status             # Show all component status (Rich table)
hermes-offline-status --json      # Machine-readable JSON status
hermes-offline-bench              # Benchmark tool-calling accuracy + speed
hermes-offline-bench --model qwen3:8b
hermes-offline-patch              # Apply patches standalone (diagnostic)
hermes-offline-modelfile          # Generate a tier-tuned Modelfile
hermes-offline-evolve             # Alias for 'hermes-offline evolve'
```

### Phase 6 — Testing & release tools

```bash
hermes-offline-test-parity                    # Full feature parity test suite
hermes-offline-test-parity --quick            # Smoke test only (infra + tools)
hermes-offline-test-parity --category search  # Filter by category
hermes-offline-test-parity --json             # CI-friendly JSON output
hermes-offline-test-parity --model qwen3:4b   # Override model

hermes-offline-compat-matrix                  # Hardware compat matrix (all tiers)
hermes-offline-compat-matrix --tier mid       # Specific tier only
hermes-offline-compat-matrix --live           # Run real inference on detected tier
hermes-offline-compat-matrix --json           # JSON output

hermes-offline-hub-submit                     # Install HermesHub skill locally
hermes-offline-hub-submit --dry-run           # Preview skill file content
hermes-offline-hub-submit --validate          # Validate an existing skill file
hermes-offline-hub-submit --manifest          # Also write manifest JSON
```

---

## Thinking mode (Qwen3)

Qwen3 models support chain-of-thought reasoning via `/think`. The model reasons internally before answering, improving accuracy on complex tasks by ~15–20%.

```bash
hermes-offline --think           # thinking on every turn
hermes-offline --auto-think      # only for complex prompts (default)
hermes-offline --no-think        # off — fastest, for simple tasks
hermes-offline --show-thinking   # also print the <think> blocks
```

Or set permanently in `~/.hermes/config.yaml`:
```yaml
think:
  mode: auto     # auto | always | never
  show: false
  threshold: 3   # lower = think more often (range 1–10)
```

| Task type | Recommended mode |
|-----------|-----------------|
| Quick questions, simple file edits | `--no-think` |
| Code writing, debugging | `--auto-think` |
| Architecture planning, complex workflows | `--think` |
| Long autonomous tasks | `--think` |

---

## Session cost tracker

Every response shows live resource usage:
```
↳ turn 4 · 312 tok out · 28.4 tok/s · 2 tools · ctx 3,932/16,384 (24%) · 5.5GB RAM · 4m12s
```

Exit summary when you quit:
```
────────────────────────────────────────
  Hermes Offline — Session Summary
────────────────────────────────────────
  Model:         qwen3:8b
  Session time:  12m34s  |  Turns: 8
  Tool calls:    14
  Prompt tokens: 28,432  |  Output: 3,891
  Avg speed:     26.1 tok/s
  Peak RAM:      5.7 GB
  Final ctx:     53% of 16,384
────────────────────────────────────────
```

In `--tui` mode a persistent footer updates every 2 seconds:
```
[qwen3:8b]  [ctx 24%]  [5.5GB RAM]  [28.4 tok/s]  [turns 4]  [tools 14]  [4m12s]
```

---

## Optimized Modelfiles

Pre-tuned Modelfiles set low temperature (0.1–0.2) and appropriate context windows for reliable tool-calling. Apply them once and switch to the resulting model in Hermes:

```bash
# Generate and register a tuned model for your tier
hermes-offline-modelfile --tier mid      # creates qwen3-8b-agent in Ollama
hermes-offline-modelfile --tier low      # creates qwen3-4b-agent
hermes-offline-modelfile --tier ultra_low

# Or use the apply script directly
bash scripts/apply-modelfile.sh 8b      # creates qwen3-8b-agent
bash scripts/apply-modelfile.sh 4b
bash scripts/apply-modelfile.sh 1.7b
bash scripts/apply-modelfile.sh llama   # Llama 3.1 8B variant

# Then switch to the tuned model in Hermes
hermes model   # → select qwen3-8b-agent from the list
```

---

## Semantic memory

Adds vector search on top of built-in FTS5 keyword memory. Allows Hermes to find memories by meaning, not just exact words.

```bash
# Install everything at once
bash scripts/install-embeddings.sh
```

Or manually:
```bash
# Pull the embedding model (274 MB, one-time)
ollama pull nomic-embed-text

# Install the vector extension
pip install sqlite-vec

# Enable in config
echo "memory:" >> ~/.hermes/config.yaml
echo "  semantic_backend: sqlite_vec" >> ~/.hermes/config.yaml
```

---

## Self-evolution (DSPy)

After several sessions, hermes-offline can compile your interaction history into an optimized few-shot prompt using DSPy BootstrapFewShot. This improves tool-calling accuracy over time without any manual work.

```bash
# Install DSPy
pip install dspy-ai       # or: pip install hermes-offline[evolution]

# Enable lightweight evolution in config
# Add to ~/.hermes/config.yaml:
#   evolution:
#     mode: lightweight

# Run manually
hermes-offline evolve

# Or it runs automatically at session end (when auto_evolve: true)
```

---

## Ollama performance tuning

Add these to `~/.bashrc`, `~/.zshrc`, or `~/.profile` for best performance:

```bash
export OLLAMA_FLASH_ATTENTION=1      # Halves KV cache memory (GPU)
export OLLAMA_KV_CACHE_TYPE=q8_0    # 8-bit KV cache (GPU)
export OLLAMA_NUM_PARALLEL=1        # One request at a time (low-end systems)
export OLLAMA_KEEP_ALIVE=0          # Unload model after use (saves RAM)
```

The setup wizard writes these to `~/.hermes/.env` automatically.

**For CPU-only systems (no GPU):**
```bash
export OLLAMA_NUM_GPU=0             # Force CPU mode
```

**For Apple Silicon:**
```bash
# No extra env vars needed — Metal GPU is used automatically
# Recommended: OLLAMA_FLASH_ATTENTION=1 for M1/M2/M3 with 8 GB RAM
export OLLAMA_FLASH_ATTENTION=1
```

---

## Updating

```bash
# Check for updates
hermes-offline update --check

# Update everything
hermes-offline update

# Update without running compat checks
hermes-offline update --skip-compat

# Force update even if already on latest
hermes-offline update --force
```

Or update manually:
```bash
pip install --upgrade hermes-agent hermes-offline
```

---

## Troubleshooting

### Ollama not running

**Symptom:** `Cannot connect to Ollama at 127.0.0.1:11434`

```bash
# Start Ollama
ollama serve

# Verify it's running
curl http://127.0.0.1:11434/api/tags
```

On Linux, Ollama may run as a systemd service:
```bash
sudo systemctl start ollama
sudo systemctl enable ollama   # auto-start on boot
```

On Windows, check the system tray for the Ollama icon. If missing:
```powershell
ollama serve
```

---

### No model installed

**Symptom:** `no models are available` or `model not found`

```bash
ollama list                    # see what's installed
ollama pull qwen3:8b          # pull the recommended model
```

---

### `hermes-offline` command not found

**Linux / macOS:**
```bash
# Check where pip installs scripts
python3 -m site --user-base
# Add <user-base>/bin to your PATH:
export PATH="$HOME/.local/bin:$PATH"
# Add this line to ~/.bashrc or ~/.zshrc to make it permanent
```

**Windows:**
```powershell
# Find Python's Scripts folder
python -c "import sysconfig; print(sysconfig.get_path('scripts'))"
# Add that path to your User PATH in System Properties → Environment Variables
```

---

### Tool calls failing or model hallucinating tools

1. **Lower the temperature** — use a Modelfile tuned for agentic use:
   ```bash
   hermes-offline-modelfile --tier mid
   hermes model   # switch to qwen3-8b-agent
   ```

2. **Use a Qwen3 model** — they have the best tool-calling accuracy at every size:
   ```bash
   ollama pull qwen3:8b
   ```

3. **Run the benchmark** to measure accuracy on your setup:
   ```bash
   hermes-offline-bench --model qwen3:8b
   ```

4. **Enable thinking mode** for complex multi-step tasks:
   ```bash
   hermes-offline --think
   ```

---

### Context fills up quickly / responses get cut off

Increase `num_ctx` in your Modelfile, or switch to a tier with a larger context window:
```bash
hermes-offline-modelfile --tier good   # 32K context
```

Or temporarily set a larger context:
```bash
OLLAMA_NUM_CTX=32768 hermes-offline
```

---

### Slow inference (< 5 tok/s)

- Set `OLLAMA_FLASH_ATTENTION=1`
- Use a quantized model (`q4_K_M` or `q5_K_M` variants)
- Reduce `num_ctx` to 4096 or 8192
- On CPU-only systems: `qwen3:4b` or `qwen3:1.7b` are much faster than 8b

---

### High RAM usage / out of memory

```bash
# Free model RAM when not in use
export OLLAMA_KEEP_ALIVE=0

# Use a smaller model
ollama pull qwen3:4b

# Reduce context size in Modelfile
hermes-offline-modelfile --tier low    # 8K context
```

---

### piper-tts install fails

```bash
# Try installing build dependencies first
# Ubuntu/Debian:
sudo apt install python3-dev portaudio19-dev build-essential -y
pip install piper-tts

# macOS:
brew install portaudio
pip install piper-tts
```

---

### faster-whisper install fails

```bash
# Usually a CUDA/cuDNN version mismatch
# CPU-only version (no GPU):
pip install faster-whisper
# Set device to CPU in config:
# voice:
#   whisper_device: cpu
```

---

### Run diagnostics

```bash
hermes-offline-status           # full component status table
hermes-offline-test-parity      # run full parity test suite
hermes-offline-compat-matrix    # verify all 5 hardware tier configurations
```

---

## vs. Cloud Hermes Agent

| Aspect | Cloud (Nous Portal / OpenRouter) | Offline Edition |
|--------|----------------------------------|----------------|
| First token latency | ~800 ms (network round-trip) | ~400 ms (local, no network) |
| Throughput | ~60 tok/s | 8–50 tok/s (hardware-dependent) |
| Tool-calling accuracy | ~95% (Claude Sonnet) | ~85–88% (qwen3:8b) |
| Cost | Per-token billing | Free (electricity only) |
| Privacy | Prompts sent to cloud provider | 100% local, never leaves machine |
| Offline use | ❌ Requires internet | ✅ Works with no connection |
| All 71+ tools | ✅ | ✅ |
| Memory system | ✅ | ✅ |
| Skills / HermesHub | ✅ | ✅ |

---

## Architecture

```
hermes-offline/
├── hermes_offline/
│   ├── __init__.py          # apply() — patches hermes-agent at import time
│   ├── entrypoint.py        # Main CLI wrapper (parses offline flags, delegates to hermes)
│   ├── patch.py             # Orchestrates all 10 patch modules
│   ├── providers.py         # Registers ollama-local as a hermes provider
│   ├── hardware.py          # RAM/VRAM/CPU detection + tier classification
│   ├── detector.py          # SystemSnapshot — one-shot system capability probe
│   ├── setup.py             # Interactive setup wizard
│   ├── benchmark.py         # Tool-calling accuracy + speed benchmark
│   ├── compressor.py        # Context compression hardening
│   ├── tool_stream.py       # Per-type smart tool output truncation
│   ├── local_search.py      # DuckDuckGo, Wikipedia, SearXNG backends
│   ├── searxng.py           # SearXNG auto-start + lifecycle management
│   ├── modelfile.py         # Tier-aware Modelfile generator
│   ├── tracker.py           # Session cost tracker (tok/s, RAM, ctx%)
│   ├── tracker_tui.py       # TUI persistent footer
│   ├── profiler.py          # RAM profiler (per-turn RSS, OOM warnings)
│   ├── think.py             # Qwen3 /think mode injection + block stripping
│   ├── embeddings.py        # LocalEmbedder (nomic-embed-text via Ollama)
│   ├── beam_memory.py       # BEAM tiered memory store (FTS5 + cosine)
│   ├── memory.py            # SqliteVecMemoryProvider (MemoryProvider ABC)
│   ├── dspy_local.py        # DSPy 2.4/2.5/2.6 version-compat Ollama wiring
│   ├── evolution.py         # GEPA self-evolution engine (BootstrapFewShot)
│   ├── updater.py           # Version check + upgrade logic
│   ├── test_parity.py       # Feature parity test suite (Phase 6)
│   ├── compat_matrix.py     # Hardware compatibility matrix (Phase 6)
│   ├── hermeshub_skill.py   # HermesHub skill manifest + skill .md (Phase 6)
│   └── modelfiles/
│       ├── qwen3-8b-agent.Modelfile
│       ├── qwen3-4b-agent.Modelfile
│       ├── qwen3-1.7b-agent.Modelfile
│       └── llama31-8b-agent.Modelfile
├── config/
│   └── offline-defaults.yaml    # Fully-documented config template
├── scripts/
│   ├── setup-offline.sh         # One-line Linux/macOS installer
│   ├── apply-modelfile.sh       # Create a tuned Ollama model from Modelfile
│   └── install-embeddings.sh    # Pull nomic-embed-text + install sqlite-vec
├── install-windows.ps1          # Full Windows installer (PowerShell)
├── install-windows.bat          # Double-click launcher for install-windows.ps1
└── pyproject.toml               # 14 entry points, optional dep groups
```

**How patching works:** `hermes-offline` applies patches at Python import time, before any `hermes_cli` code runs. Each patch is wrapped in `try/except` — if hermes-agent's internals change, the patch degrades gracefully and the base agent continues working.

---

## Credits

- [Hermes Agent](https://github.com/NousResearch/hermes-agent) by Nous Research — MIT License
- [Ollama](https://ollama.com) — local LLM server
- [Qwen3](https://huggingface.co/Qwen) by Alibaba Cloud — Apache 2.0
- [piper-tts](https://github.com/rhasspy/piper) by Rhasspy — MIT License
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) by SYSTRAN — MIT License
- [sqlite-vec](https://github.com/asg017/sqlite-vec) by Alex Garcia — MIT / Apache 2.0
- [DSPy](https://github.com/stanfordnlp/dspy) by Stanford NLP — MIT License
- [uv](https://github.com/astral-sh/uv) by Astral — MIT / Apache 2.0

---

## License

MIT — see [LICENSE](LICENSE) for details.

---

*Problems? Open an issue at [github.com/CodeHorizon-design/Hermes-Offline/issues](https://github.com/CodeHorizon-design/Hermes-Offline/issues)*
