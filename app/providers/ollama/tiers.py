"""Local model tiers (requirement T5) and the deterministic selection rule."""

from __future__ import annotations

from dataclasses import dataclass

from app.setup.hardware import Hardware


@dataclass(frozen=True)
class Tier:
    number: int
    tag: str
    description: str
    download_gb: float
    min_vram_gb: int
    min_ram_gb: int | None  # None: GPU only

    def fits_gpu(self, vram_gb: int) -> bool:
        return vram_gb >= self.min_vram_gb

    def fits_ram(self, ram_gb: int) -> bool:
        return self.min_ram_gb is not None and ram_gb >= self.min_ram_gb

    def fits(self, hw: Hardware) -> bool:
        return self.fits_gpu(hw.vram_gb) or self.fits_ram(hw.ram_gb)


# Tags, tools capability and Apache-2.0 licences verified on 2026-11-17.
TIERS: tuple[Tier, ...] = (
    Tier(1, "qwen3.5:122b-a10b-q4_K_M", "Qwen3.5, 125B MoE, 10B active", 81, 96, 96),
    Tier(2, "qwen3.8:27b-q8_0", "Qwen3.8, 27B dense", 30, 40, None),
    Tier(3, "qwen3.8:27b-q4_K_M", "Qwen3.8, 27B dense", 18, 24, None),
    Tier(4, "qwen3.6:35b-a3b-q8_0", "Qwen3.6, 36B MoE, 3B active", 39, 44, 48),
    Tier(5, "qwen3.6:35b-a3b-q4_K_M", "Qwen3.6, 36B MoE, 3B active", 24, 28, 32),
    Tier(6, "qwen3.5:9b-q8_0", "Qwen3.5, 9.7B dense", 10, 12, None),
    Tier(7, "qwen3.5:9b-q4_K_M", "Qwen3.5, 9.7B dense", 6.6, 8, 16),
    Tier(8, "qwen3.5:4b-q4_K_M", "Qwen3.5, 4.7B dense", 3.3, 5, 8),
    Tier(9, "qwen3.5:2b-q4_K_M", "Qwen3.5, 2.3B dense", 1.9, 3, 6),
)
FLOOR_TIER = 9


def fitting_tiers(hw: Hardware) -> list[Tier]:
    """All fitting tiers, best (lowest #) first."""
    return [t for t in TIERS if t.fits(hw)]


def preselected_tier(hw: Hardware) -> Tier | None:
    """GPU-fit first (lowest #), otherwise the lowest # that fits by RAM."""
    gpu = [t for t in TIERS if t.fits_gpu(hw.vram_gb)]
    if gpu:
        return gpu[0]
    ram = [t for t in TIERS if t.fits_ram(hw.ram_gb)]
    return ram[0] if ram else None


def tier_by_tag(tag: str) -> Tier | None:
    return next((t for t in TIERS if t.tag == tag), None)
