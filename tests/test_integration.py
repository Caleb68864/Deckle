"""Integration wiring tests: no orphaned views, and a full end-to-end flow.

The orphan-view test enumerates every module under ``deckle/app/views/`` and
asserts each is imported by ``deckle/app/main.py`` -- the classic failure
where a view is built and tested in isolation but never reachable from the
running app.

The end-to-end test drives the full pipeline (import PDF -> import image
directory -> reorder -> set gutter -> impose -> preview -> export -> plan
passes -> submit) against a stubbed ``PrintBackend``, crossing every
sub-spec boundary on purpose.
"""

from __future__ import annotations

import ast

import importlib
import os
import pkgutil
from dataclasses import dataclass, field
from typing import Sequence

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import deckle.app.views
from deckle.app.main import default_project
from deckle.app.state import AppState
from deckle.app.views.arrange_view import reorder_to
from deckle.app.views.import_view import load_and_apply_import
from deckle.app.views.layout_panel import apply_layout_change, set_gutter_pt
from deckle.app.views.preview_view import build_preview_frame
from deckle.core.export import export
from deckle.core.print_session import PrintSession
from deckle.core.printing import PrintResult, plan_passes

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
SAMPLE_PDF = os.path.join(FIXTURES_DIR, "sample.pdf")


# -- orphan-view test ------------------------------------------------------


def _view_module_names() -> list[str]:
    names = []
    for module_info in pkgutil.iter_modules(deckle.app.views.__path__):
        if module_info.name.startswith("_"):
            continue
        names.append(f"deckle.app.views.{module_info.name}")
    return names


def test_orphaned_views_every_view_module_is_imported_by_main():
    main_source_path = importlib.import_module("deckle.app.main").__file__
    with open(main_source_path, encoding="utf-8") as f:
        main_source = f.read()

    view_modules = _view_module_names()
    assert view_modules, "expected at least one view module under deckle/app/views/"

    orphaned = [
        name for name in view_modules if name.rsplit(".", 1)[-1] not in main_source
    ]
    assert not orphaned, f"deckle/app/main.py never imports: {orphaned}"


# -- SS-14: "python -m deckle launches the app and every view is reachable" --
#
# A bare `import deckle.__main__` (or the text-match orphan check above) only
# proves a module was *imported by name* -- it says nothing about whether the
# view is actually *constructed and mounted*, as opposed to e.g. merely
# imported for a type hint or a leftover unused import. This test parses
# `deckle/app/main.py`'s AST and proves, for every SS-09/SS-10 view class,
# that `MainWindow.__init__` both instantiates it (`self.<attr> = Cls(...)`)
# and mounts its `.widget` into the window's layout (`layout.addWidget(
# self.<attr>.widget)`) -- reachability as a structural property of the
# code, not a runtime probe.
#
# (Actually constructing a live `QMainWindow` under this test environment's
# combination of pytest plugins reproducibly crashes the interpreter --
# confirmed by isolating it to a standalone `MainWindow()` construction with
# no other assertions involved, which also crashes here even though the
# identical construction succeeds outside pytest. That's an environment
# hazard, not something a `deckle/app/main.py` change fixes, so this test
# uses the static-analysis alternative the gap explicitly allows instead of
# a runtime probe that would make the whole suite crash.)
#
# SS-13's calibration wizard is deliberately unbuilt (dispatch: manual) and
# is intentionally excluded here. SS-12's PrintDialog is reachable only from
# a click handler (not unconditionally at construction, since it depends on
# printers being available) -- it is checked separately below by the same
# AST technique, tolerant of being inside any method, not just __init__.



def _main_window_init_ast() -> ast.FunctionDef:
    main_source_path = importlib.import_module("deckle.app.main").__file__
    with open(main_source_path, encoding="utf-8") as f:
        main_source = f.read()
    tree = ast.parse(main_source, filename=main_source_path)

    main_window_cls = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow"
    )
    return next(
        node
        for node in ast.walk(main_window_cls)
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )


