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
from deckle.core.loader import EncryptedPdfError, load_image_dir, load_pdf
from deckle.core.models import SourcePage


def _load_source(path: str) -> Sequence[SourcePage]:
    if os.path.isdir(path):
        return load_image_dir(path)
    return load_pdf(path)


def load_and_apply_import(state: AppState, source_path: str) -> tuple[list[SourcePage], list]:
    """Load ``source_path`` and replace ``state.project.pages`` with it.

    Returns ``(pages, warnings)``. Routed through ``AppState.mutate`` like
    every other project change, so importing participates in undo and
    triggers the same debounced autosave.

    :param state: the app state whose project is replaced.
    :param source_path: a PDF file, or a directory of images.
    :returns: ``(pages, warnings)`` -- the warnings come from
        :class:`~deckle.core.loader.ImportedPages` and are empty for a PDF
        import.
    :raises deckle.core.loader.SourceLoadError: any refusal from the
        loader, unchanged. Its message already names the file and the
        remedy, so nothing is caught or reworded on the way through.
    """
    pages = _load_source(source_path)
    warnings = list(getattr(pages, "warnings", []))
    page_list = list(pages)
    state.mutate(lambda project: replace(project, pages=page_list))
    return page_list, warnings


# -- Qt wiring ---------------------------------------------------------
# Imported lazily inside the classes below so this module stays importable
# (and load_and_apply_import stays callable) without PySide6/a display --
# useful for headless tests that only exercise the loader/state logic.


def _qt_core():
    from PySide6.QtCore import QObject, QThread, Signal

    return QObject, QThread, Signal


def _qt_widgets():
    from PySide6.QtWidgets import (
        QFileDialog,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QWidget,
    )

    return QFileDialog, QHBoxLayout, QLabel, QPushButton, QWidget


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

    def __init__(self, state: AppState, source_path: str) -> None:
        self.state = state
        self.source_path = source_path
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
            self.pages, self.warnings = load_and_apply_import(self.state, self.source_path)
        except EncryptedPdfError as exc:
            self.error = f"password-protected PDF: {exc.path}"


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
        QFileDialog, QHBoxLayout, QLabel, QPushButton, QWidget = _qt_widgets()

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
        self.status_label = QLabel("", self.widget)
        layout.addWidget(self.import_pdf_button)
        layout.addWidget(self.import_images_button)
        layout.addWidget(self.status_label)

        self.import_pdf_button.clicked.connect(self._pick_pdf)
        self.import_images_button.clicked.connect(self._pick_images)

        self._thread = None
        self._worker: ImportWorker | None = None
        self._QThread = QThread

    def _pick_pdf(self) -> None:
        QFileDialog, *_ = _qt_widgets()
        path, _filter = QFileDialog.getOpenFileName(self.widget, "Import PDF", "", "PDF files (*.pdf)")
        if path:
            self.import_path(path)

    def _pick_images(self) -> None:
        QFileDialog, *_ = _qt_widgets()
        path = QFileDialog.getExistingDirectory(self.widget, "Import Image Directory")
        if path:
            self.import_path(path)

    def import_path(self, source_path: str) -> None:
        """Kick off a background import of ``source_path``.

        :param source_path: a PDF file, or a directory of images.
        :returns: nothing, immediately. The import runs on a ``QThread``
            so a 300-page source never blocks the UI; completion arrives as
            ``imported`` or ``failed``.
        """
        self.status_label.setText(f"Importing {source_path}...")
        worker = ImportWorker(self.state, source_path)
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
            self.status_label.setText(f"Imported {len(worker.pages)} page(s).")
            self.imported.emit(worker.pages, worker.warnings)
