"""Palette-override regression tests.

A palette is a genre statement, not just a program-number swap. These
tests pin two contracts:

1. Every overridable RunConfig field is reachable through Palette: when
   a palette declares a value, ``RunConfig().with_palette(p).<field>``
   reflects that value; when it doesn't (None), the RunConfig default
   survives.
2. ``humanize=False`` is a true pass-through: the humanize helpers in
   mapping.py return the unmodified input, so a palette that pins
   ``humanize=False`` lands a strict-grid performance.

The byte-identical determinism audit in test_determinism.py already
loops PALETTES — these tests cover the static field-propagation contract
that drives that audit's correctness.
"""
from __future__ import annotations

from dataclasses import fields, replace

import pytest

from btc_sonify.config import OVERRIDABLE, PALETTES, Palette, RunConfig
from btc_sonify.mapping import _humanize_timing, _humanize_velocity


# --- Field propagation -------------------------------------------------

@pytest.mark.parametrize("palette_name", list(PALETTES.keys()))
@pytest.mark.parametrize("field_name", OVERRIDABLE)
def test_palette_override_propagates_or_keeps_default(palette_name, field_name):
    palette = PALETTES[palette_name]
    base = RunConfig()
    cfg = base.with_palette(palette)

    palette_value = getattr(palette, field_name)
    actual = getattr(cfg, field_name)

    if palette_value is None:
        # Palette doesn't pin this knob — RunConfig default must survive.
        expected = getattr(base, field_name)
        assert actual == expected, (
            f"{palette_name}: {field_name} should keep RunConfig default "
            f"{expected!r}, got {actual!r}"
        )
    else:
        # Palette pins this knob — must propagate verbatim.
        assert actual == palette_value, (
            f"{palette_name}: {field_name} should equal palette value "
            f"{palette_value!r}, got {actual!r}"
        )


def test_overridable_tuple_lists_real_runconfig_fields():
    """OVERRIDABLE is the contract surface — every entry must name a
    real RunConfig field, otherwise replace() will TypeError at use."""
    rc_fields = {f.name for f in fields(RunConfig)}
    for name in OVERRIDABLE:
        assert name in rc_fields, f"OVERRIDABLE entry {name!r} is not a RunConfig field"


def test_overridable_tuple_matches_palette_optional_fields():
    """Palette's Optional override fields and OVERRIDABLE must agree.
    Drift between them would silently drop a palette setting."""
    palette_optional = {
        f.name for f in fields(Palette)
        if f.name not in {
            "name", "melody_program", "harmony_program",
            "drum_program", "bass_program", "voice_program",
            "rubato_default",
        }
    }
    assert palette_optional == set(OVERRIDABLE), (
        "Palette's Optional fields and OVERRIDABLE diverged: "
        f"only-in-palette={palette_optional - set(OVERRIDABLE)}, "
        f"only-in-overridable={set(OVERRIDABLE) - palette_optional}"
    )


# --- Cinematic concrete spot-checks (catch silent renames) -------------

def test_cinematic_overrides_match_spec():
    """Hard-coded cross-check: the cinematic palette should land the
    exact anchor values from the music-engineer spec. Catches the case
    where someone refactors PALETTES and accidentally drops a value."""
    cfg = RunConfig().with_palette(PALETTES["cinematic"])
    assert cfg.humanize is True
    assert cfg.velocity_min == 28          # anchor — overrides CLAUDE.md floor
    assert cfg.velocity_max == 122
    assert cfg.legato_fraction == 1.02
    assert cfg.rubato_min_factor == 0.55
    assert cfg.rubato_max_factor == 1.15
    assert cfg.drum_volume_decile == 0.94
    assert cfg.drum_range_decile == 0.92
    assert cfg.hi_hat_velocity_factor == 0.18
    assert cfg.harmony_rhythm == "sustained"


def test_palette_harmony_rhythm_assignments():
    """Each palette has the right rhythm character: cinematic and
    classical sustain (default), synthwave goes up, electronic goes
    down."""
    rhythms = {
        name: RunConfig().with_palette(p).harmony_rhythm
        for name, p in PALETTES.items()
    }
    assert rhythms["cinematic"] == "sustained"
    assert rhythms["classical"] == "sustained"
    assert rhythms["synthwave"] == "arp_up"
    assert rhythms["electronic"] == "arp_down"


# --- Typo safety on Palette construction -------------------------------

def test_palette_with_unknown_runconfig_field_raises():
    """If a future Palette gained a field whose name didn't match any
    RunConfig field, with_palette() must fail loudly via replace()
    rather than silently no-op'ing the override.

    We simulate this by mutating the OVERRIDABLE check at the call
    site: pass through replace() directly with a bogus field name."""
    with pytest.raises(TypeError):
        replace(RunConfig(), nonexistent_field=42)


# --- humanize=False pass-through regression ----------------------------

def test_humanize_false_velocity_is_passthrough():
    cfg = replace(RunConfig(), humanize=False)
    # All idx/salt combinations must return the input unchanged.
    for idx in range(20):
        for salt in (0, 100, 200, 999):
            assert _humanize_velocity(80, idx, salt, cfg) == 80


def test_humanize_false_timing_is_zero():
    cfg = replace(RunConfig(), humanize=False)
    for idx in range(20):
        for salt in (0, 5, 100, 200):
            assert _humanize_timing(idx, salt, cfg) == 0


def test_humanize_true_actually_jitters():
    """Sanity check: with humanize=True, at least *some* offsets are
    non-zero. If this ever returns all zeros, _humanize_* is broken."""
    cfg = RunConfig()  # humanize=True by default
    timings = [_humanize_timing(i, 0, cfg) for i in range(50)]
    assert any(t != 0 for t in timings), "humanize=True should produce non-zero offsets"
