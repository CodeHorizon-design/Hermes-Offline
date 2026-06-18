"""
System dependency detector for hermes-offline.

Scans the host once per process and caches the result. Every patch function
and setup helper uses this instead of probing independently, so startup is
fast and we never install something that's already there.

What it detects
───────────────
  Python packages   importlib.metadata — version strings, no subprocess
  System binaries   shutil.which — piper, ffmpeg, whisper, docker, ollama, …
  Running services  HTTP probes — Ollama, SearXNG, ComfyUI, A1111, LM Studio
  Ollama models     GET /api/tags — which models are already pulled
  System pkg mgr    apt/brew/pacman/winget presence for install hint messages

Usage
─────
    from hermes_offline.detector import get_snapshot, SystemSnapshot
    snap = get_snapshot()          # cached after first call
    if snap.has_tts:
        wire_piper()
    if snap.has_searxng:
        register_searxng()

    # Force a fresh scan (e.g. after hermes-offline update)
    snap = get_snapshot(refresh=True)
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from importlib.metadata import version as _pkg_ver, PackageNotFoundError
from typing import Optional

logger = logging.getLogger(__name__)

OLLAMA_BASE = os.environ.get(
    "OLLAMA_LOCAL_BASE_URL", "http://127.0.0.1:11434"
).rstrip("/v1").rstrip("/")


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class ServiceInfo:
    name: str
    url: str
    running: bool
    version: str = ""


@dataclass
class SystemSnapshot:
    """Complete picture of what's already installed on this machine."""

    # ── Python packages ──────────────────────────────────────────────────
    python_packages: dict[str, str] = field(default_factory=dict)
    # e.g. {"faster-whisper": "1.0.3", "piper-tts": "1.2.0", "sqlite-vec": "0.1.1"}

    # ── System binaries ──────────────────────────────────────────────────
    binaries: dict[str, str] = field(default_factory=dict)
    # e.g. {"piper": "/usr/bin/piper", "ffmpeg": "/usr/bin/ffmpeg", "docker": "/usr/bin/docker"}

    # ── Running local services ───────────────────────────────────────────
    services: dict[str, ServiceInfo] = field(default_factory=dict)
    # keys: "ollama", "searxng", "comfyui", "a1111", "lmstudio"

    # ── Ollama models already pulled ─────────────────────────────────────
    ollama_models: list[str] = field(default_factory=list)
    # e.g. ["qwen3:8b", "nomic-embed-text:latest"]

    # ── System package manager ───────────────────────────────────────────
    pkg_manager: str = ""
    # "apt" | "brew" | "pacman" | "dnf" | "winget" | ""

    # ── Computed readiness flags ─────────────────────────────────────────
    # (set by _compute_flags() after detection)
    has_ollama: bool = False
    has_tts: bool = False           # piper binary or piper-tts Python pkg
    has_whisper: bool = False       # faster-whisper or whisper.cpp binary
    has_image_gen: bool = False     # ComfyUI or A1111 running
    has_searxng: bool = False       # SearXNG running locally
    has_embeddings: bool = False    # nomic-embed-text model pulled
    has_docker: bool = False        # Docker daemon accessible
    has_ffmpeg: bool = False        # ffmpeg binary present
    has_sqlite_vec: bool = False    # sqlite-vec Python package

    # ── Install suggestions (filled during detection) ────────────────────
    missing_required: list[str] = field(default_factory=list)
    missing_optional: list[str] = field(default_factory=list)

    def service(self, name: str) -> Optional[ServiceInfo]:
        return self.services.get(name)

    def model_present(self, name: str) -> bool:
        """Check if a model (or a prefix match) is already pulled."""
        base = name.split(":")[0].lower()
        return any(m.lower().startswith(base) for m in self.ollama_models)

    def pkg_version(self, name: str) -> Optional[str]:
        return self.python_packages.get(name)

    def binary(self, name: str) -> Optional[str]:
        return self.binaries.get(name)

    def install_hint(self, pkg: str) -> str:
        """Return a distro-appropriate install hint for a system package."""
        hints = {
            "apt":    f"sudo apt install {pkg}",
            "brew":   f"brew install {pkg}",
            "pacman": f"sudo pacman -S {pkg}",
            "dnf":    f"sudo dnf install {pkg}",
            "winget": f"winget install {pkg}",
        }
        return hints.get(self.pkg_manager, f"install {pkg}")

    def summary_lines(self) -> list[str]:
        """Human-readable one-liner per component."""
        lines = []

        def _row(label: str, ok: bool, detail: str = "") -> str:
            icon = "✓" if ok else "✗"
            return f"  {icon}  {label:<24} {detail}"

        ollama_ver = self.services.get("ollama", ServiceInfo("", "", False)).version
        lines.append(_row("Ollama",          self.has_ollama,      ollama_ver))
        lines.append(_row("Piper TTS",       self.has_tts,         self.binaries.get("piper", self.python_packages.get("piper-tts", ""))))
        lines.append(_row("Whisper",         self.has_whisper,     self.python_packages.get("faster-whisper", self.binaries.get("whisper", ""))))
        lines.append(_row("SearXNG",         self.has_searxng,     self.services["searxng"].url if self.has_searxng else ""))
        lines.append(_row("Image gen",       self.has_image_gen,   "ComfyUI" if self.services.get("comfyui", ServiceInfo("","",False)).running else ("A1111" if self.services.get("a1111", ServiceInfo("","",False)).running else "")))
        lines.append(_row("nomic-embed-text",self.has_embeddings,  "pulled" if self.has_embeddings else "ollama pull nomic-embed-text"))
        lines.append(_row("Docker",          self.has_docker,      self.binaries.get("docker", "")))
        lines.append(_row("ffmpeg",          self.has_ffmpeg,      self.binaries.get("ffmpeg", "")))
        lines.append(_row("sqlite-vec",      self.has_sqlite_vec,  self.python_packages.get("sqlite-vec", "")))

        if self.missing_required:
            lines.append("")
            lines.append("  Required but missing:")
            for m in self.missing_required:
                lines.append(f"    • {m}")
        if self.missing_optional:
            lines.append("")
            lines.append("  Optional (not installed):")
            for m in self.missing_optional:
                lines.append(f"    • {m}")
        return lines


