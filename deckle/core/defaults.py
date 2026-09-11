"""The layout a new project starts from, when the user has said what it is.

Every new window opened on US Letter, portrait, zero gutter, zero margins,
flat sheets, ``slack_to="gutter"``, no grain, no thickness -- about a dozen
settings to reset before the first useful preview, for someone who buys the
same paper every time. This is where that answer is remembered.

Stored as ``config_dir("defaults.json")`` in the same shape as a
``.deckle``: a ``version`` and a ``layout`` object, read back through
:mod:`deckle.core.project_io`'s own layout reader so it inherits that
reader's tolerance of field drift in both directions -- a defaults file
written before a field existed, or after it was removed, still opens.

Three fields are deliberately NOT persisted -- the two crops and
``signature_lengths``. They describe one document rather than how someone
works, and carrying them forward would crop the next scan against margins
measured off a different one, or refuse to impose it with a gathering list
the user never typed.

Nothing here raises on the read path. A defaults file that cannot be read
is a preference lost, not a reason a window fails to open -- the same rule
:mod:`deckle.core.recent` states for the recent list. The *write* path does
raise, and the asymmetry is the point: saving is an explicit user action,
and swallowing its failure would leave someone believing their settings
were remembered.

**The CLI does not read this file.** ``deckle-cli export book.pdf`` must
produce the same book on two machines; a machine-local default silently
changing the paper and the fold scheme of every headless run would make
that untrue, and would make the golden-fixture regression depend on a
developer's config directory. The CLI's template is a ``.deckle`` named in
the invocation, which is explicit and reproducible.

This module must not import any Qt binding -- see
``tests/test_core_purity.py``.
"""

from __future__ import annotations

import json
import os
import warnings
from pathlib import Path

from deckle.core.diagnostics import log_exception
from deckle.core.models import LayoutSettings
from deckle.core.paths import config_dir, write_text_atomic
from deckle.core.project_io import layout_from_dict, layout_to_dict

DEFAULTS_VERSION = 1
DEFAULTS_FILENAME = "defaults.json"

EXCLUDED_FIELDS: frozenset[str] = frozenset(
    {"crop_odd_pt", "crop_even_pt", "signature_lengths"}
)
"""``LayoutSettings`` fields that describe a document, not a way of working.

The two crops are measured off one scan's margins; carried into the next
project they would silently crop a document they were never measured
against. ``signature_lengths`` is chosen for one book's page count and its
chapter breaks, and ``split_signatures_at`` refuses lengths that do not sum
to the sheet count -- so a carried-over value would make the *next* import
fail to impose, with a message about numbers the user never typed.
"""


def defaults_path() -> Path:
    """Where the saved defaults live. Nothing is created.

    ``config_dir``, not ``data_dir``: this is a setting the user chose,
    exactly like a printer profile, rather than data the application
    accumulated.

    :returns: the path, which may not exist.
    """
    return config_dir(DEFAULTS_FILENAME)


def save_defaults(layout: LayoutSettings) -> None:
    """Persist ``layout`` as the starting point for new projects.

    Written atomically, so a save that dies partway leaves the previous
    defaults rather than a truncated file that reads as none.

    :param layout: the settings to remember. The excluded fields are
        dropped here rather than at the call site, so there is one answer
        to "what is a default".
    :returns: nothing.
    :raises OSError: the config directory cannot be written. Raised, not
        swallowed: the user asked for this explicitly and a silent failure
        would have them believe it worked.
    """
    stored = {
        key: value
        for key, value in layout_to_dict(layout).items()
        if key not in EXCLUDED_FIELDS
    }
    payload = {"version": DEFAULTS_VERSION, "layout": stored}
    path = defaults_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(str(path), json.dumps(payload, indent=2))


def load_defaults() -> LayoutSettings | None:
    """The user's saved defaults, or ``None``.

    :returns: the settings, or ``None`` when nothing is saved, the file is
        unreadable, or a stored value is one this build cannot honour.
        Never raises: this runs while a window is being built, and a
        preference that cannot be read must not be what stops Deckle
        opening.

    This file's ``version`` is actually **read** -- a newer one returns
    ``None`` rather than being parsed hopefully. That is the opposite of
    what ``.deckle`` and the printer profiles do, and it is cheap here
    because there is exactly one writer and losing a preference costs
    nothing.
    """
    path = defaults_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        log_exception("defaults_unreadable", exc, path=str(path))
        return None
    if not isinstance(payload, dict):
        log_exception(
            "defaults_unreadable",
            TypeError(f"defaults file is a {type(payload).__name__}, not an object"),
            path=str(path),
        )
        return None
    version = payload.get("version", DEFAULTS_VERSION)
    if not isinstance(version, int) or version > DEFAULTS_VERSION:
        log_exception(
            "defaults_version_unsupported",
            ValueError(f"defaults file version {version!r} is newer than this build"),
            path=str(path),
        )
        return None
    stored = payload.get("layout")
    if not isinstance(stored, dict):
        log_exception(
            "defaults_unreadable",
            KeyError("layout"),
            path=str(path),
        )
        return None
    try:
        # Recorded rather than printed: an older build opening a newer
        # defaults file is the normal case, not a data-loss event, and a
        # bare warning naming a line inside Deckle is not something the
        # user can act on.
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            return layout_from_dict(stored)
    except (ValueError, TypeError, KeyError) as exc:
        log_exception("defaults_unreadable", exc, path=str(path))
        return None


def forget_defaults() -> bool:
    """Delete the saved defaults.

    :returns: whether a file was actually removed. ``False`` for "there was
        nothing saved", which is a normal answer and not an error.
    :raises OSError: never -- a defaults file that will not delete is
        reported by the caller, not raised at it.
    """
    path = defaults_path()
    try:
        os.remove(path)
    except FileNotFoundError:
        return False
    except OSError as exc:
        log_exception("defaults_delete_failed", exc, path=str(path))
        return False
    return True
