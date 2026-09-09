"""Statements the code was making that were not true.

A ``try/except ImportError`` fallback defining a no-op ``log_print_job``
"used only until ``deckle.core.session_log`` exists" -- it has existed
since SS-07, has its own test file, and is imported without a guard by
the module ``diagnostics`` documents itself against. And a
``try: ... finally: pass`` at the end of ``render_sheet``.

Neither changed a byte of output, which is why both survived. The cost is
paid by the next reader, who has to work out whether the guard is
guarding something: an ``ImportError`` fallback says "this dependency
might not be there", and it is.

**Source-reading tests, used here on purpose.** ``docs/decisions.md``
(2026-08-07) retired one kind of source-reading test for pinning an
artifact rather than a behaviour, and kept another because it had an
**end condition**. These have one -- they fail exactly once, when
somebody re-introduces the construct, and that failure is the message.

Three further rows of the same roadmap item live in files this branch
does not own -- ``except SourceChangedWarning`` in ``cli._cmd_impose``,
the two ``getattr``-defaulted flags in ``LayoutPanel``, and the
``schedule_saved`` signal name. Their tests belong with their edits.
"""

from __future__ import annotations

import inspect

from deckle.app import backend as backend_mod
from deckle.core import print_session as print_session_mod
from deckle.core import render as render_mod
from deckle.core import session_log as session_log_mod


def test_the_session_log_is_imported_without_a_fallback():
    """The module exists; a fallback for its absence is a false statement.

    ``deckle.core.session_log`` imports ``json``, ``logging``, ``os``,
    ``sys``, ``time``, ``pathlib`` and ``logging.handlers`` from the
    standard library plus ``deckle.core.paths`` and
    ``deckle.core.profiles`` -- both of which ``backend`` already imports
    above the old guard, so not even a circular import could have raised
    the ``ImportError`` it caught.
    """
    for module in (backend_mod, print_session_mod):
        source = inspect.getsource(module)
        assert "except ImportError" not in source, module.__name__


def test_the_backend_logs_through_the_real_session_log():
    """Not merely "the guard is gone": the name is bound to the module's
    own function, so a no-op could not be reinstated silently."""
    assert backend_mod.log_print_job is session_log_mod.log_print_job


def test_render_sheet_has_no_empty_finally():
    """``try: ... finally: pass`` did nothing, and read as though the
    absence of cleanup were being enforced. The reason there is no
    cleanup -- the path belongs to the sheet cache -- is now a plain
    comment, which is the part worth keeping."""
    source = inspect.getsource(render_mod.render_sheet)
    offenders = [
        line for line in source.splitlines() if line.strip() == "pass"
    ]

    assert offenders == [], offenders
    assert "Deliberately no cleanup" in source, (
        "the comment explaining why nothing is deleted went with the block"
    )
