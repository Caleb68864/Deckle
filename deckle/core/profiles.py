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
import dataclasses
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from deckle.core.paths import config_dir


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

    back_offset_x_pt: float = 0.0
    back_offset_y_pt: float = 0.0
    """How far to move back-side content so it lands behind the front.

    Consumer printers do not put the second side exactly behind the first,
    and a manual-duplex reload is worse than a real duplexer because the
    stack is re-registered by hand against the paper guides. Fold a folio
    sheet and the error doubles and becomes visible: the spine margin
    differs between recto and verso, and trimming the fore-edge leaves the
    text block off-centre on every other page. On a gutter-shift job one
    side of every leaf ends up with a narrower gutter, and a 3-hole punch
    eats into text on the tighter side.

    No other imposition tool can correct this, because every one of them
    ends at a PDF and cannot know what a particular printer does to the
    second side. Deckle drives the printer, so it can.

    **The stored numbers are the correction, not the error** -- the
    distance the back-side content is moved when printing, in PDF points,
    +x right and +y up. A target showing the back sitting 3pt left of
    where it belongs is corrected with ``back_offset_x_pt = +3``.

    Both default to ``0.0``: an uncalibrated printer behaves exactly as it
    did before this existed. Zero is the identity here, never a guess.

    **This corrects a constant translation only.** It cannot correct
    rotational skew or a scale error, and anything surfacing it should say
    so rather than implying it fixes all misregistration.
    """

    def save(self, name: str) -> None:
        """Persist this profile as JSON, keyed by printer ``name``."""
        path = _profile_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        data["imageable_area_pt"] = list(self.imageable_area_pt)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, name: str) -> "PrinterProfile":
        """Load a previously-saved profile for printer ``name``.

        Tolerates field drift in both directions: keys this build does not
        recognise are dropped, and keys it expects but does not find fall
        back to the dataclass defaults.

        That is not speculative hardening. ``cls(**data)`` is a schema
        contract whether or not it was written as one, and the same latent
        break was already found and fixed once for ``LayoutSettings`` --
        see the 2026-08-04 decision-log entry, whose closing note is that
        any ``Type(**stored_dict)`` breaks on the next field change.
        Adding ``back_offset_x_pt``/``back_offset_y_pt`` is that change:
        without this, a profile written by a build that has them cannot be
        read by one that does not, and a calibration measured once would
        be lost by a downgrade rather than ignored.
        """
        path = _profile_path(name)
        data = json.loads(path.read_text(encoding="utf-8"))
        known = {field.name for field in dataclasses.fields(cls)}
        kwargs = {key: value for key, value in data.items() if key in known}
        if "imageable_area_pt" in kwargs:
            kwargs["imageable_area_pt"] = tuple(kwargs["imageable_area_pt"])
        return cls(**kwargs)


def _config_dir() -> Path:
    """The OS-appropriate config directory for Deckle's printer profiles.

    The three-way platform answer now lives in
    :func:`deckle.core.paths.config_dir`, because the recent-projects list
    needs the same one and two copies could drift -- leaving a user's
    profiles somewhere their recent list was not.
    """
    return config_dir("printer_profiles")


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
