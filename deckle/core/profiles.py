"""PrinterProfile: calibrated manual-duplex printer behavior.

A profile captures how a specific physical printer behaves on the second
(back) pass of a manual-duplex job -- which face the sheet lands, which
edge feeds first, whether the operator needs to reverse the output stack,
and which edge the operator flips the paper on. It is persisted as JSON,
one file per printer name, under the OS config directory.

Until calibration (SS-13) exists, ``BUILTIN_PRESETS`` supplies profiles
covering the two reload behaviors from the verified back-pass ordering
table, so ``plan_passes`` (see ``deckle/core/printing.py``) never requires
a calibration run to function.

This module must not import any Qt binding -- see
``tests/test_core_purity.py``.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class PrinterProfile:
    """A printer's calibrated manual-duplex behavior, persisted as JSON.

    ``reverse_stack`` and ``flip_axis`` are the two behavioral axes that
    ``plan_passes`` consumes directly: ``reverse_stack`` (derived, at
    calibration time, from the combination of ``output_face`` and
    ``feed_edge`` -- a face-down output on a printer that does not
    re-invert the stack needs its back pass reversed to restore sheet
    order; a face-up output that preserves order does not) drives sheet
    order on the back pass, while ``flip_axis`` drives whether back sides
    need a 180-degree rotation.
    """

    version: int
    flip_axis: Literal["long", "short"]
    output_face: Literal["up", "down"]
    feed_edge: Literal["top", "bottom"]
    reverse_stack: bool
    imageable_area_pt: tuple[float, float, float, float]
    calibrated_at: str
    calibration_version: int

    def save(self, name: str) -> None:
        """Persist this profile as JSON, keyed by printer ``name``."""
        path = _profile_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        data["imageable_area_pt"] = list(self.imageable_area_pt)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, name: str) -> "PrinterProfile":
        """Load a previously-saved profile for printer ``name``."""
        path = _profile_path(name)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["imageable_area_pt"] = tuple(data["imageable_area_pt"])
        return cls(**data)


def _config_dir() -> Path:
    """The OS-appropriate config directory for Deckle's printer profiles."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "Deckle" / "printer_profiles"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Deckle" / "printer_profiles"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "deckle" / "printer_profiles"


def _profile_path(name: str) -> Path:
    return _config_dir() / f"{name}.json"


# Built-in presets covering the two reload behaviors from the verified
# back-pass ordering table (see deckle/core/printing.py), so a user can
# print manual-duplex jobs before any calibration run (SS-13) exists.
BUILTIN_PRESETS: dict[str, PrinterProfile] = {
    "generic_face_down_reversed": PrinterProfile(
        version=1,
        flip_axis="long",
        output_face="down",
        feed_edge="top",
        reverse_stack=True,
        imageable_area_pt=(18.0, 18.0, 18.0, 18.0),
        calibrated_at="",
        calibration_version=0,
    ),
    "generic_face_up_in_order": PrinterProfile(
        version=1,
        flip_axis="short",
        output_face="up",
        feed_edge="bottom",
        reverse_stack=False,
        imageable_area_pt=(18.0, 18.0, 18.0, 18.0),
        calibrated_at="",
        calibration_version=0,
    ),
}