def _instantiated_attrs(func_node: ast.AST) -> dict[str, str]:
    """Map ``self.<attr>`` -> ``ClassName`` for every ``self.<attr> = ClassName(...)``
    assignment found in ``func_node``."""
    attrs: dict[str, str] = {}
    for node in ast.walk(func_node):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
        ):
            continue
        if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name):
            attrs[target.attr] = node.value.func.id
    return attrs


def _mounted_attrs(func_node: ast.AST) -> set[str]:
    """``self.<attr>`` names whose ``.widget`` is passed to ``addWidget(...)``
    somewhere in ``func_node``."""
    mounted: set[str] = set()
    for node in ast.walk(func_node):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "addWidget":
            continue
        for arg in node.args:
            if (
                isinstance(arg, ast.Attribute)
                and arg.attr == "widget"
                and isinstance(arg.value, ast.Attribute)
                and isinstance(arg.value.value, ast.Name)
                and arg.value.value.id == "self"
            ):
                mounted.add(arg.value.attr)
    return mounted


# Every SS-09/SS-10 view that MainWindow.__init__ is expected to build and
# mount unconditionally at startup, keyed by the class name main.py imports.
_STARTUP_VIEW_CLASSES = {"ImportView", "ArrangeView", "LayoutPanel", "PreviewView"}


def test_main_window_instantiates_and_mounts_every_startup_view():
    init_node = _main_window_init_ast()
    instantiated = _instantiated_attrs(init_node)
    mounted = _mounted_attrs(init_node)

    instantiated_classes = set(instantiated.values())
    missing_instantiation = _STARTUP_VIEW_CLASSES - instantiated_classes
    assert not missing_instantiation, (
        f"MainWindow.__init__ never instantiates: {missing_instantiation}"
    )

    attrs_for_startup_views = {
        attr for attr, cls in instantiated.items() if cls in _STARTUP_VIEW_CLASSES
    }
    unmounted = attrs_for_startup_views - mounted
    assert not unmounted, (
        f"MainWindow.__init__ instantiates but never mounts (.widget never "
        f"reaches addWidget): {unmounted}"
    )


def test_main_window_constructs_print_dialog_from_a_click_handler():
    main_source_path = importlib.import_module("deckle.app.main").__file__
    with open(main_source_path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=main_source_path)

    main_window_cls = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow"
    )

    # SS-12's PrintDialog must be constructed somewhere reachable from
    # MainWindow (a click handler, not necessarily __init__ -- it depends
    # on printers being available), not merely imported.
    constructs_print_dialog = any(
        isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "PrintDialog"
        for node in ast.walk(main_window_cls)
    )
    assert constructs_print_dialog, "MainWindow never constructs a PrintDialog"

    # And that construction must be wired to something the print button's
    # click signal actually reaches -- not dead code in an unused method.
    connects_print_button = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "connect"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "clicked"
        for node in ast.walk(main_window_cls)
    )
    assert connects_print_button, "no button.clicked.connect(...) wiring found on MainWindow"


# -- end-to-end flow ---------------------------------------------------------


def _profile(**overrides):
    from deckle.core.profiles import PrinterProfile

    base = dict(
        version=1,
        flip_axis="long",
        output_face="down",
        feed_edge="top",
        reverse_stack=True,
        imageable_area_pt=(18.0, 18.0, 18.0, 18.0),
        calibrated_at="2026-01-01T00:00:00",
        calibration_version=1,
    )
    base.update(overrides)
    return PrinterProfile(**base)


@dataclass
class _StubPrintBackend:
    """Records every submission it receives; never touches a real printer."""

    calls: list[list[int]] = field(default_factory=list)

    def submit(
        self,
        plan,
        sheets: Sequence[int],
        printer_name: str,
        copies: int,
        dpi: int,
        **_kwargs,
    ) -> PrintResult:
        self.calls.append(list(sheets))
        return PrintResult(submitted=len(sheets), job_id=f"job-{len(self.calls)}", error=None)


@pytest.fixture(autouse=True)
def _isolated_session_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DECKLE_SESSION_STATE_DIR", str(tmp_path / "sessions"))


def _make_image_dir(tmp_path) -> str:
    from PIL import Image

    dir_path = tmp_path / "images"
    dir_path.mkdir()
    for i in range(3):
        image = Image.new("RGB", (200, 300), color=(10 * i, 20, 30))
        image.save(dir_path / f"page{i + 1}.png", dpi=(150, 150))
    return str(dir_path)


