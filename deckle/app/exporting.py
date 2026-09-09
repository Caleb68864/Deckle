"""Turning the job into a PDF: the whole document, or one pass of it.

The other way out of Deckle. :func:`save_pdf_as` writes what the preview is
showing -- deliberately the same ``SheetPlan``, so what you save is what you
saw -- and :func:`export_single_pass` writes one side of the manual duplex
for printing somewhere that is not this machine.

Free functions over a window-shaped object, for the same reason
:mod:`deckle.app.project_actions` is: each reads ``state``,
``preview_view.plan``, ``profile`` and the status bar, and nothing else.
Split out from the project functions because a ``.deckle`` and a PDF are
different products of the same job -- one is the work, the other is the
paper -- and they fail in different ways.

The pass itself comes from :func:`deckle.core.printing.pass_export`, which
is the same function the CLI calls: the sheet order and the half turn are
the profile's answer, not this module's, and a second implementation would
be free to disagree with the first while both looked right.

PySide6 is imported inside the functions that open a file dialog.
"""

from __future__ import annotations

import os

from deckle.core.diagnostics import log_event, log_exception
from deckle.core.export import export
from deckle.core.outputs import describe_write_failure, output_path_problem
from deckle.core.printing import pass_export

NOTHING_TO_EXPORT_MESSAGE = "Nothing to export yet -- import a PDF or images first."
"""Why Save PDF is unavailable. Shown as the button's tooltip.

Save PDF used to stay enabled with no document and scold the user *after*
they clicked it, while Print in the identical situation was disabled with an
explanation. Same class of problem deserves the same affordance.
"""


def suggested_export_name(pages) -> str:
    """A default filename derived from the first imported source.

    ``book.pdf`` imposed becomes ``book-deckle.pdf`` -- never the source
    name itself, so a careless Save can't overwrite the input.

    :param pages: the document's pages, in order.
    :returns: the suggested filename, or ``"deckle-output.pdf"`` when
        nothing has been imported yet.
    """
    if not pages:
        return "deckle-output.pdf"
    stem = os.path.splitext(os.path.basename(pages[0].ref.path))[0]
    return f"{stem}-deckle.pdf"


def suggested_pass_export_name(source_name: str, side: str) -> str:
    """The filename for a one-pass export of ``source_name``.

    ``book-deckle.pdf`` becomes ``book-deckle-front.pdf``. The side is in
    the name because the two files are indistinguishable once they leave
    this machine -- the whole point of the feature is handing them to a
    copy shop -- and printing the back pass first ruins the stack.

    :param source_name: the name a whole-document export would get.
    :param side: ``"front"`` or ``"back"``.
    :returns: the suggested filename.
    """
    stem, ext = os.path.splitext(source_name)
    return f"{stem}-{side}{ext or '.pdf'}"


def save_pdf_as(window) -> None:
    from PySide6.QtWidgets import QFileDialog

    if not window.state.project.pages:
        # Defensive: the button is disabled in this state. Never open a
        # save dialog for a document that does not exist.
        window.status_bar.showMessage(NOTHING_TO_EXPORT_MESSAGE)
        return

    start_dir = os.path.dirname(window.state.project.pages[0].ref.path) or os.getcwd()
    path, _ = QFileDialog.getSaveFileName(
        window.window,
        "Save imposed PDF",
        os.path.join(start_dir, window.suggested_export_name()),
        "PDF files (*.pdf)",
    )
    if not path:
        return
    if not path.lower().endswith(".pdf"):
        path += ".pdf"

    # Check the destination before doing any work, and say the same
    # thing the CLI says -- one document, two front ends, one
    # explanation. `deckle.core.outputs` owns the wording.
    source = window.state.project.pages[0].ref.path
    problem = output_path_problem(path, source)
    if problem is not None:
        window.status_bar.showMessage(problem)
        log_event("output_path_rejected", path=path, detail=problem)
        return

    plan = window.preview_view.plan
    sheets = len(plan.sheets)
    window.status_bar.showMessage(f"Exporting {sheets} sheet(s) to {os.path.basename(path)}...")
    try:
        export(plan, path)
    except OSError as exc:
        # The common failures are all OSError and all explainable: the
        # file is open in a viewer, the drive went away, the disk is
        # full. Anything else is a bug and should still surface as one.
        message = describe_write_failure(path, exc)
        window.status_bar.showMessage(message)
        log_exception("output_write_failed", exc, path=path)
        return
    except Exception as exc:  # noqa: BLE001 -- surfaced, never swallowed
        window.status_bar.showMessage(f"Export failed: {exc}")
        log_exception("export_failed", exc, path=path)
        return
    window.status_bar.showMessage(f"Saved {sheets} sheet(s) to {path}")


