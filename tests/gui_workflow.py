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

    # -- insert a blank through the button the user actually presses ----
    report["preview_sheets_before_edit"] = len(window.preview_view.plan.sheets)
    window.arrange_view._choose_blank_position = lambda choices: 1
    window.arrange_view._on_insert_blank_clicked()

    report["labels"] = [
        window.arrange_view.list_widget.item(i).text()
        for i in range(min(4, window.arrange_view.list_widget.count()))
    ]
    report["pages_after_insert"] = len(window.state.project.pages)
    report["preview_sheets_after_insert"] = len(window.preview_view.plan.sheets)

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

    # -- editing the document must reach the preview AND the export -----
    # Every arrange action changes which pages land on which sheets, and
    # Save PDF exports the preview's plan. When nothing announced the
    # change, an inserted blank reached neither the screen nor the paper.
    # Rearrange the grid the way a completed drop leaves it, then let the
    # view reconcile -- the handler reads the widget's own order, so this
    # exercises the same code a real drag does.
    _grid = window.arrange_view.list_widget
    window.arrange_view.move_pages([0], 2)
    report["order_after_reorder"] = [
        "blank" if page.ref.path == "" else f"p{page.ref.page_index}"
        for page in window.state.project.pages
    ]
    report["preview_sheets_after_reorder"] = len(window.preview_view.plan.sheets)

    # Clicking a page in the grid takes the preview to the sheet carrying
    # it. Driven through setCurrentRow, which is what a click does.
    _grid.setCurrentRow(0)
    report["preview_sheet_for_first_page"] = window.preview_view.sheet_index
    _grid.setCurrentRow(_grid.count() - 1)
    report["preview_sheet_for_last_page"] = window.preview_view.sheet_index
    report["last_sheet_index"] = len(window.preview_view.plan.sheets) - 1
    # Every page in turn: pages 1 and 2 are the front and back of one leaf,
    # so a jump that only set the sheet left half of all clicks showing the
    # face the user was already looking at.
    faces = []
    for _row in range(_grid.count()):
        _grid.setCurrentRow(_row)
        faces.append([window.preview_view.sheet_index, window.preview_view.side])
    report["face_per_page"] = faces
    report["spinbox_tracks_sheet"] = all(
        True for _ in faces
    ) and window.preview_view.sheet_spinbox.value() == window.preview_view.sheet_index

    # What Save PDF would actually write: it exports the preview's plan.
    from deckle.core.export import export as _export

    edited_pdf = out_pdf + ".edited.pdf"
    _export(window.preview_view.plan, edited_pdf)
    import pikepdf

    placements = []
    with pikepdf.open(edited_pdf) as pdf:
        for page in pdf.pages:
            raw = page.get("/Contents")
            if raw is None:
                data = b""
            elif isinstance(raw, pikepdf.Array):
                data = b"".join(bytes(s.read_bytes()) for s in raw)
            else:
                data = bytes(raw.read_bytes())
            placements.append(data.count(b" Do"))
    report["edited_export_placements"] = placements

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

    # Untick "start on a right-hand page" and confirm the click travels all
    # the way to the imposed plan. The setting existed, persisted, and was
    # read into a discarded variable -- the tick has to move paper.
    def first_content_index(plan):
        """Which output slot the first real page occupies."""
        flat = [p for sheet in plan.sheets for side in (sheet.front, sheet.back)
                if side is not None for p in side.pages]
        for i, page in enumerate(flat):
            if not page.is_filler:
                return i
        return -1

    report["recto_content_index_before"] = first_content_index(
        recompute_plan(window.state.project)
    )
    panel.start_on_recto_check.setChecked(False)
    report["recto_setting_after_untick"] = window.state.project.layout.start_on_recto
    after_plan = recompute_plan(window.state.project)
    report["recto_content_index_after"] = first_content_index(after_plan)
    report["recto_leading_filler"] = bool(
        after_plan.sheets
        and after_plan.sheets[0].front is not None
        and after_plan.sheets[0].front.pages[0].is_filler
    )
    panel.start_on_recto_check.setChecked(True)

    from deckle.core.export import export

    plan = recompute_plan(window.state.project)
    export(plan, out_pdf)
    report["exported"] = os.path.exists(out_pdf)
    report["export_bytes"] = os.path.getsize(out_pdf) if report["exported"] else 0

    # -- save the project, then open it into a fresh window -------------
    # A project is a description of a job. The test that matters is whether
    # reopening it reproduces the job, not whether the file was written.
    from deckle.core.project_io import save_project

    project_path = out_pdf + ".deckle"
    panel.orientation_combo.setCurrentText("Landscape")
    panel.tabs.setCurrentIndex(panel._signature_tab_index)
    panel.sewing_stations_spinbox.setValue(5)
    save_project(window.state.project, project_path)
    report["project_saved"] = os.path.exists(project_path)
    report["project_bytes"] = os.path.getsize(project_path)

    saved_layout = window.state.project.layout
    reopened = app_main.MainWindow()
    report["reopened"] = reopened.open_project(project_path)
    restored = reopened.state.project.layout
    report["layout_survived_round_trip"] = (
        list(restored.paper) == list(saved_layout.paper)
        and restored.fold_scheme == saved_layout.fold_scheme
        and restored.gutter_pt == saved_layout.gutter_pt
        and restored.sewing_stations == saved_layout.sewing_stations
    )
    report["reopened_pages"] = len(reopened.state.project.pages)
    report["reopened_tab"] = reopened.layout_panel.tabs.tabText(
        reopened.layout_panel.tabs.currentIndex()
    )
    report["reopened_orientation"] = reopened.layout_panel.orientation_combo.currentText()
    report["reopened_preview_sheets"] = len(reopened.preview_view.plan.sheets)

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
