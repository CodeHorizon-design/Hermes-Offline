"""
HermesHub Skill Submission — Hermes Offline Edition.

This module generates the skill manifest file for submitting
hermes-offline as a community skill to HermesHub (agentskills.io).

The generated skill:
  - Teaches hermes to set itself up as offline-first when asked
  - Provides the slash-command /offline for quick setup
  - Documents all hermes-offline features for the agent's awareness
  - Registers itself via the standard HermesHub manifest format

Usage:
    hermes-offline-hub-submit               # write skill file to ~/.hermes/skills/
    hermes-offline-hub-submit --dry-run     # print skill content only
    hermes-offline-hub-submit --path /dir   # write to custom path
    hermes-offline-hub-submit --validate    # check existing skill file
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from typing import Optional

try:
    from rich.console import Console
    from rich.syntax import Syntax
    from rich.panel import Panel
    _RICH = True
except ImportError:
    _RICH = False


# ── Skill manifest ────────────────────────────────────────────────────────────

SKILL_MANIFEST = {
    "id": "hermes-offline-edition",
    "name": "Hermes Offline Edition",
    "version": "1.0.0",
    "description": (
        "Run Hermes Agent entirely offline via Ollama — zero API keys, zero subscriptions. "
        "Preserves all 71+ built-in tools, full memory system, skills hub, MCP, cron, and ACP server. "
        "Auto-detects hardware and recommends the best local model. "
        "Features: Qwen3 thinking mode, session cost tracker, BEAM tiered memory, "
        "local TTS (piper), local transcription (faster-whisper), local search (DDG/Wikipedia/SearXNG), "
        "local image gen (ComfyUI/A1111), DSPy self-evolution, RAM profiler."
    ),
    "author": "hermes-offline contributors",
    "license": "MIT",
    "homepage": "https://github.com/CodeHorizon-design/Hermes-Offline",
    "tags": [
        "offline", "ollama", "local-llm", "privacy", "no-api-key",
        "qwen3", "llama", "setup", "configuration", "hardware"
    ],
    "requires": {
        "hermes_agent": ">=0.16.0",
        "python": ">=3.11",
    },
    "install": "pip install hermes-offline",
    "entry_points": {
        "hermes-offline": "Run Hermes with all offline patches pre-applied",
        "hermes-offline-setup": "Interactive setup wizard (hardware-aware)",
        "hermes-offline-bench": "Benchmark tool-calling accuracy + speed",
        "hermes-offline-status": "Show component status (Rich table or --json)",
        "hermes-offline update": "Check for updates + upgrade",
    },
    "slash_command": "/offline",
}


# ── Skill file content ────────────────────────────────────────────────────────

SKILL_FILE_CONTENT = """\
# Hermes Offline Edition
*Version 1.0.0 — https://github.com/CodeHorizon-design/Hermes-Offline*

## What This Skill Does

This skill teaches you to configure and operate Hermes Agent in fully
offline mode via Ollama. When a user asks to run offline, go offline,
stop using cloud APIs, or set up local LLMs, follow these instructions.

## Quick Setup

When the user asks to run offline or set up local LLM:

1. Check if `hermes-offline` is installed:
   ```bash
   hermes-offline --version
   ```

2. If not installed:
   ```bash
   pip install hermes-offline
   ```

3. Run the setup wizard (detects hardware, recommends best model):
   ```bash
   hermes-offline-setup
   ```

4. Launch offline Hermes:
   ```bash
   hermes-offline
   ```

## Hardware Tiers & Model Recommendations

| RAM  | VRAM | Recommended Model | Pull Command              | Size   |
|------|------|-------------------|---------------------------|--------|
| 4 GB | 0    | qwen3:1.7b        | `ollama pull qwen3:1.7b`  | 1.1 GB |
| 8 GB | 0    | qwen3:4b          | `ollama pull qwen3:4b`    | 2.6 GB |
| 16 GB| 0–8  | qwen3:8b          | `ollama pull qwen3:8b`    | 5.2 GB |
| 16 GB| 8 GB | qwen2.5-coder:14b | `ollama pull qwen2.5-coder:14b` | 9 GB |
| 32 GB| 16 GB| qwen3-coder:30b   | `ollama pull qwen3-coder:30b` | 19 GB |

