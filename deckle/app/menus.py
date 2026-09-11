"""Deckle's menu bar and keyboard shortcuts, written down as data.

The window had exactly two bindings -- undo and redo -- and no menu at
all. Everything else was a button somewhere in the left-hand column, which
is fine for a program you use once and hostile for one you use for an
afternoon: opening a project, saving a PDF and printing are the three
things a binder does on a loop, and each of them meant finding a button.

The menu is a **description**, not a construction. :data:`MENUS` names, for
every entry, the label a person reads and the ``MainWindow`` method it
calls; :func:`build_menu_bar` is the only part that touches Qt. Keeping the
two apart buys the thing this repo asks for everywhere else -- the
interesting half is testable without a display. A menu item pointing at a
method that does not exist, or two entries quietly claiming the same
shortcut, are both caught by importing this module and reading it.

This module must not import PySide6 at module scope; the Qt names are
reached for inside :func:`build_menu_bar`.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MenuItem:
    """One line of a menu.

    :ivar label: what the user reads. Empty means a separator.
    :ivar action: the name of the ``MainWindow`` method to call. Empty for
        a separator or a submenu.
    :ivar shortcuts: the key sequences that invoke it, in Qt's spelling.
        A tuple rather than a single string because muscle memory is not
        universal -- Redo is Ctrl+Y for some people and Ctrl+Shift+Z for
        others, and a shortcut that silently does nothing is worse than
        one that does not exist. Qt shows the first in the menu.
    :ivar submenu: the name of a ``MainWindow`` attribute already holding a
        ``QMenu`` to graft in here. Recent projects is the only one: it is
        rebuilt from the store whenever the store changes, so the menu bar
        borrows it rather than owning a second copy.
    :ivar scope: which widget the shortcut belongs to, or ``""`` for the
        whole window. ``"grid"`` means the page grid: bare letters like
        ``R`` and ``S`` are only safe while the grid has focus, since a
        window-wide letter competes with every text field on the left.
        The menu entry is the same ``QAction`` either way, so the key is
        still written next to the command where someone can find it.
    :ivar gate: what has to be true before this command can be used, or
        ``""`` for one that is always available. One of :data:`GATES`.
        The window greys the entry out otherwise.

        This lives on the entry rather than in the window because it had
        already drifted the other way round: ``_sync_document_actions``
        named four commands in a tuple in its own body and
        ``_sync_print_action`` named a fifth in its, so the set of
        commands needing a document was written down in three places --
        twice by hand, once here -- and the lookup that read them
        tolerated a name it did not recognise. A rename here, or a typo
        there, left the command permanently enabled with nothing failing.
        That is the same shape as B11, B12 and B29: one list of controls
        maintained in several places.
    """

    label: str = ""
    action: str = ""
    shortcuts: tuple[str, ...] = ()
    submenu: str = ""
    scope: str = ""
    gate: str = ""

    @property
    def is_separator(self) -> bool:
        """:returns: whether this entry is a rule rather than a command."""
        return not self.label


@dataclass(frozen=True)
class Menu:
    """One top-level menu.

    :ivar title: the menu bar label, with ``&`` marking its mnemonic.
    :ivar items: the entries, in order.
    """

    title: str
    items: tuple[MenuItem, ...] = field(default_factory=tuple)


SEPARATOR = MenuItem()


#: Every condition :attr:`MenuItem.gate` may name.
#:
#: Closed deliberately. :func:`actions_gated_by` refuses anything outside
#: this set rather than returning an empty list, because "no entry carries
#: that gate" and "the caller misspelled the gate" are indistinguishable
#: from the outside, and the second one silently disables nothing -- which
#: is exactly how the hand-written tuples failed.
GATES: frozenset[str] = frozenset(
    {
        # There is a document with pages in it.
        "document",
        # ...and a printer to send it to.
        "document+printer",
        # There is something on the undo / redo stack.
        "undo",
        "redo",
    }
)


#: The whole menu bar. Order is the order on screen.
#:
#: The groupings follow the job rather than the class layout: File is the
#: document's whole life, from importing a source to putting it on paper;
#: Edit is what you do to the pages; View is where you are looking; Help is
#: what you send us when it goes wrong.
MENUS: tuple[Menu, ...] = (
    Menu(
        "&File",
        (
            MenuItem("Import &PDF...", "import_pdf", ("Ctrl+I",)),
            MenuItem("Import &Images...", "import_images", ("Ctrl+Shift+I",)),
            SEPARATOR,
            MenuItem("&Open project...", "open_project_dialog", ("Ctrl+O",)),
            MenuItem("&Recent projects", submenu="_recent_menu"),
            MenuItem("&Save project", "save_project", ("Ctrl+S",), gate="document"),
            MenuItem(
                "Save project &as...", "save_project_as", ("Ctrl+Shift+S",),
                gate="document",
            ),
            SEPARATOR,
            MenuItem("Save &PDF...", "save_pdf", ("Ctrl+E",), gate="document"),
            MenuItem("Save one &pass as PDF...", "export_single_pass", gate="document"),
            MenuItem(
                "P&rint...", "print_document", ("Ctrl+P",),
                gate="document+printer",
            ),
            SEPARATOR,
            MenuItem("&Quit", "quit", ("Ctrl+Q",)),
        ),
    ),
    Menu(
        "&Edit",
        (
            MenuItem("&Undo", "undo", ("Ctrl+Z",), gate="undo"),
            MenuItem("&Redo", "redo", ("Ctrl+Y", "Ctrl+Shift+Z"), gate="redo"),
            SEPARATOR,
            # The grid keys. They appear here so they are discoverable -- a
            # shortcut nobody can find is a shortcut for the person who
            # wrote it -- but they fire only while the page grid has focus.
            MenuItem("Rotate selected pages &90", "rotate_selection", ("R",), scope="grid"),
            # Delete alongside S because reaching for it is the reflex, and
            # skipping is what "leave this page out" means in Deckle -- a
            # skipped page keeps its slot, so the gesture is reversible.
            # There is no destructive page removal to bind it to.
            MenuItem("&Skip / unskip pages", "skip_selection", ("S", "Del"), scope="grid"),
            MenuItem("Insert &blank...", "insert_blank"),
        ),
    ),
    Menu(
        "&View",
        (
            MenuItem("Zoom &in", "zoom_in", ("Ctrl++", "Ctrl+=")),
            MenuItem("Zoom &out", "zoom_out", ("Ctrl+-",)),
            MenuItem("&Actual size", "zoom_actual", ("Ctrl+1",)),
            MenuItem("&Fit to window", "zoom_fit", ("Ctrl+0",)),
            SEPARATOR,
            MenuItem("&Previous sheet", "previous_sheet", ("Ctrl+PgUp",)),
            MenuItem("&Next sheet", "next_sheet", ("Ctrl+PgDown",)),
            MenuItem("Fi&rst sheet", "first_sheet", ("Ctrl+Home",)),
            MenuItem("&Last sheet", "last_sheet", ("Ctrl+End",)),
        ),
    ),
    Menu(
        "&Help",
        (
            MenuItem("&About Deckle", "show_about"),
            MenuItem("Open &diagnostics folder", "open_diagnostics_folder"),
        ),
    ),
)


def action_names(menus: tuple[Menu, ...] = MENUS) -> list[str]:
    """Every ``MainWindow`` method the menu bar will try to call.

    :param menus: the menus to read, defaulting to :data:`MENUS`.
    :returns: the method names, in menu order, without duplicates.
    """
    names: list[str] = []
    for menu in menus:
        for item in menu.items:
            if item.action and item.action not in names:
                names.append(item.action)
    return names


def actions_gated_by(gate: str, menus: tuple[Menu, ...] = MENUS) -> list[str]:
    """Every command that is only available while ``gate`` holds.

    This is the single place the window asks "which entries go grey when
    there is no document?". Before it existed the answer was a tuple of
    string literals inside ``MainWindow._sync_document_actions`` and
    another inside ``_sync_print_action``, neither of which the menu
    description knew about; renaming an entry here left the command
    enabled forever and reddened nothing.

    :param gate: one of :data:`GATES`.
    :param menus: the menus to read, defaulting to :data:`MENUS`.
    :returns: the method names carrying that gate, in menu order.
    :raises ValueError: ``gate`` is not one of :data:`GATES`. Deliberately
        fatal rather than an empty list: a caller that misspells a gate
        would otherwise disable nothing, silently, which is the exact
        failure this function replaced.
    """
    if gate not in GATES:
        raise ValueError(
            f"unknown menu gate {gate!r}; known gates are {sorted(GATES)}"
        )
    return [
        item.action
        for menu in menus
        for item in menu.items
        if item.action and item.gate == gate
    ]


def submenu_names(menus: tuple[Menu, ...] = MENUS) -> list[str]:
    """Every ``MainWindow`` attribute the menu bar will graft in as a
    submenu.

    :param menus: the menus to read.
    :returns: the attribute names, in menu order.
    """
    return [item.submenu for menu in menus for item in menu.items if item.submenu]


def shortcut_conflicts(menus: tuple[Menu, ...] = MENUS) -> dict[str, list[str]]:
    """Key sequences claimed by more than one entry.

    Qt does not refuse an ambiguous shortcut; it picks one of the two
    actions, or neither, and gives no indication which. That is the kind
    of defect nobody reports because it looks like a slip of the finger,
    so it is checked here instead.

    :param menus: the menus to read.
    :returns: ``{shortcut: [label, label, ...]}`` for every sequence
        claimed twice. Empty when the bindings are unambiguous.
    """
    claimed: dict[str, list[str]] = {}
    for menu in menus:
        for item in menu.items:
            for shortcut in item.shortcuts:
                claimed.setdefault(shortcut, []).append(item.label)
    return {key: labels for key, labels in claimed.items() if len(labels) > 1}


# -- Qt wiring ---------------------------------------------------------


def build_menu_bar(
    window,
    owner,
    menus: tuple[Menu, ...] = MENUS,
    scope_widgets: dict[str, object] | None = None,
) -> dict[str, object]:
    """Populate ``window``'s menu bar from ``menus``, calling into ``owner``.

    :param window: the ``QMainWindow`` whose ``menuBar()`` is filled.
    :param owner: the object carrying the methods named by
        :attr:`MenuItem.action` and the menus named by
        :attr:`MenuItem.submenu` -- in practice the ``MainWindow``.
    :param menus: the menus to build, defaulting to :data:`MENUS`.
    :param scope_widgets: ``{scope: widget}`` for the scoped shortcuts --
        ``{"grid": <the page list>}``. A scope with no widget falls back to
        a window-wide shortcut, which is the right degradation for a
        headless caller with no grid to speak of.
    :returns: ``{action_name: QAction}``, so the window can enable and
        disable entries alongside the buttons that do the same jobs.
    :raises AttributeError: an item names a method or submenu ``owner``
        does not have. Deliberately fatal: a dead menu entry is a promise
        the window cannot keep, and the window is being built right now,
        which is the cheapest moment to find out.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QAction, QKeySequence

    widgets = scope_widgets or {}
    bar = window.menuBar()
    actions: dict[str, object] = {}
    for spec in menus:
        menu = bar.addMenu(spec.title)
        for item in spec.items:
            if item.is_separator:
                menu.addSeparator()
                continue
            if item.submenu:
                submenu = getattr(owner, item.submenu)
                submenu.setTitle(item.label)
                menu.addMenu(submenu)
                continue
            action = QAction(item.label, window)
            if item.shortcuts:
                action.setShortcuts([QKeySequence(s) for s in item.shortcuts])
            scoped = widgets.get(item.scope) if item.scope else None
            if scoped is not None:
                # One QAction, two homes: the menu shows it and the grid
                # owns the key. Adding it to the grid with a widget-scoped
                # context is what keeps a bare "S" from being swallowed by
                # -- or stolen from -- the margin fields on the left.
                action.setShortcutContext(
                    Qt.ShortcutContext.WidgetWithChildrenShortcut
                )
                scoped.addAction(action)
            # Bound method, resolved now: an item naming a method that does
            # not exist must fail while the window is being built, not the
            # first time somebody reaches for the menu mid-job.
            handler = getattr(owner, item.action)
            action.triggered.connect(lambda _checked=False, fn=handler: fn())
            menu.addAction(action)
            actions[item.action] = action
    return actions
