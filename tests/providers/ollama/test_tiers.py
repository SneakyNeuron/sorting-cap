import re

import pytest
from helpers import hardware

from app.paths import REPO_ROOT
from app.providers.ollama.tiers import TIERS, fitting_tiers, preselected_tier

SPEC = (REPO_ROOT / "specs/features/01-chat-shell-setup/requirements.md").read_text()


def _spec_rows():
    rows = re.findall(
        r"^\| (\d) \| `([^`]+)` \| [^|]+\| ([\d.]+) GB \| (\d+) GB \| ([^|]+)\|", SPEC, re.M
    )
    out = []
    for number, tag, download, vram, ram in rows:
        m = re.match(r"\s*(\d+) GB", ram)
        out.append((int(number), tag, float(download), int(vram), int(m.group(1)) if m else None))
    return out


def test_table_matches_requirement_t5():
    assert len(_spec_rows()) == 9
    assert [
        (t.number, t.tag, t.download_gb, t.min_vram_gb, t.min_ram_gb) for t in TIERS
    ] == _spec_rows()


# Every worked example from T5: (vram, ram, preselected, also offered)
EXAMPLES = [
    (0, 8, 8, ()),
    (0, 16, 7, ()),
    (0, 32, 5, ()),
    (0, 64, 4, ()),
    (0, 128, 1, ()),
    (8, 16, 7, ()),
    (12, 16, 6, ()),
    (12, 64, 6, ()),
    (16, 32, 6, ()),
    (4, 64, 9, (4,)),
    (8, 64, 7, (4,)),
    (24, 125, 3, ()),
    (48, 64, 2, ()),
    (96, 128, 1, ()),
]


@pytest.mark.parametrize(("vram", "ram", "best", "also"), EXAMPLES)
def test_worked_examples(vram, ram, best, also):
    hw = hardware(ram=ram, vram=vram)
    assert preselected_tier(hw).number == best
    offered = [t.number for t in fitting_tiers(hw)]
    assert best in offered and all(a in offered for a in also)
    assert offered == sorted(offered)


def test_no_gpu_24gb_ram_does_not_offer_6():
    hw = hardware(ram=24, vram=0)
    assert preselected_tier(hw).number == 7
    assert 6 not in [t.number for t in fitting_tiers(hw)]


def test_nothing_fits():
    hw = hardware(ram=5, vram=2)
    assert fitting_tiers(hw) == [] and preselected_tier(hw) is None


def test_rounded_hardware_gets_nominal_tier():
    from app.setup.hardware import kib_to_gb, mib_to_gb

    assert preselected_tier(hardware(ram=kib_to_gb(16_256_000), vram=mib_to_gb(24564))).number == 3
    assert preselected_tier(hardware(ram=kib_to_gb(16_256_000), vram=0)).number == 7
