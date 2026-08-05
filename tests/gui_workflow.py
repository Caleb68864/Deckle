"""Drives Deckle's desktop app through a whole job and reports what happened.

Run as a script, not collected as tests. ``tests/test_gui_workflow.py``
executes it in a subprocess and asserts on the JSON it prints.

**Why a subprocess.** Constructing a real ``QMainWindow`` works perfectly in a
plain interpreter and kills the process with exit 127 under pytest on this
machine -- an interaction between pytest and the offscreen Qt platform, not a
defect in Deckle. Individual views construct fine either way, which is why the
existing UI tests build those directly. But testing views one at a time is
exactly how the whole thumbnail pipeline came to render correctly for months
while never putting a single image on screen: every part worked, and nothing
asked whether they were connected.

So this walks the real window, in a real process, and reports facts the
assertions can be specific about.

Usage::

    python tests/gui_workflow.py <source.pdf> <output.pdf>

Prints one JSON object to stdout. Any traceback goes to stderr and the exit
code is non-zero.
"""

from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main(source_pdf: str, out_pdf: str) -> int:
    from PySide6.QtWidgets import QApplication

    # Held in a local for its lifetime: a garbage-collected QApplication
    # takes every widget with it.
    _app = QApplication.instance() or QApplication([])  # noqa: F841

    import deckle.app.main as app_main
    from deckle.app.state import insert_blank
    from deckle.app.views.arrange_view import ThumbnailWorker
    from deckle.app.views.layout_panel import recompute_plan
    from deckle.core.loader import load_pdf

    # No printer may be touched: enumeration reaches the OS spooler, which
    # on a bad day blocks for minutes. The app's own timeout covers that in
    # production; a test must not depend on it.
    app_main.available_printer_names = lambda: []
    app_main.MainWindow.refresh_printers = (
        lambda self, blocking=False, timeout_ms=None: self._apply_printers([])
    )

    report: dict = {}
    window = app_main.MainWindow()

    # -- empty state ----------------------------------------------------
    report["empty_save_enabled"] = window.save_pdf_button.isEnabled()
    report["empty_print_enabled"] = window.print_button.isEnabled()
    report["empty_status"] = window.status_bar.currentMessage()

    # -- import ---------------------------------------------------------
    from dataclasses import replace

    pages = load_pdf(source_pdf)
    window.state.mutate(lambda project: replace(project, pages=list(pages)))
    window._on_imported(pages, [])

    report["page_count"] = len(window.state.project.pages)
    report["after_import_save_enabled"] = window.save_pdf_button.isEnabled()
    report["after_import_status"] = window.status_bar.currentMessage()

    # -- insert a blank, and check it is named --------------------------
    window.state.mutate(lambda project: insert_blank(project, 1))
    window.arrange_view.refresh()
    report["labels"] = [
        window.arrange_view.list_widget.item(i).text()
        for i in range(min(4, window.arrange_view.list_widget.count()))
    ]

    # -- thumbnails must reach the screen -------------------------------
    # The bug this whole file exists for: renders landed in item data and
    # were never turned into icons.
    worker = ThumbnailWorker(window.state.project.pages, 0, 4)
    worker.run()
    window.arrange_view._worker = worker
    window.arrange_view._on_thumbnails_ready(worker)
    report["thumbnail_worker_failed"] = worker.failed
    report["thumbnails_rendered"] = len(worker.rendered)
    report["icons_present"] = sum(
        1
        for i in range(window.arrange_view.list_widget.count())
        if not window.arrange_view.list_widget.item(i).icon().isNull()
    )
    report["icon_px"] = window.arrange_view.list_widget.iconSize().width()

    # -- layout: paper, orientation, and the mode tabs ------------------
    panel = window.layout_panel
    report["tabs"] = [panel.tabs.tabText(i) for i in range(panel.tabs.count())]
    panel.orientation_combo.setCurrentText("Landscape")
    report["paper_after_landscape"] = list(window.state.project.layout.paper)

    panel.grain_combo.setCurrentText("Long grain")
    panel.tabs.setCurrentIndex(panel._signature_tab_index)
    report["fold_scheme_after_tab"] = window.state.project.layout.fold_scheme

    plan = recompute_plan(window.state.project)
    report["warning_kinds"] = sorted({w.kind for w in plan.warnings})
    report["sheets"] = len(plan.sheets)
    report["signatures"] = len(plan.signatures)

    # -- back to flat sheets, then export -------------------------------
    panel.tabs.setCurrentIndex(panel._single_tab_index)
    report["fold_scheme_back"] = window.state.project.layout.fold_scheme

    from deckle.core.export import export

    plan = recompute_plan(window.state.project)
    export(plan, out_pdf)
    report["exported"] = os.path.exists(out_pdf)
    report["export_bytes"] = os.path.getsize(out_pdf) if report["exported"] else 0

    # -- the preview shows the artifact ---------------------------------
    report["preview_sheets"] = len(window.preview_view.plan.sheets)

    print(json.dumps(report))
    sys.stdout.flush()
    # Leave without unwinding. Qt's offscreen platform dies during
    # interpreter teardown here and turns a completed run into exit 127,
    # which would mask a real failure behind an artifact of shutdown. The
    # work is finished and the report is already written.
    os._exit(0)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: gui_workflow.py <source.pdf> <output.pdf>", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