Auto-detect the best model for the user's hardware:
```bash
hermes-offline-setup   # interactive, hardware-aware
```

## Key Configuration (~/.hermes/config.yaml)

```yaml
provider: ollama-local
model:
  default: qwen3:8b       # or whichever tier fits

context:
  compression_threshold: 0.70    # local models degrade faster near limit
  max_tool_output_chars: 2000    # truncate large tool results

web:
  backend: duckduckgo            # free, no key required

tts:
  provider: piper                # local, no internet needed

tracker:
  enabled: true
  status_line: true
  summary_on_exit: true
```

## Thinking Mode (Qwen3 only)

Qwen3 models support chain-of-thought reasoning via `/think`:
```bash
hermes-offline --think          # force thinking on every turn
hermes-offline --auto-think     # heuristic-based (default)
hermes-offline --no-think       # fastest, simple tasks
```

## Performance Tuning

Apply a pre-tuned Modelfile for reliable tool calling at low temperature:
```bash
bash ~/.hermes/scripts/apply-modelfile.sh 8b
# Creates model: qwen3-8b-agent with temperature=0.2, optimized ctx
```

## Session Cost Tracker

Every response shows live resource usage:
```
  ↳ turn 4 · 312 tok out · 28.4 tok/s · 2 tools · ctx 3,932/16,384 (24%) · 5.5GB RAM
```

## Semantic Memory (Optional)

Enable local vector memory (nomic-embed-text, 274 MB):
```bash
bash ~/.hermes/scripts/install-embeddings.sh
```

## Status Check

```bash
hermes-offline-status           # Rich table of all component states
hermes-offline-status --json    # machine-readable for automation
```

## /offline Slash Command

When the user types `/offline` or asks to switch to offline mode:
1. Run `hermes-offline-status` to show current state
2. If Ollama is not running, instruct user to start it: `ollama serve`
3. If no model is found, recommend: `ollama pull qwen3:8b`
4. Confirm current provider is `ollama-local` in config
5. Suggest: `hermes-offline` to launch with all patches applied

## Troubleshooting

**Ollama not found:**
- Linux/macOS: `curl -fsSL https://ollama.com/install.sh | sh`
- Windows: Download from https://ollama.com/download

**Model not loaded:**
- `ollama list` — see installed models
- `ollama pull qwen3:8b` — pull recommended model

**Slow inference:**
- Add to ~/.bashrc: `export OLLAMA_FLASH_ATTENTION=1`
- Reduce context: set `num_ctx: 8192` in Modelfile
- Use smaller model: `qwen3:4b` for 8 GB RAM

**Tool calls failing:**
- Lower temperature: use `hermes-offline-modelfile` to regenerate
- Try qwen3 models — best tool-calling at every size tier
- Run benchmark: `hermes-offline-bench --model qwen3:8b`

## Feature Compatibility Matrix

| Feature              | Works Offline? | Notes                          |
|----------------------|----------------|--------------------------------|
| File/terminal tools  | ✅ Always      | No dependencies                |
| Browser tools (12)   | ✅ Always      | Playwright is already local    |
| Memory (FTS5)        | ✅ Always      | SQLite built-in                |
| Memory (vector)      | ✅ Optional    | sqlite-vec + nomic-embed-text  |
| Skills system        | ✅ Always      | Local files                    |
| MCP client/server    | ✅ Always      | Local stdio/HTTP               |
| Web search           | ✅ DDG/Wiki    | No API key needed              |
| Voice TTS            | ✅ piper       | Local, no internet             |
| Transcription        | ✅ whisper     | Local, no internet             |
| Image gen            | ✅ Optional    | ComfyUI or A1111 if running    |
| Self-evolution       | ✅ Lightweight | DSPy + local session history   |
| Messaging gateway    | ✅ User tokens | Telegram/Discord/etc — free    |

