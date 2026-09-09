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
from urllib.parse import quote, unquote

from deckle.core.paths import config_dir, write_text_atomic
from deckle.core.schema import check_values


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
        """Persist this profile as JSON, keyed by printer ``name``.

        Written atomically, because this is the most expensive data Deckle
        holds: a calibration is not derived from anything, it comes from
        printing a target, measuring it by hand, and reprinting when the
        numbers are wrong. A truncating write killed partway destroyed it,
        and :meth:`load` has no tolerance for a corrupt file -- so a torn
        write did not degrade the printer to "uncalibrated", it made that
        printer unusable until the user found and deleted a file in a
        directory they have never opened.

        :param name: the printer this profile describes.
        :returns: nothing.
        :raises OSError: the config directory cannot be written. The
            previously stored calibration is still there.
        """
        path = _profile_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        # No per-field conversion: `json` serialises a tuple as an array
        # already, so naming `imageable_area_pt` here did nothing the
        # encoder was not doing anyway -- and naming one field on the way
        # out is what made it look correct to name one field on the way in.
        write_text_atomic(path, json.dumps(asdict(self), indent=2))

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

        Tolerant about keys and **strict about values**, which pull in
        opposite directions on purpose. A key this build does not know is
        a setting it can safely ignore. A value outside a field's declared
        set is a profile asking for behaviour this build cannot produce,
        and guessing wastes paper: ``flip_axis`` is
        ``Literal["long", "short"]`` and nothing enforced it, so
        ``"diagonal"`` loaded happily and planned the back pass as though
        the operator flips on the short edge -- meaning on a printer that
        flips long-edge, **every back side prints upside down**, for a
        whole stack, with nothing on screen to suggest it.

        :param name: the printer whose profile to read.
        :returns: the profile.
        :raises OSError: no profile is stored for that printer.
        :raises json.JSONDecodeError: the stored file is not valid JSON.
        :raises deckle.core.schema.StoredValueError: a stored value is not
            one this build can honour. A ``ValueError``, so a caller with
            a ``ValueError`` branch already reports it cleanly.
        """
        path = _profile_path(name)
        if not path.exists():
            # A profile saved before printer names were percent-encoded.
            # Fall back rather than reporting the printer as uncalibrated,
            # which would send the user to re-measure something they had
            # already measured.
            legacy = _legacy_profile_path(name)
            if legacy.exists():
                path = legacy
        data = json.loads(path.read_text(encoding="utf-8"))
        known = {field.name for field in dataclasses.fields(cls)}
        kwargs = {key: value for key, value in data.items() if key in known}
        check_values(cls, kwargs, subject="printer profile field")
        # Every list back to a tuple, not just `imageable_area_pt`. That
        # entry's own lesson -- any `Type(**stored_dict)` breaks on the next
        # field change -- was applied here only to unknown keys; the tuple
        # half was fixed in the layout loader alone, and naming one field
        # is exactly how `crop_odd_pt` came back as a list. No
        # `PrinterProfile` field is genuinely a list.
        kwargs = {
            key: tuple(value) if isinstance(value, list) else value
            for key, value in kwargs.items()
        }
        return cls(**kwargs)


def _config_dir() -> Path:
    """The OS-appropriate config directory for Deckle's printer profiles.

    The three-way platform answer now lives in
    :func:`deckle.core.paths.config_dir`, because the recent-projects list
    needs the same one and two copies could drift -- leaving a user's
    profiles somewhere their recent list was not.
    """
    return config_dir("printer_profiles")


def _safe_profile_stem(name: str) -> str:
    """``name`` as a filename component that cannot leave its directory.

    A printer name is not a filename and is not the user's to sanitise. On
    Windows a queue is routinely called ``\\\\server\\queue``, which is an
    absolute UNC path -- so ``config_dir / f"{name}.json"`` discarded the
    config directory entirely and wrote the profile onto the print server.
    ``/``, ``:`` and ``..`` do the same job on the other platforms.

    Percent-encoding rather than replacement, because it is reversible and
    total: two printers whose names differ only in punctuation still get
    two files, where mapping every awkward character to ``_`` would have
    silently merged their calibrations.

    Spaces, hyphens, underscores and dots are left alone. They are harmless
    in a path component, and this directory is one the GUIDE sends people
    into to read and delete files by hand -- ``My Test Printer.json`` is
    findable and ``My%20Test%20Printer.json`` is not. It also means the
    overwhelmingly common name encodes to exactly what it already was, so
    no existing profile needs migrating.

    A dot is safe *within* a component but ``.`` and ``..`` name directories,
    and since every separator is encoded those two exact stems are the only
    way left to escape. They fall back to encoding everything.

    :param name: the printer name.
    :returns: a single safe path component, without the suffix.
    """
    stem = quote(name, safe=" -_.")
    if stem.strip(".") == "":
        return quote(name, safe="")
    return stem


def _profile_path(name: str) -> Path:
    return _config_dir() / f"{_safe_profile_stem(name)}.json"


def saved_profiles() -> dict[str, Path]:
    """Every calibrated profile on this machine, as ``{name: file}``.

    :returns: printer names mapped to the file each was read from, in
        no particular order. Empty when nothing has ever been saved --
        the directory is not created just to look in it.

    The names are recovered by reversing :func:`_safe_profile_stem`, which
    is possible only because that function percent-encodes rather than
    replacing: ``quote``/``unquote`` round-trip, where a scheme mapping
    every awkward character to ``_`` would have made two printers
    indistinguishable here as well as on disk.

    Two files can decode to the same name -- an encoded one and a
    pre-encoding legacy one -- and the encoded file wins, which is the
    same precedence :meth:`PrinterProfile.load` applies. It reads the
    legacy path only when the encoded one is absent, so listing a name
    against a file ``load`` would not open is the one answer that would be
    actively wrong.

    Nothing here opens or parses a file. A listing must not be able to
    fail because one calibration has become unreadable -- that is exactly
    when its owner most needs to see it named -- so validity is the
    caller's question to ask, per profile, via :meth:`PrinterProfile.load`.
    """
    directory = _config_dir()
    if not directory.exists():
        return {}
    found: dict[str, Path] = {}
    for path in sorted(directory.glob("*.json")):
        name = unquote(path.stem)
        # `sorted` alone does not decide this: `%41.json` and `A.json` both
        # decode to "A" and either could sort first. Ask the same question
        # `load` asks instead of relying on filename order.
        if name in found and path != _profile_path(name):
            continue
        found[name] = path
    return found


def _legacy_profile_path(name: str) -> Path:
    """Where a profile written before the names were encoded would be.

    Read-only and deliberately never written to again. A calibration is
    measured by hand and reprinted when the numbers are wrong; changing the
    naming scheme must not quietly orphan one that already exists.

    :param name: the printer name.
    :returns: the pre-encoding path.
    """
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
