"""
SearXNG integration for hermes-offline.

SearXNG is a free, self-hosted metasearch engine — combines Google, Bing,
DuckDuckGo, Wikipedia and 70+ sources with no API key, no tracking, no cost.

This module handles the full lifecycle:
  1. detect()   — check if SearXNG is already running (via detector snapshot)
  2. install()  — set it up if not (Docker preferred, pip fallback)
  3. start()    — start the container/process if stopped
  4. register() — wire it as hermes's default web search backend

Install strategy (in order, using what's already on the machine):
  A. Already running at localhost:8080     → skip install entirely ✓
  B. Docker installed + image exists       → docker start (no pull needed) ✓
  C. Docker installed, no image            → docker pull + run (~200 MB)
  D. No Docker, pip searxng available      → pip install + run as process
  E. Nothing available                     → advise user, use DDG fallback

The detector auto-detects (A) and (B)/(C) so we never ask Docker to pull
an image it already has cached.

Config:
  searxng:
    url: http://localhost:8080      # default
    enabled: true
    auto_start: true                # start container on hermes-offline launch
    docker_image: searxng/searxng   # default Docker image
    docker_port: 8080
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

SEARXNG_DEFAULT_URL  = os.environ.get("SEARXNG_URL", "http://localhost:8080")
SEARXNG_DOCKER_IMAGE = "searxng/searxng"
SEARXNG_CONTAINER    = "hermes-searxng"
SEARXNG_PORT         = int(os.environ.get("SEARXNG_PORT", "8080"))


# ── Status detection ──────────────────────────────────────────────────────────

def is_running(url: str = SEARXNG_DEFAULT_URL, timeout: float = 2.0) -> bool:
    """Return True if SearXNG is responding at url."""
    probe = f"{url}/search?q=test&format=json"
    try:
        with urllib.request.urlopen(probe, timeout=timeout):
            return True
    except Exception:
        return False


def _docker_image_exists() -> bool:
    """Return True if the SearXNG Docker image is already cached locally."""
    if not shutil.which("docker"):
        return False
    try:
        out = subprocess.check_output(
            ["docker", "images", "-q", SEARXNG_DOCKER_IMAGE],
            stderr=subprocess.DEVNULL, timeout=5,
        ).decode().strip()
        return bool(out)
    except Exception:
        return False


def _docker_container_exists() -> bool:
    """Return True if a hermes-searxng container exists (running or stopped)."""
    if not shutil.which("docker"):
        return False
    try:
        out = subprocess.check_output(
            ["docker", "ps", "-a", "-q", "--filter", f"name={SEARXNG_CONTAINER}"],
            stderr=subprocess.DEVNULL, timeout=5,
        ).decode().strip()
        return bool(out)
    except Exception:
        return False


def _docker_container_running() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        out = subprocess.check_output(
            ["docker", "ps", "-q", "--filter", f"name={SEARXNG_CONTAINER}"],
            stderr=subprocess.DEVNULL, timeout=5,
        ).decode().strip()
        return bool(out)
    except Exception:
        return False


# ── Install & start ───────────────────────────────────────────────────────────

def _write_searxng_settings(config_dir: Path) -> None:
    """Write a minimal SearXNG settings.yml for offline/local use."""
    config_dir.mkdir(parents=True, exist_ok=True)
    settings = """\
use_default_settings: true
general:
  debug: false
  instance_name: "hermes-offline search"
server:
  secret_key: "hermes-offline-local-secret"
  bind_address: "0.0.0.0"
  port: 8080
search:
  safe_search: 0
  autocomplete: ""
  default_lang: "en"
engines:
  - name: duckduckgo
    engine: duckduckgo
    shortcut: d
    disabled: false
  - name: google
    engine: google
    shortcut: g
    disabled: false
  - name: wikipedia
    engine: wikipedia
    shortcut: w
    disabled: false
  - name: bing
    engine: bing
    shortcut: b
    disabled: false
ui:
  static_use_hash: true
  default_locale: "en"
  query_in_title: false
  results_on_new_tab: false