*All 71+ built-in Hermes tools are preserved.*
"""


# ── Skill file I/O ────────────────────────────────────────────────────────────

def _default_skills_dir() -> pathlib.Path:
    home = pathlib.Path.home()
    # Check common Hermes skills locations
    candidates = [
        home / ".hermes" / "skills",
        home / ".config" / "hermes" / "skills",
        home / "hermes" / "skills",
    ]
    for c in candidates:
        if c.exists():
            return c
    # Default: create it
    d = home / ".hermes" / "skills"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_skill_file(skills_dir: Optional[pathlib.Path] = None) -> pathlib.Path:
    """Write the skill .md file to the skills directory."""
    d = skills_dir or _default_skills_dir()
    d.mkdir(parents=True, exist_ok=True)
    target = d / "hermes-offline-edition.md"
    target.write_text(SKILL_FILE_CONTENT, encoding="utf-8")
    return target


def write_manifest_file(skills_dir: Optional[pathlib.Path] = None) -> pathlib.Path:
    """Write the skill manifest JSON alongside the .md file."""
    d = skills_dir or _default_skills_dir()
    d.mkdir(parents=True, exist_ok=True)
    target = d / "hermes-offline-edition.manifest.json"
    target.write_text(json.dumps(SKILL_MANIFEST, indent=2), encoding="utf-8")
    return target


def validate_skill_file(path: pathlib.Path) -> tuple[bool, list[str]]:
    """Basic validation of an existing skill file."""
    errors = []
    if not path.exists():
        return False, [f"File not found: {path}"]
    content = path.read_text(encoding="utf-8")
    required_sections = [
        "Quick Setup", "Hardware Tiers", "Key Configuration",
        "Thinking Mode", "Troubleshooting", "Feature Compatibility",
    ]
    for section in required_sections:
        if section not in content:
            errors.append(f"Missing section: {section}")
    if len(content) < 2000:
        errors.append(f"Skill file suspiciously short ({len(content)} chars)")
    return len(errors) == 0, errors


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        prog="hermes-offline-hub-submit",
        description="Generate and install the Hermes Offline skill for HermesHub",
    )
    parser.add_argument("--dry-run",  action="store_true", help="Print skill content, don't write files")
    parser.add_argument("--path",     default=None,        help="Write to custom directory instead of ~/.hermes/skills/")
    parser.add_argument("--validate", action="store_true", help="Validate an existing skill file")
    parser.add_argument("--manifest", action="store_true", help="Also write manifest JSON")
    args = parser.parse_args(argv)

    console = Console() if _RICH else None

    if args.dry_run:
        if console:
            console.print(Panel(
                "[bold]hermes-offline-edition.md[/bold]\n[dim](dry-run — no files written)[/dim]",
                border_style="blue", expand=False,
            ))
            console.print(Syntax(SKILL_FILE_CONTENT, "markdown", theme="monokai", line_numbers=False))
        else:
            print(SKILL_FILE_CONTENT)
        return

    skills_dir = pathlib.Path(args.path) if args.path else None

    if args.validate:
        d = skills_dir or _default_skills_dir()
        skill_path = d / "hermes-offline-edition.md"
        ok, errors = validate_skill_file(skill_path)
        if ok:
            msg = f"[green]✓[/green] Skill file valid: {skill_path}"
        else:
            msg = f"[red]✗[/red] Skill file invalid:\n" + "\n".join(f"  - {e}" for e in errors)
        if console:
            console.print(msg)
        else:
            print(msg)
        sys.exit(0 if ok else 1)

    skill_path = write_skill_file(skills_dir)
    if console:
        console.print(f"[green]✓[/green] Skill file written: [bold]{skill_path}[/bold]")
    else:
        print(f"✓ Skill file written: {skill_path}")

    if args.manifest:
        manifest_path = write_manifest_file(skills_dir)
        if console:
            console.print(f"[green]✓[/green] Manifest written:    [bold]{manifest_path}[/bold]")
        else:
            print(f"✓ Manifest written: {manifest_path}")

    if console:
        console.print(
            "\n[dim]The skill is now registered in your local Hermes skills directory.\n"
            "To submit to HermesHub, visit: https://agentskills.io/submit\n"
            "and paste the contents of the .manifest.json file.[/dim]"
        )
    else:
        print("\nSkill registered locally.")
        print("To submit to HermesHub, visit: https://agentskills.io/submit")


if __name__ == "__main__":
    main()
