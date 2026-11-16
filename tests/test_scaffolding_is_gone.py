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

The three further rows of the same roadmap item (M7) that lived in files
that branch did not own were closed on 2026-11-16, with their tests at
the end of this file: the unreachable ``except SourceChangedWarning`` in
``cli._cmd_impose``, the two ``getattr``-defaulted flags in
``LayoutPanel`` (whose behaviour ``tests/test_layout_panel_flags.py``
pins), and the ``schedule_saved`` signal, renamed ``status_message``
because it carries every outcome the panel reports, not only a saved
schedule.
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


# -- M7's remaining rows, closed 2026-11-16 ---------------------------------


def test_saving_a_project_never_rereads_its_sources(tmp_path):
    """The premise the deleted ``except`` rested on, as behaviour.

    ``save_project`` writes references and never opens a source, so a
    source that changed -- or vanished -- since import cannot make it
    raise :class:`SourceChangedWarning`. Only ``load_project`` checks.
    """
    from deckle.core.models import LayoutSettings, Project, SourcePage, SourceRef
    from deckle.core.project_io import save_project

    src = tmp_path / "gone.pdf"  # never created: nothing to hash
    project = Project(
        pages=[SourcePage(
            ref=SourceRef(path=str(src), page_index=0, sha256="0" * 64,
                          width_pt=612.0, height_pt=792.0),
            rotate_deg=0, skipped=False,
        )],
        layout=LayoutSettings(paper=(612.0, 792.0), gutter_pt=0.0,
                              binding_edge="left"),
        printer=None,
    )

    save_project(project, str(tmp_path / "job.deckle"))

    assert (tmp_path / "job.deckle").is_file()


def test_impose_does_not_catch_what_saving_cannot_raise():
    from deckle.cli import commands

    source = inspect.getsource(commands._cmd_impose)

    assert "except SourceChangedWarning" not in source


def test_the_layout_panel_declares_its_flags():
    """Read through ``getattr`` defaults, a misspelt flag reads as its
    default forever; declared, it is an ``AttributeError`` the first time."""
    from deckle.app.views import layout_panel

    source = inspect.getsource(layout_panel)

    assert 'getattr(self, "_document_loaded"' not in source
    assert 'getattr(self, "_syncing_mode"' not in source


def test_the_panels_status_signal_is_named_for_what_it_carries():
    """It carries crop, ink-measurement, gathering, station and defaults
    outcomes as well as a saved schedule; the window shows every one."""
    import deckle.app.main as main_mod
    from deckle.app.views import layout_panel

    for module in (layout_panel, main_mod):
        assert "schedule_saved" not in inspect.getsource(module), module.__name__
    assert "status_message = Signal(str)" in inspect.getsource(layout_panel)
