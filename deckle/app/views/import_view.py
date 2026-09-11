"""ImportView: pick a PDF or image directory and populate ``AppState``.

The actual file I/O (``deckle.core.loader``) runs on a ``QThread`` via
``ImportWorker`` -- never on the UI thread. ``load_and_apply_import`` is
the plain-Python core of that worker: it is deliberately Qt-free so it can
be exercised directly by headless tests without a ``QApplication`` or
event loop, and is the single place that turns a loaded source into an
``AppState.mutate`` call.
"""

from __future__ import annotations

import os
from dataclasses import replace
from typing import Sequence

from deckle.app.state import AppState
from deckle.core.diagnostics import log_exception
from deckle.core.loader import EncryptedPdfError, SourceLoadError, load_image_dir, load_pdf
from deckle.core.models import SourcePage


def _load_source(path: str) -> Sequence[SourcePage]:
    if os.path.isdir(path):
        return load_image_dir(path)
    return load_pdf(path)


def load_and_apply_import(
    state: AppState, source_path: str, *, append: bool = False
) -> tuple[list[SourcePage], list]:
    """Load ``source_path`` into ``state.project``, replacing or appending.

    Returns ``(pages, warnings)``. Routed through ``AppState.mutate`` like
    every other project change, so importing participates in undo and
    triggers the same debounced autosave.

    ``append`` exists because a document made of more than one source is the
    normal case for the books this program is for -- a scanned text with a
    typeset title page, plates dropped in between chapters -- and the README
    says so in as many words: "PDFs and image folders, interleaved". Every
    layer downstream of here already handles it. ``Project.pages`` is a flat
    list whose entries each name their own source, the imposer never asks
    where a page came from, and `.deckle` round-trips a mixed list without
    comment. Only the import verb was missing, so the promise was true of
    the model and false of the application.

    Appending, not merging: the pages land at the end and Arrange is where
    they are moved. That keeps this function's job to loading, and leaves
    ordering to the view built for it.

    :param state: the app state whose project is updated.
    :param source_path: a PDF file, or a directory of images.
    :param append: add to the existing pages instead of replacing them.
    :returns: ``(pages, warnings)`` -- the newly imported pages only, never
        the whole document, so a caller can report what it just added.
        The warnings come from
        :class:`~deckle.core.loader.ImportedPages` and are empty for a PDF
        import.
    :raises deckle.core.loader.SourceLoadError: any refusal from the
        loader, unchanged. Its message already names the file and the
        remedy, so nothing is caught or reworded on the way through.
    """
    pages = _load_source(source_path)
    warnings = list(getattr(pages, "warnings", []))
    page_list = list(pages)
    if append:
        state.mutate(
            lambda project: replace(project, pages=[*project.pages, *page_list])
        )
    else:
        state.mutate(lambda project: replace(project, pages=page_list))
    return page_list, warnings


def import_advisory_message(warnings: Sequence[object]) -> str:
    """One line saying what the import quietly left out, or ``""``.

    ``load_and_apply_import`` returns its warnings "so a caller can report
    what it just added", and for the whole life of the GUI the caller did
    not: ``MainWindow._on_imported`` took the list and read neither entry.
    The CLI has always printed them (``deckle.cli.report``), so importing
    a folder of scans on the command line told you that four files were
    skipped and importing the same folder in the app did not.

    That is the one advisory the loader raises that changes what reaches
    paper. ``skipped_non_image_files`` means pages the user believes they
    scanned are not in the book, and nothing downstream can notice: the
    plan, the preview and the exported PDF are all internally consistent
    around the gap. It is found by counting the printed book.

    Pure and Qt-free, like everything above the Qt wiring in this module,
    so the wording is testable without a display.

    :param warnings: the advisories from :func:`load_and_apply_import`.
        Objects carrying ``.detail``; anything else is rendered with
        ``str`` rather than dropped, because an advisory nobody can read
        is still better than an advisory nobody is shown.
    :returns: the status-bar text, or ``""`` when there is nothing to say.
    """
    details = [
        getattr(warning, "detail", None) or str(warning)
        for warning in warnings
    ]
    details = [detail for detail in details if detail]
    return " ".join(details)


# -- Qt wiring ---------------------------------------------------------
# Imported lazily inside the classes below so this module stays importable
# (and load_and_apply_import stays callable) without PySide6/a display --
# useful for headless tests that only exercise the loader/state logic.


def _qt_core():
    from PySide6.QtCore import QObject, QThread, Signal

    return QObject, QThread, Signal


def _qt_widgets():
    from PySide6.QtWidgets import (
        QCheckBox,
        QFileDialog,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QWidget,
    )

    return QCheckBox, QFileDialog, QHBoxLayout, QLabel, QPushButton, QWidget