# ── Detection functions ───────────────────────────────────────────────────────

_PYTHON_PACKAGES_TO_CHECK = [
    "hermes-agent",
    "hermes-offline",
    "faster-whisper",
    "piper-tts",
    "sqlite-vec",
    "dspy-ai",
    "psutil",
    "pyyaml",
    "rich",
    "textual",
]

_BINARIES_TO_CHECK = [
    "ollama",
    "piper",
    "ffmpeg",
    "docker",
    "docker-compose",
    "whisper",          # whisper.cpp CLI
    "whisper-cpp",
    "nvidia-smi",
    "rocm-smi",
    "uv",
    "pip",
]

_SERVICES_TO_PROBE = [
    ("ollama",    f"{OLLAMA_BASE}/api/tags",           "Ollama"),
    ("searxng",   "http://localhost:8080/search?q=test&format=json", "SearXNG"),
    ("comfyui",   "http://localhost:8188/system_stats", "ComfyUI"),
    ("a1111",     "http://localhost:7860/sdapi/v1/sd-models", "A1111"),
    ("lmstudio",  "http://localhost:1234/v1/models",    "LM Studio"),
]

_PKG_MANAGERS = [
    ("apt",    "apt"),
    ("brew",   "brew"),
    ("pacman", "pacman"),
    ("dnf",    "dnf"),
    ("winget", "winget"),
]


def _detect_python_packages() -> dict[str, str]:
    found: dict[str, str] = {}
    for pkg in _PYTHON_PACKAGES_TO_CHECK:
        try:
            found[pkg] = _pkg_ver(pkg)
        except PackageNotFoundError:
            pass
    return found


def _detect_binaries() -> dict[str, str]:
    found: dict[str, str] = {}
    for name in _BINARIES_TO_CHECK:
        path = shutil.which(name)
        if path:
            found[name] = path
    return found