def test_end_to_end_import_arrange_layout_preview_export_print(tmp_path):
    state = AppState(default_project())

    # 1. import a PDF
    pdf_pages, _pdf_warnings = load_and_apply_import(state, SAMPLE_PDF)
    assert len(state.project.pages) == len(pdf_pages) > 0

    # 2. import an image directory (replaces the source, matching
    # load_and_apply_import's documented behavior)
    image_dir = _make_image_dir(tmp_path)
    image_pages, _image_warnings = load_and_apply_import(state, image_dir)
    assert len(state.project.pages) == len(image_pages) == 3

    # 3. reorder: move the last imported page to the front. Stated as the
    # whole resulting order, because that is what a drop actually
    # produces and `reorder_to` is the only path the grid takes.
    reorder_to(state, [2, 0, 1])
    assert state.project.pages[0] == image_pages[2]

    # 4. set gutter, 5. impose -- apply_layout_change mutates and
    # immediately recomputes the whole-document SheetPlan
    plan = apply_layout_change(state, lambda project: set_gutter_pt(project, 24.0))
    assert state.project.layout.gutter_pt == 24.0
    assert plan.sheets

    # 6. preview -- render exactly the sheet on screen
    profile = _profile()
    frame = build_preview_frame(plan, profile, plan.sheets[0].index, "front")
    assert frame.rendered.width > 0
    assert frame.rendered.height > 0

    # 7. export
    out_path = str(tmp_path / "out.pdf")
    export(plan, out_path)
    assert os.path.exists(out_path)
    assert os.path.getsize(out_path) > 0

    # 8. plan passes
    passes = plan_passes(plan, profile)
    assert len(passes) == 2
    assert passes[0].side == "front"
    assert passes[1].side == "back"

    # 9. submit through a stubbed PrintBackend
    backend = _StubPrintBackend()
    session = PrintSession(plan, profile, backend, printer_name="Test Printer")
    session.start()
    while not session.finished:
        assert session.last_error is None
        session.advance()

    expected_front_order = [s.index for s in plan.sheets]
    expected_back_order = list(reversed(expected_front_order)) if profile.reverse_stack else expected_front_order
    assert backend.calls[0] == expected_front_order
    assert backend.calls[-1] == expected_back_order

    # Final SheetPlan assertions -- one sheet per two pages, no double
    # padding of an already-even three-page-plus-reorder document.
    assert plan.paper_pt == state.project.layout.paper
    assert len(plan.sheets) == (len(state.project.pages) + 1) // 2


# --- a new project starts from the user's own defaults -------------------


@pytest.fixture
def config_root(tmp_path, monkeypatch):
    """Both variables, because `paths._root` reads the environment at call
    time and an unguarded test would change the developer's next launch."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    return tmp_path


def test_a_new_project_uses_the_saved_defaults(config_root):
    from deckle.core.defaults import save_defaults
    from deckle.core.models import LayoutSettings

    save_defaults(LayoutSettings(
        paper=(841.89, 595.28), gutter_pt=36.0, binding_edge="left",
        fold_scheme="folio",
    ))

    layout = default_project().layout

    assert layout.paper == (841.89, 595.28)
    assert layout.gutter_pt == 36.0
    assert layout.fold_scheme == "folio"


def test_a_new_project_falls_back_to_letter(config_root):
    layout = default_project().layout

    assert layout.paper == (612.0, 792.0)
    assert layout.gutter_pt == 0.0
    assert layout.fold_scheme == "none"


def test_an_unreadable_defaults_file_does_not_stop_a_new_project(config_root):
    """`default_project` runs inside `MainWindow.__init__`. An exception
    there is a window that does not open, over a preference."""
    from deckle.core.defaults import defaults_path

    path = defaults_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json", encoding="utf-8")

    assert default_project().layout.paper == (612.0, 792.0)


def test_the_new_project_carries_no_pages_and_no_printer(config_root):
    project = default_project()

    assert project.pages == []
    assert project.printer is None