def export_single_pass(window) -> None:
    """Write one pass -- fronts or backs -- as its own PDF.

    For printing somewhere that is not this machine: a copy shop, a
    second computer, a friend's laser. Deckle's manual duplex is two
    passes through a printer with a reload in between, and the CLI has
    been able to write one of them (``--pass front``) since the
    beginning while the app could only ever write the whole document.
    Someone taking a job out of the house had no way to produce the two
    files they needed.

    The pass comes from :func:`deckle.core.printing.pass_export`, which
    is the same function the CLI calls -- the sheet order and the half
    turn are the profile's answer, not this method's, and a second
    implementation of them would be free to disagree with the first
    while both looked right.

    The reload instruction goes in the status bar and is worth reading:
    the file is going to be printed by someone who has never seen
    Deckle, and it is the sentence that decides whether the backs land
    on the right fronts.

    :returns: nothing. Every failure is reported in the status bar.
    """
    from PySide6.QtWidgets import QFileDialog

    if not window.state.project.pages:
        window.status_bar.showMessage(NOTHING_TO_EXPORT_MESSAGE)
        return

    side = window.choose_export_pass()
    if side is None:
        return

    plan = window.preview_view.plan
    # The profile the window is already drawing against -- the selected
    # printer's calibration when it has one. Exporting a pass against a
    # different profile from the one on screen would be the same lie
    # B16 was about.
    export_pass = pass_export(plan, window.profile, side)

    start_dir = os.path.dirname(window.state.project.pages[0].ref.path) or os.getcwd()
    suggested = suggested_pass_export_name(window.suggested_export_name(), side)
    path, _ = QFileDialog.getSaveFileName(
        window.window,
        f"Save {side} pass",
        os.path.join(start_dir, suggested),
        "PDF files (*.pdf)",
    )
    if not path:
        return
    if not path.lower().endswith(".pdf"):
        path += ".pdf"

    source = window.state.project.pages[0].ref.path
    problem = output_path_problem(path, source)
    if problem is not None:
        window.status_bar.showMessage(problem)
        log_event("output_path_rejected", path=path, detail=problem)
        return

    try:
        export(
            plan,
            path,
            sheets=export_pass.sheets,
            side=export_pass.side,
            rotate_180=export_pass.rotate_180,
            back_offset_pt=export_pass.back_offset_pt,
        )
    except OSError as exc:
        message = describe_write_failure(path, exc)
        window.status_bar.showMessage(message)
        log_exception("pass_write_failed", exc, path=path, side=side)
        return
    except Exception as exc:  # noqa: BLE001 -- surfaced, never swallowed
        window.status_bar.showMessage(f"Export failed: {exc}")
        log_exception("pass_export_failed", exc, path=path, side=side)
        return
    window.status_bar.showMessage(
        f"Saved the {side} pass ({len(export_pass.sheets)} sheet(s)) to "
        f"{path} -- {export_pass.reload_instruction}"
    )
    log_event(
        "pass_exported",
        path=path,
        side=side,
        sheets=len(export_pass.sheets),
    )