def _probe_service(key: str, url: str, name: str, timeout: float = 2.0) -> ServiceInfo:
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "hermes-offline/detector"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(512).decode("utf-8", errors="replace")
            version = ""
            # Try to extract version from Ollama response
            if key == "ollama":
                try:
                    data = json.loads(body + resp.read().decode("utf-8", errors="replace"))
                except Exception:
                    data = {}
                version = data.get("version", "")
            return ServiceInfo(name=name, url=url.split("/api")[0].split("/search")[0].split("/system")[0].split("/sdapi")[0].split("/v1")[0], running=True, version=version)
    except Exception:
        return ServiceInfo(name=name, url="", running=False)


def _detect_services() -> dict[str, ServiceInfo]:
    services: dict[str, ServiceInfo] = {}
    for key, url, name in _SERVICES_TO_PROBE:
        services[key] = _probe_service(key, url, name)
    return services


def _detect_ollama_models(services: dict[str, ServiceInfo]) -> list[str]:
    if not services.get("ollama", ServiceInfo("","",False)).running:
        return []
    try:
        with urllib.request.urlopen(f"{OLLAMA_BASE}/api/tags", timeout=3) as resp:
            data = json.loads(resp.read())
            return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []


def _detect_pkg_manager() -> str:
    for name, cmd in _PKG_MANAGERS:
        if shutil.which(cmd):
            return name
    return ""


def _compute_flags(snap: SystemSnapshot) -> None:
    """Fill computed boolean flags from raw detection results."""
    svc = snap.services

    snap.has_ollama   = svc.get("ollama", ServiceInfo("","",False)).running
    snap.has_tts      = bool(snap.binaries.get("piper") or snap.python_packages.get("piper-tts"))
    snap.has_whisper  = bool(
        snap.python_packages.get("faster-whisper") or
        snap.binaries.get("whisper") or
        snap.binaries.get("whisper-cpp")
    )
    snap.has_image_gen = (
        svc.get("comfyui", ServiceInfo("","",False)).running or
        svc.get("a1111",   ServiceInfo("","",False)).running
    )
    snap.has_searxng  = svc.get("searxng", ServiceInfo("","",False)).running
    snap.has_docker   = bool(snap.binaries.get("docker"))
    snap.has_ffmpeg   = bool(snap.binaries.get("ffmpeg"))
    snap.has_sqlite_vec = bool(snap.python_packages.get("sqlite-vec"))

    # Embedding model check
    snap.has_embeddings = snap.model_present("nomic-embed-text")

    # Missing required
    if not snap.has_ollama:
        snap.missing_required.append(
            "Ollama not running — start with: ollama serve"
        )
    if not snap.ollama_models and snap.has_ollama:
        snap.missing_required.append(
            "No models pulled — run: ollama pull qwen3:8b"
        )

    # Missing optional
    if not snap.has_tts:
        snap.missing_optional.append("piper-tts (voice output)")
    if not snap.has_whisper:
        snap.missing_optional.append("faster-whisper (voice input)")
    if not snap.has_searxng:
        snap.missing_optional.append("SearXNG (self-hosted private search)")
    if not snap.has_embeddings:
        snap.missing_optional.append("nomic-embed-text (semantic memory)")
    if not snap.has_sqlite_vec:
        snap.missing_optional.append("sqlite-vec (vector similarity search)")


# ── Session cache ─────────────────────────────────────────────────────────────

_cache: Optional[SystemSnapshot] = None


def get_snapshot(refresh: bool = False) -> SystemSnapshot:
    """
    Return cached system snapshot, scanning once per process.
    Pass refresh=True to re-scan (e.g. after an upgrade).
    """
    global _cache
    if _cache is not None and not refresh:
        return _cache

    snap = SystemSnapshot()
    snap.python_packages = _detect_python_packages()
    snap.binaries        = _detect_binaries()
    snap.services        = _detect_services()
    snap.ollama_models   = _detect_ollama_models(snap.services)
    snap.pkg_manager     = _detect_pkg_manager()
    _compute_flags(snap)

    _cache = snap
    logger.debug(
        "SystemSnapshot: ollama=%s tts=%s whisper=%s searxng=%s imagegen=%s embeds=%s",
        snap.has_ollama, snap.has_tts, snap.has_whisper,
        snap.has_searxng, snap.has_image_gen, snap.has_embeddings,
    )
    return snap


