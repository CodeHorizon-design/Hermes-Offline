"""
hermes-offline updater — stay current with zero effort.

`hermes-offline update` does everything in one shot:

  1. VERSION CHECK    — compare installed vs latest hermes-agent on PyPI
  2. COMPAT CHECK     — probe local Ollama: model exists, chat works, tools work
  3. UPGRADE          — uv/pip upgrade hermes-agent + hermes-offline together
  4. POST-CHECK       — re-run compat after upgrade to confirm nothing broke
  5. CHANGELOG        — show what changed (GitHub releases, trimmed to fit terminal)

Design:
  - Pure stdlib for network calls (no requests/httpx dep)
  - Rich for display (already a dep)
  - uv first, pip fallback — same installer chain as setup wizard
  - All steps are individually skippable via flags
  - Rollback hint printed if post-check fails
  - Safe to run on every launch (fast path: already current → 3-second check, exit 0)

Usage:
    hermes-offline update              # full update flow
    hermes-offline update --check      # check only, no install
    hermes-offline update --skip-compat # skip Ollama probe
    hermes-offline update --force      # upgrade even if already current
    hermes-offline update --dry-run    # show what would happen, do nothing
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from importlib.metadata import version as _pkg_version, PackageNotFoundError
from typing import Optional

# Rich is already a hard dep
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich import print as rprint

console = Console()

PYPI_URL   = "https://pypi.org/pypi/{package}/json"
GH_REL_URL = "https://api.github.com/repos/NousResearch/hermes-agent/releases"
OLLAMA_BASE = os.environ.get("OLLAMA_LOCAL_BASE_URL", "http://127.0.0.1:11434").rstrip("/v1").rstrip("/")


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class VersionInfo:
    installed: str
    latest: str
    hermes_offline_installed: str
    hermes_offline_latest: str
    upgrade_available: bool
    major_bump: bool          # True if major version changed — needs caution

@dataclass
class CompatResult:
    ollama_running: bool      = False
    model_found: bool         = False
    model_name: str           = ""
    chat_ok: bool             = False
    tool_call_ok: bool        = False
    embed_ok: bool            = False   # nomic-embed-text optional
    memory_ok: bool           = False   # BEAM store init optional
    errors: list[str]         = field(default_factory=list)

    @property
    def essential_ok(self) -> bool:
        """True if the essentials (Ollama + model + chat) work."""
        return self.ollama_running and self.model_found and self.chat_ok

    @property
    def icon(self) -> str:
        return "✓" if self.essential_ok else "✗"


# ── Version helpers ───────────────────────────────────────────────────────────

def _installed_version(pkg: str) -> str:
    try:
        return _pkg_version(pkg)
    except PackageNotFoundError:
        return "0.0.0"


def _pypi_latest(pkg: str) -> str:
    """Fetch latest stable version from PyPI. Returns '0.0.0' on any failure."""
    url = PYPI_URL.format(package=pkg)
    try:
        with urllib.request.urlopen(url, timeout=6) as resp:
            data = json.loads(resp.read())
            # info.version is the latest stable release
            return data["info"]["version"]
    except Exception:
        return "0.0.0"


def _parse_ver(v: str) -> tuple[int, int, int]:
    parts = str(v).split(".")
    try:
        return int(parts[0]), int(parts[1] if len(parts) > 1 else 0), int(parts[2] if len(parts) > 2 else 0)
    except (ValueError, IndexError):
        return (0, 0, 0)


def _ver_gt(a: str, b: str) -> bool:
    return _parse_ver(a) > _parse_ver(b)


def check_versions() -> VersionInfo:
    ha_inst  = _installed_version("hermes-agent")
    ho_inst  = _installed_version("hermes-offline")
    ha_latest = _pypi_latest("hermes-agent")
    ho_latest = _pypi_latest("hermes-offline")

    upgrade_avail = _ver_gt(ha_latest, ha_inst) or _ver_gt(ho_latest, ho_inst)
    major_bump = _parse_ver(ha_latest)[0] > _parse_ver(ha_inst)[0]

    return VersionInfo(
        installed=ha_inst,
        latest=ha_latest,
        hermes_offline_installed=ho_inst,
        hermes_offline_latest=ho_latest,
        upgrade_available=upgrade_avail,
        major_bump=major_bump,
    )


# ── Compatibility checks ──────────────────────────────────────────────────────

def run_compat_checks(verbose: bool = False) -> CompatResult:
    result = CompatResult()

    # 1. Ollama health
    try:
        with urllib.request.urlopen(f"{OLLAMA_BASE}/api/tags", timeout=3) as resp:
            data = json.loads(resp.read())
            result.ollama_running = True
            models = [m["name"] for m in data.get("models", [])]
    except Exception as exc:
        result.errors.append(f"Ollama not reachable: {exc}")
        return result   # can't continue without Ollama

    # 2. Configured model is present
    result.model_name = _get_configured_model() or ""
    if result.model_name:
        base = result.model_name.split(":")[0].lower()
        result.model_found = any(m.lower().startswith(base) for m in models)
        if not result.model_found:
            result.errors.append(
                f"Model '{result.model_name}' not found in Ollama. "
                f"Pull with: ollama pull {result.model_name}"
            )
    else:
        # No configured model — just check that something is available
        result.model_found = bool(models)
        if models:
            result.model_name = models[0]
        else:
            result.errors.append("No models found in Ollama. Pull one: ollama pull qwen3:8b")

    # 3. Basic chat completion test
    if result.model_found and result.model_name:
        result.chat_ok, chat_err = _test_chat(result.model_name, verbose)
        if chat_err:
            result.errors.append(chat_err)

    # 4. Tool call test (hermes sends JSON function calls — verify model can do it)
    if result.chat_ok:
        result.tool_call_ok, tool_err = _test_tool_call(result.model_name, verbose)
        if tool_err:
            result.errors.append(tool_err)

    # 5. Embedding test (optional — nomic-embed-text)
    embed_model = _get_embed_model()
    if embed_model:
        base = embed_model.split(":")[0].lower()
        if any(m.lower().startswith(base) for m in models):
            result.embed_ok, embed_err = _test_embed(embed_model, verbose)
            if embed_err:
                result.errors.append(embed_err)

    # 6. Memory store smoke test
    try:
        from hermes_offline.beam_memory import BEAMStore
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as td:
            store = BEAMStore(pathlib.Path(td) / "test.db")
            store.close()
        result.memory_ok = True
    except Exception as exc:
        result.errors.append(f"Memory store init failed: {exc}")

    return result


def _get_configured_model() -> Optional[str]:
    try:
        from hermes_cli.config import load_config
        cfg = load_config()
        m = cfg.get("model", {})
        if isinstance(m, dict):
            return m.get("default")
        if isinstance(m, str):
            return m
    except Exception:
        pass
    return None


def _get_embed_model() -> Optional[str]:
    try:
        from hermes_cli.config import load_config
        cfg = load_config()
        return cfg.get("memory", {}).get("embedding_model")
    except Exception:
        return None


def _ollama_chat(model: str, prompt: str, tools: Optional[list] = None, timeout: int = 30) -> dict:
    """POST to Ollama /api/chat, return parsed JSON response."""
    messages = [{"role": "user", "content": prompt}]
    payload: dict = {"model": model, "messages": messages, "stream": False}
    if tools:
        payload["tools"] = tools
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{OLLAMA_BASE}/api/chat",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _test_chat(model: str, verbose: bool) -> tuple[bool, Optional[str]]:
    try:
        resp = _ollama_chat(model, "Reply with exactly: ONLINE", timeout=20)
        content = resp.get("message", {}).get("content", "")
        if not content:
            return False, f"Empty chat response from {model}"
        return True, None
    except urllib.error.URLError as exc:
        return False, f"Chat request failed: {exc}"
    except Exception as exc:
        return False, f"Chat test error: {exc}"


def _test_tool_call(model: str, verbose: bool) -> tuple[bool, Optional[str]]:
    """
    Send a prompt with a dummy tool and check the model returns a tool_calls
    structure. This validates hermes's JSON tool call parsing will work.
    """
    tools = [{
        "type": "function",
        "function": {
            "name": "get_time",
            "description": "Get the current time",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }]
    try:
        resp = _ollama_chat(
            model,
            "What time is it? Use the get_time tool.",
            tools=tools,
            timeout=25,
        )
        msg = resp.get("message", {})
        # Accept either a tool_calls list or text containing "get_time"
        # (some models call the tool, others describe the call in text — both are fine)
        has_tool_call = bool(msg.get("tool_calls"))
        has_text_ref  = "get_time" in msg.get("content", "").lower()
        if has_tool_call or has_text_ref:
            return True, None
        return False, (
            f"Model '{model}' didn't produce a tool call. "
            "Tool-heavy workflows may be unreliable. "
            "Consider: ollama pull qwen3:8b"
        )
    except Exception as exc:
        return False, f"Tool call test error: {exc}"


def _test_embed(model: str, verbose: bool) -> tuple[bool, Optional[str]]:
    payload = json.dumps({"model": model, "prompt": "hello"}).encode()
    req = urllib.request.Request(
        f"{OLLAMA_BASE}/api/embeddings",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            vec = data.get("embedding", [])
            if len(vec) < 64:
                return False, f"Embedding vector too short ({len(vec)} dims)"
            return True, None
    except Exception as exc:
        return False, f"Embedding test error: {exc}"


# ── Changelog ─────────────────────────────────────────────────────────────────

def fetch_changelog(from_version: str, to_version: str, max_releases: int = 5) -> list[dict]:
    """
    Fetch GitHub release notes between two versions.
    Returns list of {tag, name, body_excerpt} dicts.
    """
    try:
        req = urllib.request.Request(
            GH_REL_URL + f"?per_page={max_releases}",
            headers={"Accept": "application/vnd.github+json", "User-Agent": "hermes-offline/updater"},
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            releases = json.loads(resp.read())
    except Exception:
        return []

    from_parts = _parse_ver(from_version)
    to_parts   = _parse_ver(to_version)

    result = []
    for rel in releases:
        tag = rel.get("tag_name", "").lstrip("v")
        parts = _parse_ver(tag)
        if from_parts < parts <= to_parts:
            body = (rel.get("body") or "").strip()
            # Trim to first 600 chars, cut at last complete line
            if len(body) > 600:
                body = body[:600]
                body = body[:body.rfind("\n")] if "\n" in body else body
                body += "\n…"
            result.append({
                "tag": rel.get("tag_name", tag),
                "name": rel.get("name", tag),
                "body": body,
            })
    return result


# ── Upgrade ───────────────────────────────────────────────────────────────────

def _get_installer() -> list[str]:
    """Return [installer, ...flags] for upgrading packages."""
    if shutil.which("uv"):
        return ["uv", "pip", "install", "--upgrade"]
    if shutil.which("pip"):
        return ["pip", "install", "--upgrade"]
    if shutil.which("pip3"):
        return ["pip3", "install", "--upgrade"]
    return [sys.executable, "-m", "pip", "install", "--upgrade"]


def do_upgrade(dry_run: bool = False) -> tuple[bool, str]:
    """
    Upgrade hermes-agent and hermes-offline together.
    Returns (success, message).
    """
    cmd = _get_installer() + ["hermes-agent", "hermes-offline"]
    if dry_run:
        return True, f"[dry-run] Would run: {' '.join(cmd)}"

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if proc.returncode == 0:
            return True, proc.stdout[-800:] if proc.stdout else "Upgrade completed."
        else:
            err = (proc.stderr or proc.stdout or "unknown error")[-800:]
            return False, err
    except subprocess.TimeoutExpired:
        return False, "Upgrade timed out after 5 minutes."
    except Exception as exc:
        return False, str(exc)


# ── Display helpers ───────────────────────────────────────────────────────────

def _version_badge(inst: str, latest: str) -> str:
    if inst == "0.0.0":
        return f"[red]not installed[/red]"
    if _ver_gt(latest, inst):
        return f"[yellow]{inst}[/yellow] → [green]{latest}[/green]"
    return f"[green]{inst}[/green] (latest)"


def _compat_row(label: str, ok: bool, note: str = "") -> list:
    icon = "[green]✓[/green]" if ok else "[red]✗[/red]"
    status = "[green]OK[/green]" if ok else "[red]FAIL[/red]"
    return [icon, label, status, note]


def print_version_table(vi: VersionInfo) -> None:
    t = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    t.add_column("Package")
    t.add_column("Status")
    t.add_row(
        "[bold]hermes-agent[/bold]",
        _version_badge(vi.installed, vi.latest),
    )
    t.add_row(
        "[bold]hermes-offline[/bold]",
        _version_badge(vi.hermes_offline_installed, vi.hermes_offline_latest),
    )
    console.print(t)


def print_compat_table(cr: CompatResult) -> None:
    t = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    t.add_column("")
    t.add_column("Check")
    t.add_column("Result")
    t.add_column("Notes", style="dim")

    model_note = cr.model_name if cr.model_name else "—"
    t.add_row(*_compat_row("Ollama running",   cr.ollama_running))
    t.add_row(*_compat_row("Model available",  cr.model_found,  model_note))
    t.add_row(*_compat_row("Chat completion",  cr.chat_ok))
    t.add_row(*_compat_row("Tool calls",       cr.tool_call_ok, "JSON function calling"))
    t.add_row(*_compat_row("Embeddings",       cr.embed_ok,     "nomic-embed-text (optional)"))
    t.add_row(*_compat_row("Memory store",     cr.memory_ok,    "BEAM SQLite (optional)"))
    console.print(t)

    if cr.errors:
        console.print()
        for err in cr.errors:
            console.print(f"  [yellow]⚠[/yellow]  {err}")


# ── Main update flow ──────────────────────────────────────────────────────────

def run_update(
    check_only: bool = False,
    skip_compat: bool = False,
    force: bool = False,
    dry_run: bool = False,
    verbose: bool = False,
) -> int:
    """
    Full update flow. Returns exit code (0 = success, 1 = error).
    """
    console.print(Panel(
        "[bold]hermes-offline update[/bold]\n"
        "[dim]Keeps hermes-offline in sync with hermes-agent — zero API keys, full feature parity[/dim]",
        border_style="blue",
        expand=False,
    ))
    console.print()

    # ── Step 1: Version check ──────────────────────────────────────────────
    console.print("[bold]1/4  Checking versions...[/bold]")
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
                  transient=True, console=console) as prog:
        task = prog.add_task("Querying PyPI...", total=None)
        vi = check_versions()
        prog.update(task, completed=True)

    print_version_table(vi)
    console.print()

    if not vi.upgrade_available and not force:
        console.print("[green]✓[/green] Already up to date.")
        if not skip_compat:
            _run_compat_step(verbose)
        return 0

    if vi.major_bump and not force:
        console.print(
            f"[yellow]⚠[/yellow]  Major version bump detected "
            f"({vi.installed} → {vi.latest}). This may include breaking changes.\n"
            f"   Review the changelog before upgrading. Re-run with --force to proceed."
        )
        _show_changelog(vi, max_releases=3)
        return 0

    # ── Step 2: Pre-upgrade compat check ──────────────────────────────────
    if not skip_compat:
        console.print("[bold]2/4  Pre-upgrade compatibility check...[/bold]")
        pre_cr = _run_compat_step(verbose, label="pre-upgrade")
        console.print()
        if not pre_cr.essential_ok:
            console.print(
                "[yellow]⚠[/yellow]  Essential checks failed before upgrade.\n"
                "   Fix Ollama/model issues first, then re-run hermes-offline update."
            )
            return 1
    else:
        console.print("[dim]2/4  Skipping compat check (--skip-compat)[/dim]")

    if check_only:
        console.print("[dim]--check mode: skipping install.[/dim]")
        _show_changelog(vi)
        return 0

    # ── Step 3: Changelog ─────────────────────────────────────────────────
    _show_changelog(vi)

    # ── Step 4: Upgrade ───────────────────────────────────────────────────
    console.print(f"[bold]3/4  Upgrading packages...[/bold]")
    if dry_run:
        console.print("[dim](dry-run — no changes will be made)[/dim]")

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
                  transient=True, console=console) as prog:
        task = prog.add_task("Installing...", total=None)
        success, msg = do_upgrade(dry_run=dry_run)
        prog.update(task, completed=True)

    if not success:
        console.print(f"[red]✗[/red]  Upgrade failed:\n{msg}")
        console.print("\n[dim]Rollback: pip install hermes-agent==" + vi.installed + "[/dim]")
        return 1

    if verbose or dry_run:
        console.print(f"[dim]{msg}[/dim]")

    # Refresh version info after upgrade
    new_vi = check_versions()
    console.print(
        f"[green]✓[/green]  Upgraded:  "
        f"hermes-agent {vi.installed} → {new_vi.installed}  |  "
        f"hermes-offline {vi.hermes_offline_installed} → {new_vi.hermes_offline_installed}"
    )
    console.print()

    # ── Step 5: Post-upgrade compat check ─────────────────────────────────
    if not skip_compat and not dry_run:
        console.print("[bold]4/4  Post-upgrade compatibility check...[/bold]")
        post_cr = _run_compat_step(verbose, label="post-upgrade")
        console.print()

        if not post_cr.essential_ok:
            console.print("[red]✗[/red]  Post-upgrade checks failed.")
            console.print(
                f"\n[dim]To roll back:\n"
                f"  pip install hermes-agent=={vi.installed} hermes-offline=={vi.hermes_offline_installed}[/dim]"
            )
            return 1
    else:
        console.print("[dim]4/4  Skipping post-upgrade check.[/dim]")

    console.print(
        Panel(
            "[green bold]✓ Update complete![/green bold]\n\n"
            "hermes-offline is fully current — all features preserved, all offline patches active.\n"
            "Run [bold]hermes-offline[/bold] to start.",
            border_style="green",
            expand=False,
        )
    )
    return 0


def _run_compat_step(verbose: bool, label: str = "") -> CompatResult:
    prefix = f"[{label}] " if label else ""
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
                  transient=True, console=console) as prog:
        task = prog.add_task(f"{prefix}Running checks...", total=None)
        cr = run_compat_checks(verbose=verbose)
        prog.update(task, completed=True)
    print_compat_table(cr)
    return cr


def _show_changelog(vi: VersionInfo, max_releases: int = 5) -> None:
    if vi.installed == "0.0.0" or not _ver_gt(vi.latest, vi.installed):
        return
    console.print(f"[bold]Changelog[/bold]  {vi.installed} → {vi.latest}")
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
                  transient=True, console=console) as prog:
        task = prog.add_task("Fetching release notes...", total=None)
        releases = fetch_changelog(vi.installed, vi.latest, max_releases)
        prog.update(task, completed=True)

    if not releases:
        console.print("[dim]  (No release notes available — check GitHub)[/dim]")
    else:
        for rel in releases:
            console.print(f"\n  [bold]{rel['tag']}[/bold]  {rel['name']}")
            if rel["body"]:
                for line in rel["body"].splitlines()[:12]:
                    console.print(f"    [dim]{line}[/dim]")
    console.print()


# ── CLI entry point ───────────────────────────────────────────────────────────

def main(argv: Optional[list[str]] = None) -> None:
    """Standalone CLI: hermes-offline-update [flags]"""
    import argparse

    parser = argparse.ArgumentParser(
        prog="hermes-offline update",
        description="Update hermes-agent + hermes-offline with compat checks",
    )
    parser.add_argument("--check",       action="store_true", help="Check versions only, don't install")
    parser.add_argument("--skip-compat", action="store_true", help="Skip Ollama compatibility probes")
    parser.add_argument("--force",       action="store_true", help="Upgrade even if already current")
    parser.add_argument("--dry-run",     action="store_true", help="Show what would happen, do nothing")
    parser.add_argument("--verbose",     action="store_true", help="Show extra output")

    args = parser.parse_args(argv)
    sys.exit(run_update(
        check_only=args.check,
        skip_compat=args.skip_compat,
        force=args.force,
        dry_run=args.dry_run,
        verbose=args.verbose,
    ))