"""
    settings_file = config_dir / "settings.yml"
    if not settings_file.exists():
        settings_file.write_text(settings)
        logger.info("Wrote SearXNG settings.yml to %s", settings_file)


def start_docker(pull_if_missing: bool = True) -> tuple[bool, str]:
    """
    Start (or create+start) the SearXNG Docker container.
    Returns (success, message).
    """
    if not shutil.which("docker"):
        return False, "Docker not installed"

    # Already running?
    if _docker_container_running() and is_running():
        return True, "SearXNG already running"

    # Container exists but stopped → just start it
    if _docker_container_exists():
        try:
            subprocess.run(
                ["docker", "start", SEARXNG_CONTAINER],
                check=True, capture_output=True, timeout=30,
            )
            logger.info("Started existing SearXNG container")
            return True, "SearXNG container started"
        except subprocess.CalledProcessError as exc:
            return False, f"docker start failed: {exc.stderr.decode()[:200]}"

    # Image exists but no container → create + run
    image_exists = _docker_image_exists()
    if not image_exists and not pull_if_missing:
        return False, f"Docker image {SEARXNG_DOCKER_IMAGE} not cached — run with pull_if_missing=True"

    config_dir = Path.home() / ".hermes" / "searxng"
    _write_searxng_settings(config_dir)

    cmd = [
        "docker", "run", "-d",
        "--name", SEARXNG_CONTAINER,
        "--restart", "unless-stopped",
        "-p", f"{SEARXNG_PORT}:8080",
        "-v", f"{config_dir}:/etc/searxng",
        "-e", "INSTANCE_NAME=hermes-offline",
        SEARXNG_DOCKER_IMAGE,
    ]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if proc.returncode == 0:
            logger.info("SearXNG Docker container started: %s", proc.stdout.strip()[:12])
            return True, "SearXNG started via Docker"
        return False, f"docker run failed: {proc.stderr[:300]}"
    except subprocess.TimeoutExpired:
        return False, "docker run timed out (image pull takes a few minutes)"
    except Exception as exc:
        return False, str(exc)


def install_searxng(verbose: bool = False) -> tuple[bool, str]:
    """
    Install and start SearXNG using the best available method.
    Uses detector to avoid redundant work.
    Returns (success, method_used_message).
    """
    try:
        from hermes_offline.detector import get_snapshot
        snap = get_snapshot()
    except ImportError:
        snap = None

    # A: Already running → nothing to do
    if is_running():
        return True, "SearXNG already running at " + SEARXNG_DEFAULT_URL

    # B/C: Docker path (preferred)
    has_docker = (snap.has_docker if snap else bool(shutil.which("docker")))
    if has_docker:
        image_cached = _docker_image_exists()
        action = "start existing" if _docker_container_exists() else (
                 "run cached image" if image_cached else "pull + run")
        if verbose:
            logger.info("SearXNG install via Docker (%s)...", action)
        ok, msg = start_docker(pull_if_missing=True)
        if ok:
            return True, f"SearXNG via Docker ({action})"
        logger.warning("Docker install failed (%s), trying pip fallback", msg)

    # D: pip searxng fallback (pure Python, no Docker)
    ok, msg = _install_pip_searxng(snap)
    if ok:
        return True, msg

    # E: Can't install — advise
    hints = []
    if not has_docker:
        hints.append("Install Docker Desktop to get SearXNG automatically")
    hints.append("Or run manually: docker run -d -p 8080:8080 searxng/searxng")
    return False, " / ".join(hints)


def _install_pip_searxng(snap) -> tuple[bool, str]:
    """Try to install searxng as a Python package and start it."""
    installer = _get_installer()
    if not installer:
        return False, "No pip/uv found"
    try:
        subprocess.run(
            installer + ["searxng"],
            check=True, capture_output=True, timeout=180,
        )
    except Exception as exc:
        return False, f"pip install searxng failed: {exc}"

    # Try to start it
    searxng_bin = shutil.which("searxng") or shutil.which("searx")
    if searxng_bin:
        try:
            config_dir = Path.home() / ".hermes" / "searxng"
            _write_searxng_settings(config_dir)
            subprocess.Popen(
                [searxng_bin, "-d"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True, "SearXNG started via pip install"
        except Exception as exc:
            return False, f"searxng start failed: {exc}"
    return False, "searxng installed but binary not found in PATH"


def _get_installer() -> Optional[list[str]]:
    if shutil.which("uv"):
        return ["uv", "pip", "install"]
    if shutil.which("pip"):
        return ["pip", "install"]
    if shutil.which("pip3"):
        return ["pip3", "install"]
    return None


# ── Registration with hermes ──────────────────────────────────────────────────

def register_searxng_backend(url: str = SEARXNG_DEFAULT_URL) -> bool:
    """
    Register SearXNG as the preferred hermes web search backend.
    Falls back to DDG if SearXNG is unreachable.
    Returns True if SearXNG is live and registered.
    """
    if not is_running(url):
        logger.debug("SearXNG not reachable at %s — not registering", url)
        return False

    try:
        import tools.web_tools as wt

        # Mark SearXNG as the preferred backend
        setattr(wt, "_SEARXNG_URL", url)
        setattr(wt, "_SEARXNG_REGISTERED", True)

        # Always-available flag (no API key check needed)
        original_check = getattr(wt, "check_web_search_requirements", None)
        if original_check and not getattr(original_check, "_offline_patched", False):
            def _always_ok():
                return True
            _always_ok._offline_patched = True
            wt.check_web_search_requirements = _always_ok

        logger.info("Registered SearXNG at %s as hermes web search backend", url)
        return True

    except ImportError:
        logger.debug("hermes tools.web_tools not importable yet")
        return False


def maybe_auto_start(cfg: Optional[dict] = None) -> bool:
    """
    Called at patch time: start SearXNG automatically if configured.
    config.yaml: searxng.auto_start: true
    Returns True if SearXNG ended up running.
    """
    if cfg is None:
        try:
            from hermes_cli.config import load_config
            cfg = load_config().get("searxng", {})
        except Exception:
            cfg = {}

    if not isinstance(cfg, dict):
        cfg = {}

    # Don't auto-start if explicitly disabled
    if cfg.get("enabled") is False:
        return False

    url = cfg.get("url", SEARXNG_DEFAULT_URL)

    # Already running?
    if is_running(url):
        return True

    # Auto-start only if configured or Docker container already exists
    auto = cfg.get("auto_start", False)
    container_exists = _docker_container_exists()

    if not auto and not container_exists:
        return False

    ok, msg = start_docker(pull_if_missing=False)
    if ok:
        logger.info("SearXNG auto-started: %s", msg)
        return True

    logger.debug("SearXNG auto-start failed: %s", msg)
    return False