def _cli_status() -> None:
    """Entry point for `hermes-offline-status` — print system snapshot and exit."""
    import sys
    import argparse

    parser = argparse.ArgumentParser(
        prog="hermes-offline-status",
        description="Show what hermes-offline components are installed and running",
    )
    parser.add_argument("--refresh", action="store_true", help="Re-scan (don't use cache)")
    parser.add_argument("--json",    action="store_true", help="Output as JSON")
    args = parser.parse_args()

    snap = get_snapshot(refresh=args.refresh)

    if args.json:
        import dataclasses
        def _ser(obj):
            if dataclasses.is_dataclass(obj):
                return dataclasses.asdict(obj)
            return str(obj)
        import json as _json
        print(_json.dumps(dataclasses.asdict(snap), default=_ser, indent=2))
        sys.exit(0)

    print_snapshot(snap)

    # Exit 1 if required components missing
    if snap.missing_required:
        sys.exit(1)
    sys.exit(0)


def print_snapshot(snap: Optional[SystemSnapshot] = None) -> None:
    """Print a human-readable system status table using Rich."""
    if snap is None:
        snap = get_snapshot()
    try:
        from rich.console import Console
        from rich.table import Table
        from rich.panel import Panel

        con = Console()
        t = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
        t.add_column("")
        t.add_column("Component")
        t.add_column("Status")
        t.add_column("Detail", style="dim")

        def row(label, ok, detail=""):
            icon   = "[green]✓[/green]" if ok else "[dim]–[/dim]"
            status = "[green]Ready[/green]" if ok else "[dim]Not installed[/dim]"
            t.add_row(icon, label, status, detail or "")

        ollama_detail = snap.services["ollama"].version if snap.has_ollama else "ollama serve"
        row("Ollama",           snap.has_ollama,      ollama_detail)

        models_str = ", ".join(snap.ollama_models[:3]) + ("…" if len(snap.ollama_models) > 3 else "")
        row("Local models",     bool(snap.ollama_models), models_str or "ollama pull qwen3:8b")

        piper_detail = snap.binaries.get("piper") or snap.python_packages.get("piper-tts","")
        row("Piper TTS",        snap.has_tts,         piper_detail)

        whisper_detail = snap.python_packages.get("faster-whisper", snap.binaries.get("whisper",""))
        row("Whisper",          snap.has_whisper,     whisper_detail)

        sx_url = snap.services["searxng"].url if snap.has_searxng else ""
        row("SearXNG",          snap.has_searxng,     sx_url or "localhost:8080")

        img_detail = ("ComfyUI" if snap.services.get("comfyui",ServiceInfo("","",False)).running
                      else "A1111" if snap.services.get("a1111",ServiceInfo("","",False)).running else "")
        row("Image generation", snap.has_image_gen,   img_detail)

        row("nomic-embed-text", snap.has_embeddings,  "274 MB" if snap.has_embeddings else "ollama pull nomic-embed-text")
        row("sqlite-vec",       snap.has_sqlite_vec,  snap.python_packages.get("sqlite-vec",""))
        row("Docker",           snap.has_docker,      snap.binaries.get("docker",""))
        row("ffmpeg",           snap.has_ffmpeg,      snap.binaries.get("ffmpeg",""))

        con.print(Panel(t, title="[bold]hermes-offline — system status[/bold]", border_style="blue", expand=False))

        if snap.missing_required:
            con.print("[red]Required:[/red]")
            for m in snap.missing_required:
                con.print(f"  [red]•[/red] {m}")
        if snap.missing_optional:
            con.print("[dim]Optional (install to unlock features):[/dim]")
            for m in snap.missing_optional:
                con.print(f"  [dim]•[/dim] {m}")

    except ImportError:
        # Fallback: plain text
        print("hermes-offline — system status")
        for line in snap.summary_lines():
            print(line)
