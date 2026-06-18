"""
Hardware detection for model recommendation.

Detects available RAM, VRAM (NVIDIA/AMD/Apple Silicon),
and CPU core count to recommend the best local model.
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class HardwareProfile:
    total_ram_gb: float
    available_ram_gb: float
    vram_gb: float
    gpu_name: str
    cpu_cores: int
    os_name: str
    arch: str
    is_apple_silicon: bool

    @property
    def effective_vram_gb(self) -> float:
        """For Apple Silicon, unified memory is both RAM and VRAM."""
        if self.is_apple_silicon:
            return self.available_ram_gb * 0.75
        return self.vram_gb

    @property
    def tier(self) -> str:
        """Return hardware tier: ultra_low / low / mid / good / great."""
        effective = self.effective_vram_gb or self.available_ram_gb
        if effective >= 16:
            return "great"
        elif effective >= 8:
            return "good"
        elif effective >= 6:
            return "mid"
        elif effective >= 4:
            return "low"
        return "ultra_low"

    def recommended_model(self) -> str:
        models = {
            "ultra_low": "qwen3:1.7b",
            "low":       "qwen3:4b",
            "mid":       "llama3.1:8b",
            "good":      "qwen2.5-coder:14b",
            "great":     "qwen3-coder:30b",
        }
        return models[self.tier]

    def recommended_ctx(self) -> int:
        ctxs = {
            "ultra_low": 4096,
            "low":       8192,
            "mid":       16384,
            "good":      32768,
            "great":     65536,
        }
        return ctxs[self.tier]

    def summary(self) -> str:
        lines = [
            f"  OS:             {self.os_name} ({self.arch})",
            f"  RAM:            {self.total_ram_gb:.1f} GB total / {self.available_ram_gb:.1f} GB available",
        ]
        if self.vram_gb:
            lines.append(f"  VRAM:           {self.vram_gb:.1f} GB ({self.gpu_name})")
        elif self.is_apple_silicon:
            lines.append(f"  GPU:            Apple Silicon (unified memory)")
        else:
            lines.append(f"  GPU:            None (CPU-only inference)")
        lines.append(f"  CPU cores:      {self.cpu_cores}")
        lines.append(f"  Hardware tier:  {self.tier.upper()}")
        lines.append(f"  Recommended:    {self.recommended_model()}")
        return "\n".join(lines)


def _get_ram_gb() -> tuple[float, float]:
    try:
        import psutil
        vm = psutil.virtual_memory()
        return round(vm.total / 1e9, 1), round(vm.available / 1e9, 1)
    except ImportError:
        pass
    try:
        with open("/proc/meminfo") as f:
            lines = f.readlines()
        total = available = 0
        for line in lines:
            if line.startswith("MemTotal:"):
                total = int(line.split()[1]) * 1024
            elif line.startswith("MemAvailable:"):
                available = int(line.split()[1]) * 1024
        return round(total / 1e9, 1), round(available / 1e9, 1)
    except Exception:
        return 8.0, 4.0


def _get_vram_gb() -> tuple[float, str]:
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.total,name", "--format=csv,noheader,nounits"],
            stderr=subprocess.DEVNULL, timeout=5
        ).decode().strip()
        parts = out.split(",")
        vram_mb = float(parts[0].strip())
        name = parts[1].strip() if len(parts) > 1 else "NVIDIA GPU"
        return round(vram_mb / 1024, 1), name
    except Exception:
        pass
    try:
        out = subprocess.check_output(
            ["rocm-smi", "--showmeminfo", "vram", "--json"],
            stderr=subprocess.DEVNULL, timeout=5
        ).decode()
        import json
        data = json.loads(out)
        for card in data.values():
            total = card.get("VRAM Total Memory (B)", 0)
            if total:
                return round(int(total) / 1e9, 1), "AMD GPU"
    except Exception:
        pass
    return 0.0, ""


def _is_apple_silicon() -> bool:
    return platform.system() == "Darwin" and platform.machine() == "arm64"


def get_hardware_profile() -> HardwareProfile:
    total_ram, avail_ram = _get_ram_gb()
    is_apple = _is_apple_silicon()
    if is_apple:
        vram_gb, gpu_name = 0.0, "Apple Silicon"
    else:
        vram_gb, gpu_name = _get_vram_gb()

    try:
        import psutil
        cpu_cores = psutil.cpu_count(logical=False) or os.cpu_count() or 4
    except ImportError:
        cpu_cores = os.cpu_count() or 4

    return HardwareProfile(
        total_ram_gb=total_ram,
        available_ram_gb=avail_ram,
        vram_gb=vram_gb,
        gpu_name=gpu_name,
        cpu_cores=cpu_cores,
        os_name=platform.system(),
        arch=platform.machine(),
        is_apple_silicon=is_apple,
    )