class ImportWorker:
    """Runs ``load_and_apply_import`` on a background ``QThread``.

    Built as a plain class (not a ``QObject`` subclass) whose Qt pieces
    are assembled in ``ImportView.import_path`` so importing this module
    never requires PySide6's ``QObject``/``Signal`` machinery to already
    be usable -- only the code path that actually launches a real import
    does.

    :param state: the app state to import into.
    :param source_path: a PDF file, or a directory of images.
    :ivar pages: the imported pages, once :meth:`run` has finished.
    :ivar warnings: non-fatal import advisories.
    :ivar error: a message when the import failed, else ``None``.
    """

    def __init__(
        self, state: AppState, source_path: str, *, append: bool = False
    ) -> None:
        self.state = state
        self.source_path = source_path
        self.append = append
        self.pages: list[SourcePage] = []
        self.warnings: list = []
        self.error: str | None = None

    def run(self) -> None:
        """Perform the import, recording the outcome on ``self``.

        :returns: nothing -- results land on :attr:`pages`,
            :attr:`warnings` and :attr:`error`, because this runs as a
            ``QThread.run`` override and has nowhere to return to.
        :raises deckle.core.loader.SourceLoadError: every load failure
            except :class:`~deckle.core.loader.EncryptedPdfError`, which is
            turned into a message on :attr:`error` instead. The rest are
            left to propagate rather than silently producing an empty
            import.
        """
        try:
            self.pages, self.warnings = load_and_apply_import(
                self.state, self.source_path, append=self.append
            )
        except EncryptedPdfError as exc:
            self.error = f"password-protected PDF: {exc.path}"
        except SourceLoadError as exc:
            # Every other refusal from the loader already carries a message
            # naming the file and the remedy -- a corrupt PDF, a directory
            # with no images, a file that is not a PDF despite its name.
            # Letting them propagate wasted all of that: a QThread has
            # nowhere to deliver an exception, so Qt printed a traceback,
            # `error` stayed None, and `_on_finished` took the SUCCESS
            # branch and reported "Imported 0 page(s)." Reporting a failure
            # as a success is worse than crashing.
            self.error = str(exc)
            log_exception("import_failed", exc, path=self.source_path)


class ImportView:
    """A small widget offering "Import PDF..." / "Import Images..." actions.

    Import runs on a ``QThread`` so a large (e.g. 300-page) source never
    blocks the UI thread. ``imported`` fires on the main thread once the
    background worker finishes, carrying ``(pages, warnings)``; ``failed``
    fires with an error message instead.

    :param state: the app state to import into.
    :param parent: the parent ``QWidget``, or ``None``.
    :ivar imported: ``Signal(list, list)`` carrying ``(pages, warnings)``.
    :ivar failed: ``Signal(str)`` carrying an error message.
    :ivar widget: the ``QWidget`` to place in a layout. This class is not
        itself a widget.
    """

    def __init__(self, state: AppState, parent=None) -> None:
        QObject, QThread, Signal = _qt_core()
        QCheckBox, QFileDialog, QHBoxLayout, QLabel, QPushButton, QWidget = _qt_widgets()

        class _Signals(QObject):
            imported = Signal(list, list)
            failed = Signal(str)

        self._signals = _Signals()
        self.imported = self._signals.imported
        self.failed = self._signals.failed

        self.state = state
        self.widget = QWidget(parent)
        layout = QHBoxLayout(self.widget)
        self.import_pdf_button = QPushButton("Import PDF...", self.widget)
        self.import_images_button = QPushButton("Import Images...", self.widget)
        # Unchecked is the old behaviour, and stays the default: an import
        # that silently appended to a document the user thought it was
        # replacing would be its own kind of surprise. Checked is what makes
        # the README's "interleaved" true of the app and not just the model.
        self.append_checkbox = QCheckBox("Add to the current document", self.widget)
        self.append_checkbox.setToolTip(
            "Keep the pages already imported and add these after them. "
            "Reorder them in Arrange."
        )
        self.status_label = QLabel("", self.widget)
        layout.addWidget(self.import_pdf_button)
        layout.addWidget(self.import_images_button)
        layout.addWidget(self.append_checkbox)
        layout.addWidget(self.status_label)

        self.import_pdf_button.clicked.connect(self.pick_pdf)
        self.import_images_button.clicked.connect(self.pick_images)

        self._thread = None
        self._worker: ImportWorker | None = None
        self._QThread = QThread

    def pick_pdf(self) -> None:
        _QCheckBox, QFileDialog, *_ = _qt_widgets()
        path, _filter = QFileDialog.getOpenFileName(self.widget, "Import PDF", "", "PDF files (*.pdf)")
        if path:
            self.import_path(path)

    def pick_images(self) -> None:
        _QCheckBox, QFileDialog, *_ = _qt_widgets()
        path = QFileDialog.getExistingDirectory(self.widget, "Import Image Directory")
        if path:
            self.import_path(path)

    def import_path(self, source_path: str, append: bool | None = None) -> None:
        """Kick off a background import of ``source_path``.

        :param source_path: a PDF file, or a directory of images.
        :param append: add to the current document rather than replacing
            it, or ``None`` to take the checkbox's setting. Passed
            explicitly by tests and by any caller that already knows.
        :returns: nothing, immediately. The import runs on a ``QThread``
            so a 300-page source never blocks the UI; completion arrives as
            ``imported`` or ``failed``.
        """
        if append is None:
            append = self.append_checkbox.isChecked()
        self.status_label.setText(
            f"{'Adding' if append else 'Importing'} {source_path}..."
        )
        worker = ImportWorker(self.state, source_path, append=append)
        thread = self._QThread(self.widget)
        thread.run = worker.run  # simplest correct QThread.run override
        thread.finished.connect(lambda: self._on_finished(worker))
        self._thread = thread
        self._worker = worker
        thread.start()

    def _on_finished(self, worker: ImportWorker) -> None:
        if worker.error is not None:
            self.status_label.setText(worker.error)
            self.failed.emit(worker.error)
        else:
            verb = "Added" if worker.append else "Imported"
            self.status_label.setText(f"{verb} {len(worker.pages)} page(s).")
            self.imported.emit(worker.pages, worker.warnings)
