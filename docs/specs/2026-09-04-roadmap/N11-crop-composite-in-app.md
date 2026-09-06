# N11 — Show the ink composite under the crop boxes

**Roadmap item:** `docs/ROADMAP.md` N11
**Depends on:** —. Edits `deckle/app/views/layout_panel.py`, so it collides with **M1** (the control-table refactor of that file) and with **N5** (which adds a control to the Signatures tab of the same panel), and it adds a `cancel` parameter to `deckle/core/render.py`'s `composite_pages`. Recommended: **N5 → N11 → M1**, so M1 refactors a panel whose control set is settled.
**Blocks:** —
**Size:** M
**Decision needed first:** none

---

## 1. Context

The Crop & trim tab has **eight spinboxes and no picture**. A user cropping a
scan sets `Crop odd left` to 0.5in and has no way to find out what that removed
short of exporting a PDF and opening it — and the thing they actually need to
know is not "what does page 1 look like" but "does this rectangle clip anything,
*on any page*". A marginal note on one page in two hundred is exactly what a
number cannot show, and the tooltip on **Measure crop from the ink** says so:

> Check the result before printing -- a marginal note on one page in two
> hundred is what a number cannot show you.

and then offers no way to check it.

The picture already exists. `deckle crop-preview` superimposes every page's ink
and draws the proposed crop on it, and the CLI prints:

> Every page's ink in one picture. Anything outside the red rectangle is what
> the crop would remove.

**The roadmap says this rendering function "may need moving to core so the app
can call it". It does not — it is already there.** `composite_pages` lives in
`deckle/core/render.py:474`, imports PIL lazily inside the function body, and
is exercised by `tests/test_composite.py` headlessly. `deckle/cli.py:1129` is
its only caller. Nothing needs to move; the panel simply has to call it.

Verified:

```bash
$ grep -rn "composite_pages" deckle
deckle/cli.py:1129:    from deckle.core.render import auto_crop_insets, composite_pages
deckle/cli.py:1162:        composite = composite_pages(
deckle/core/render.py:474:def composite_pages(
```

## 2. Current code

`deckle/core/render.py:474-559` — the function to reuse, in full signature and
the parts that matter:

```python
def composite_pages(
    pages: Sequence[SourcePage],
    *,
    dpi: int = 72,
    parity: str | None = None,
    crop_pt: Insets | None = None,
) -> RenderedPage:
    """Every page's ink in one picture, with the proposed crop drawn on it.
    ...
    :raises ValueError: no page was left to composite -- an empty picture
        would look like a document with no content, which is a different
        and much more alarming answer than "you filtered everything out".
    """
    from PIL import Image, ImageChops, ImageDraw

    selected = []
    for page in pages:
        if page.skipped or is_blank_page(page):
            continue
        ...
    scale = dpi / 72.0
    width = max(int(round(page.ref.width_pt * scale)) for page in selected)
    height = max(int(round(page.ref.height_pt * scale)) for page in selected)

    canvas = Image.new("L", (width, height), 255)
    for page in selected:
        rendered = _rasterize_for_bbox(page.ref, dpi).convert("L")
        ...
        canvas = ImageChops.darker(canvas, rendered)
```

**No `cancel` parameter**, and it rasterises every unskipped page. On a
300-page scan that is 300 pdfium rasterisations per call.

`deckle/core/render.py:183-185` — the degenerate result other renderers return
when cancelled:

```python
def _empty_rendered_page() -> RenderedPage:
    return RenderedPage(width=0, height=0, rgba=b"")
```

`deckle/core/render.py:187-196` — the precedent for a cancellable renderer:

```python
def render_sheet(
    plan: SheetPlan,
    sheet_index: int,
    side: Literal["front", "back"],
    dpi: int,
    cancel: threading.Event | None = None,
) -> RenderedPage:
    """...If ``cancel`` is
    already set, returns a degenerate page promptly without exporting or
    rasterizing.
    """
```

`deckle/cli.py:1136-1160` — the parity decision N11 must mirror, and the reason
it exists:

```python
    crop = args.crop
    if args.auto_crop:
        # The rectangle has to be measured over exactly the pages the
        # picture shows. Measuring odd and even separately -- the
        # default, and right when cropping -- and then drawing the odd
        # answer over a composite of every page produces a confidently
        # wrong picture: it shows the even pages' ink beside a
        # rectangle never measured against it, so a crop that clips
        # them looks safe.
```

`deckle/app/views/layout_panel.py:894-933` — the Crop & trim tab as it stands:
the trim spinbox, the eight crop boxes built in a loop, and the auto-crop
button:

```python
        self.crop_spinboxes: dict[tuple[str, str], object] = {}
        for parity in ("odd", "even"):
            for edge in ("left", "bottom", "right", "top"):
                box = QDoubleSpinBox(self.widget)
                box.setDecimals(3)
                box.setRange(0.0, from_points(720.0, self._unit))
                box.valueChanged.connect(
                    lambda _value, p=parity: self._on_crop_changed(p)
                )
                self.crop_spinboxes[(parity, edge)] = box
                crop_form.addRow(f"Crop {parity} {edge}:", box)

        self.auto_crop_button = QPushButton("Measure crop from the ink", self.widget)
        ...
        crop_form.addRow("", self.auto_crop_button)
```

`deckle/app/views/layout_panel.py:1602-1622` — `_crop_from_boxes` and
`_on_crop_changed`, which is where a redraw is triggered from:

```python
    def _on_crop_changed(self, parity: str) -> None:
        insets = self._crop_from_boxes(parity)
        try:
            plan = apply_layout_change(
                self.state, lambda project: set_crop(project, parity, insets)
            )
        except ValueError as exc:
            self.schedule_saved.emit(f"Crop: {exc}")
            return
        self.layout_changed.emit(plan)
```

`deckle/app/views/layout_panel.py:1624-1645` — `_on_auto_crop`, which
**rasterises every page synchronously on the GUI thread** (B17) and is the
existing precedent for "this panel does expensive rendering".

`deckle/app/views/arrange_view.py:242-301` — `ThumbnailWorker`, the worker
pattern to copy: a plain class (not a `QObject`), a `cancel` event, a `failed`
flag, and `run` that returns early when cancelled and swallows exceptions
because "a `QThread` has nowhere to deliver the exception".

`deckle/app/views/arrange_view.py:607-649` — the Qt half of that pattern:
supersede the previous worker, `thread.run = worker.run`,
`thread.finished.connect(lambda: self._on_thumbnails_ready(worker))`,
`thread.finished.connect(thread.deleteLater)`, and an
`if worker is not self._worker` guard on arrival.

`deckle/app/views/arrange_view.py:319-339` — `_icon_from_rendered`, which shows
how a `RenderedPage`'s raw RGBA becomes a `QPixmap`, **including the `.copy()`**
and why it matters:

```python
    image = QImage(
        rendered.rgba,
        rendered.width,
        rendered.height,
        rendered.width * 4,
        QImage.Format.Format_RGBA8888,
    ).copy()
    return QIcon(QPixmap.fromImage(image))
```

`deckle/app/main.py:223-269` — `_live_threads(view)`, which finds every render
thread a view still has alive by walking `view.widget.findChildren(QThread)`
plus `view._thread`. `stop_background_work` (`main.py:1140-1182`) iterates
**only** `(self.preview_view, self.arrange_view)`.

**Call sites of `composite_pages` (grep):** `deckle/cli.py:1129,1162`;
`deckle/core/render.py:474`; `tests/test_composite.py:24,80,89,100,113,128,129,142,154,164`.

**Existing tests:** `tests/test_composite.py` (the function, thoroughly, with a
`DPI` constant and `_pages(path, n, skipped=...)` helper),
`tests/test_layout_panel_widgets.py` / `test_layout_panel_settings.py` /
`test_layout_panel_refresh.py` (the panel, headless),
`tests/test_view_workers.py` (the worker cancellation contract),
`tests/test_shutdown.py` (`_live_threads` and `stop_background_work`).

## 3. Change

### Steps

1. **`deckle/core/render.py` — make the composite cancellable.** Add a
   keyword-only parameter:

   ```python
   def composite_pages(
       pages: Sequence[SourcePage],
       *,
       dpi: int = 72,
       parity: str | None = None,
       crop_pt: Insets | None = None,
       cancel: threading.Event | None = None,
   ) -> RenderedPage:
   ```

   with, in the docstring:

   ```
       :param cancel: optional event. Checked before each page is
           rasterised, and a set event returns a degenerate ``0x0`` page
           immediately -- the same contract :func:`render_sheet` gives, and
           for the same reason: this rasterises EVERY unskipped page, so a
           300-page document superseded by the next spinbox nudge would
           otherwise finish work nobody will look at.
   ```

   Implementation: `if cancel is not None and cancel.is_set(): return
   _empty_rendered_page()` immediately on entry, again inside the `for page in
   selected` loop before `_rasterize_for_bbox`, and once more before the
   `ImageDraw` block. `threading` is already imported (`render.py:31` region —
   confirm; if not, add it).

   A cancelled composite returns a degenerate page rather than raising, so the
   caller's "is this still current" guard is the only thing that has to know.

2. **`deckle/app/views/layout_panel.py` — the worker.** Beside the module's
   other plain helpers, above the Qt wiring divider at `layout_panel.py:625`:

   ```python
   COMPOSITE_PREVIEW_DPI = 36
   """Resolution for the in-panel ink composite.

   Half ``composite_pages``'s own default. That default is 72 because
   ``crop-preview`` writes a PNG someone opens and zooms; this one is shown
   in a ~380px settings column, where 72 dpi doubles the rasterisation cost
   of every page in the document for detail the column cannot display.
   """

   COMPOSITE_DEBOUNCE_MS = 300
   """How long to wait after the last crop edit before redrawing.

   Long enough that holding a spinbox's arrow key does not queue a
   whole-document rasterisation per step, short enough that it still feels
   like a response to what you typed. The same cancel-and-reschedule shape
   ``AppState`` uses for autosave, for the same reason.
   """


   class CompositeWorker:
       """Runs ``composite_pages`` on a background ``QThread``.

       Plain class, not a ``QObject`` -- the same shape as
       ``ThumbnailWorker`` in ``arrange_view.py`` and ``PreviewWorker`` in
       ``preview_view.py``, with the Qt wiring left to the panel.

       :param pages: the document's pages.
       :param parity: ``"odd"``, ``"even"`` or ``None`` for all.
       :param crop_pt: the rectangle to draw, or ``None`` for none.
       :ivar rendered: the composite, or ``None``.
       :ivar failed: set when the composite raised. Distinct from a
           ``None`` ``rendered``, which is also what a cancelled run
           leaves.
       :ivar message: what to say under the picture -- a page count, or why
           there is no picture.
       :ivar cancel: set when a newer edit supersedes this run.
       """
   ```

   `run` mirrors `ThumbnailWorker.run` (`arrange_view.py:268-301`): return early
   if cancelled; call `composite_pages(..., cancel=self.cancel)` inside
   `try/except Exception`, `log_exception("crop_composite_failed", exc)` and set
   `self.failed = True`; catch `ValueError` separately and put its message on
   `self.message` (it is the documented "you filtered everything out" case, not
   a fault); return early again if cancelled before storing the result.

3. **`deckle/app/views/layout_panel.py` — the parity choice.** Add a pure
   helper, so the "which rectangle belongs over which pages" rule is testable
   and stated once:

   ```python
   COMPOSITE_PARITIES: tuple[tuple[str, str], ...] = (
       ("all", "All pages"),
       ("odd", "Odd pages"),
       ("even", "Even pages"),
   )

   def composite_crop_for(layout, parity: str):
       """The rectangle to draw over a composite of ``parity``, or ``None``.

       Drawing the odd crop over a picture of every page is a confidently
       wrong answer: it shows the even pages' ink beside a rectangle never
       measured against it, so a crop that clips them looks safe. The CLI
       records the same reasoning at ``deckle.cli._cmd_crop_preview``.

       So: odd shows ``crop_odd_pt``; even shows ``crop_even_pt``, falling
       back to ``crop_odd_pt`` because that is what the imposer applies when
       only one is set; and "all pages" shows a rectangle only when there IS
       one rectangle -- when ``crop_even_pt`` is unset and ``crop_odd_pt``
       therefore applies to the whole document.

       :param layout: the current ``LayoutSettings``.
       :param parity: ``"all"``, ``"odd"`` or ``"even"``.
       :returns: the insets, or ``None`` to draw nothing.
       """
       if parity == "odd":
           return layout.crop_odd_pt
       if parity == "even":
           return layout.crop_even_pt or layout.crop_odd_pt
       return None if layout.crop_even_pt else layout.crop_odd_pt
   ```

   and the caption for the one case where no rectangle is drawn:

   ```python
   COMPOSITE_MIXED_MESSAGE = (
       "Odd and even pages are cropped differently, so no single rectangle "
       "describes this picture. Pick a parity above."
   )
   ```

4. **`deckle/app/views/layout_panel.py` — the controls.** On the Crop & trim
   tab, after `self.auto_crop_button`'s row (`layout_panel.py:933`):

   ```python
           self.composite_check = QCheckBox("Show the ink composite", self.widget)
           self.composite_check.setChecked(True)
           self.composite_check.setToolTip(
               "Superimpose every page's ink in one picture and draw the "
               "crop on it. Anything outside the red rectangle is what the "
               "crop would remove.\n\n"
               "This is the question a number cannot answer: not \"what "
               "does page 1 look like\" but \"does this rectangle clip "
               "anything, on any page\". A marginal note on one page in two "
               "hundred is exactly what it catches.\n\n"
               "Untick it on a very large scan -- it rasterises every page."
           )
           crop_form.addRow("", self.composite_check)

           self.composite_parity_combo = QComboBox(self.widget)
           for _key, label in COMPOSITE_PARITIES:
               self.composite_parity_combo.addItem(label)
           self._composite_parity_keys = [key for key, _ in COMPOSITE_PARITIES]
           self.composite_parity_combo.setToolTip(
               "A scan's margins alternate leaf by leaf, so odd and even "
               "pages are different pictures and one rectangle rarely fits "
               "both. Composite them separately to see it."
           )
           crop_form.addRow("Composite:", self.composite_parity_combo)

           self.composite_label = QLabel("", self.widget)
           self.composite_label.setAlignment(_qt_align_center())
           self.composite_label.setMinimumHeight(160)
           crop_form.addRow("", self.composite_label)

           self.composite_caption = QLabel("", self.widget)
           self.composite_caption.setWordWrap(True)
           crop_form.addRow("", self.composite_caption)
   ```

   `QCheckBox`, `QComboBox` and `QLabel` are all already unpacked from
   `_qt_widgets()` (`layout_panel.py:663-667`). Add a
   `_qt_align_center()` seam beside `_qt_fields_at_size_hint()`
   (`layout_panel.py:630`) returning
   `Qt.AlignmentFlag.AlignCenter`.

5. **`deckle/app/views/layout_panel.py` — the debounce.** A `QTimer` owned by
   the panel, single-shot, restarted on every edit:

   ```python
           from PySide6.QtCore import QTimer   # inside __init__, lazily

           self._composite_timer = QTimer(self.widget)
           self._composite_timer.setSingleShot(True)
           self._composite_timer.setInterval(COMPOSITE_DEBOUNCE_MS)
           self._composite_timer.timeout.connect(self._start_composite)
           self._composite_thread = None
           self._composite_worker: CompositeWorker | None = None
   ```

   ```python
       def _schedule_composite(self) -> None:
           """Redraw the composite shortly, cancelling any pending redraw.

           Cancel-and-reschedule, so holding a spinbox arrow key produces
           one rasterisation of the document rather than one per step --
           the same shape ``AppState`` uses for autosave.
           """
           if not self.composite_check.isChecked():
               self._clear_composite()
               return
           self._composite_timer.start()
   ```

   Called from: `_on_crop_changed` (after the successful branch),
   `_on_auto_crop` (at the end), `composite_check.toggled`,
   `composite_parity_combo.currentIndexChanged`, `refresh_from_project` (at the
   end, beside `_refresh_suggestion`), and `set_document_loaded`.

6. **`deckle/app/views/layout_panel.py` — the render.**

   ```python
       def _start_composite(self) -> None:
           """Kick off a background composite of the current crop.

           Supersedes any run still in flight, exactly as
           ``ArrangeView.request_visible_thumbnails`` does: without it,
           nudging a spinbox eight times queues eight whole-document
           rasterisations, each parented to the widget and so never freed,
           with the slowest painting last.
           """
           if self._composite_worker is not None:
               self._composite_worker.cancel.set()
           pages = list(self.state.project.pages)
           if not pages:
               self._clear_composite()
               return
           parity = self._composite_parity_keys[
               self.composite_parity_combo.currentIndex()
           ]
           layout = self.state.project.layout
           crop = composite_crop_for(layout, parity)
           worker = CompositeWorker(
               pages,
               parity=None if parity == "all" else parity,
               crop_pt=crop,
               dpi=COMPOSITE_PREVIEW_DPI,
           )
           thread = self._QThread(self.widget)
           thread.run = worker.run
           thread.finished.connect(lambda: self._on_composite_ready(worker))
           thread.finished.connect(thread.deleteLater)
           self._composite_thread = thread
           self._composite_worker = worker
           worker.no_rectangle_reason = (
               COMPOSITE_MIXED_MESSAGE
               if parity == "all" and layout.crop_even_pt else ""
           )
           self.composite_caption.setText("Compositing...")
           thread.start()

       def _on_composite_ready(self, worker: CompositeWorker) -> None:
           if worker is not self._composite_worker or worker.cancel.is_set():
               return
           ...
   ```

   `_on_composite_ready` sets the pixmap from `worker.rendered` using the same
   `QImage(...).copy()` dance as `_icon_from_rendered`
   (`arrange_view.py:330-339`) — **the `.copy()` is not optional**: the buffer
   belongs to a worker thread's result and painting from freed memory shows as
   intermittent garbage rather than a crash. Scale to the label's width with
   `Qt.AspectRatioMode.KeepAspectRatio` and
   `Qt.TransformationMode.SmoothTransformation`.

   The caption is, in order of precedence:
   `worker.message` (the "nothing to composite" ValueError text) →
   `"Could not draw the composite."` when `worker.failed` →
   `worker.no_rectangle_reason` when set →
   `f"{n} page(s) superimposed. Anything outside the red rectangle is what the crop would remove."`
   — the last sentence lifted verbatim from `cli._cmd_crop_preview`
   (`cli.py:1185-1188`), so both front ends say the same thing.

   `self._QThread` is not currently held on `LayoutPanel`; add
   `self._QThread = QThread` in `__init__` with `QThread` added to
   `_qt_core()`'s return (`layout_panel.py:640-643`), matching how
   `ArrangeView` and `PreviewView` do it.

   ```python
       def _clear_composite(self) -> None:
           """Take the picture down and say nothing."""
           self.composite_label.clear()
           self.composite_caption.setText("")
   ```

7. **`deckle/app/main.py` — shut the thread down with the others.**
   `stop_background_work` iterates `(self.preview_view, self.arrange_view)`
   (`main.py:1160, 1168`). Add `self.layout_panel`. `_live_threads` already
   works on any object with a `widget` and/or a `_thread`
   (`main.py:246-269`), and it reads `view._thread` first — so also add:

   ```python
       @property
       def _thread(self):
           """The current composite thread, for ``_live_threads``.

           Named to match ``ArrangeView``/``PreviewView`` so shutdown can
           treat all three the same. A render still inside pdfium when the
           interpreter finalises takes the process down with it, which is
           what ``stop_background_work`` exists to prevent -- and this
           panel now starts renders too.
           """
           return self._composite_thread
   ```

   and a `_worker` property returning `self._composite_worker`, since
   `stop_background_work` cancels via `getattr(view, "_worker", None)`
   (`main.py:1162`).

8. **`docs/GUIDE.md`** — no change here; D2/D6 are the docs pass.

## 4. Tests

### `tests/test_composite.py` (extend — it owns the function)

| Test | Setup | Assertion | Failure on the unfixed tree |
|---|---|---|---|
| `test_a_cancelled_composite_returns_a_degenerate_page` | `cancel = threading.Event(); cancel.set()`; `composite_pages(_pages(path, 2), dpi=DPI, cancel=cancel)` | `.width == 0`, `.height == 0`, `.rgba == b""`, and no `ValueError` | `TypeError: composite_pages() got an unexpected keyword argument 'cancel'` |
| `test_cancelling_partway_stops_rasterising` | a `threading.Event` subclass (or a `Mock` wrapping one) whose `is_set` sets a flag after the second call; patch `deckle.core.render._rasterize_for_bbox` to count calls | fewer calls than there are pages | as above |
| `test_an_unset_cancel_composites_normally` | `cancel=threading.Event()` | identical result to `cancel=None` | as above |

### `tests/test_layout_panel_crop_preview.py` (new)

`LayoutPanel` constructs headless (`tests/test_layout_panel_widgets.py` does
it). Drive the worker directly and the panel through a fake `QThread` that runs
synchronously — the shape `tests/test_view_workers.py` already uses.

| Test | Assertion | Failure on the unfixed tree |
|---|---|---|
| `test_odd_shows_the_odd_crop` | `composite_crop_for(layout(crop_odd_pt=A, crop_even_pt=B), "odd") is A` | `ImportError: cannot import name 'composite_crop_for'` |
| `test_even_falls_back_to_the_odd_crop` | `composite_crop_for(layout(crop_odd_pt=A, crop_even_pt=None), "even") is A` — because that is what the imposer applies | as above |
| `test_all_pages_shows_nothing_when_the_two_differ` | `composite_crop_for(layout(A, B), "all") is None` | as above |
| `test_all_pages_shows_the_one_crop_when_there_is_one` | `composite_crop_for(layout(A, None), "all") is A` | as above |
| `test_the_panel_has_a_composite_label` | `hasattr(panel, "composite_label")` and it is a `QLabel` in the Crop & trim form | `AttributeError` |
| `test_editing_a_crop_schedules_a_redraw` | patch `panel._start_composite` with a recorder; `panel.crop_spinboxes[("odd","left")].setValue(0.5)`; then fire the timer manually (`panel._composite_timer.timeout.emit()`) | the recorder fired exactly once | `AttributeError: 'LayoutPanel' object has no attribute '_composite_timer'` |
| `test_eight_quick_edits_produce_one_redraw` | set all eight boxes, then fire the timer once | the recorder fired once, not eight times | as above |
| `test_a_superseded_composite_never_paints` | build two `CompositeWorker`s; set `panel._composite_worker` to the second; call `panel._on_composite_ready(first)` | `composite_label.pixmap()` is null | as above |
| `test_unticking_the_checkbox_clears_the_picture` | tick off | `composite_label.pixmap()` is null and the caption is `""` | `AttributeError: ... 'composite_check'` |
| `test_the_caption_says_what_the_rectangle_means` | run a worker synchronously over a 2-page fixture and hand it to `_on_composite_ready` | the caption contains `"Anything outside the red rectangle is what the crop would remove."` — the CLI's exact sentence | as above |
| `test_a_mixed_crop_under_all_pages_explains_itself` | `crop_odd_pt` and `crop_even_pt` both set, parity `"all"` | the caption is `COMPOSITE_MIXED_MESSAGE` | as above |
| `test_an_empty_document_draws_nothing` | a project with no pages | no thread started, caption `""` | as above |
| `test_a_document_of_only_skipped_pages_says_so` | every page skipped; run the worker | `worker.message` names the "every page was skipped" case and nothing raises | as above |
| `test_the_worker_records_a_failure_rather_than_raising` | patch `composite_pages` to raise `RuntimeError`; run the worker | `worker.failed is True`, no exception escapes `run` | as above |

### `tests/test_output_command_parity.py` (extend)

| Test | Assertion | Failure on the unfixed tree |
|---|---|---|
| `test_the_panel_and_crop_preview_say_the_same_thing_about_the_rectangle` | the caption string in `layout_panel` and the sentence printed by `cli._cmd_crop_preview` share the substring `"Anything outside the red rectangle is what the crop would remove."` | `ImportError` for the panel constant |

### `tests/test_shutdown.py` (extend)

| Test | Assertion | Failure on the unfixed tree |
|---|---|---|
| `test_shutdown_cancels_the_composite_thread` | a stub window whose `layout_panel` exposes `_worker` / `_thread` fakes; call `MainWindow.stop_background_work` unbound | the composite worker's `cancel` is set and its thread was waited on | `AssertionError` — `stop_background_work` iterates only two views today |

## 5. Acceptance

| Check | Command |
|---|---|
| The composite tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_composite.py` |
| The new panel tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_layout_panel_crop_preview.py` |
| The panel and shutdown tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider -k "layout_panel or shutdown"` |
| Full suite still at baseline | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |
| Nothing was moved out of core | `grep -q "^def composite_pages" deckle/core/render.py` |
| The composite never runs on the GUI thread | `sed -n '/def _start_composite/,/def _on_composite_ready/p' deckle/app/views/layout_panel.py \| grep -q "thread.start()"` and `! sed -n '/def _start_composite/,/def _on_composite_ready/p' deckle/app/views/layout_panel.py \| grep -q "composite_pages("` |
| The pixmap owns its own bytes | `sed -n '/def _on_composite_ready/,/def _clear_composite/p' deckle/app/views/layout_panel.py \| grep -q "\.copy()"` |
| Core stays Qt-free | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_core_purity.py` |
| The panel's threads are shut down | `sed -n '/def stop_background_work/,$p' deckle/app/main.py \| grep -q "layout_panel"` |
| `[HUMAN]` It closes the loop | Launch Deckle, import a scanned PDF, open Crop & trim. The composite appears within a second. Drag `Crop odd left` up: the red rectangle moves in real time and the picture shows what falls outside it. Push it far enough to cut into the type and confirm you can see that happening before you print anything. |

## 6. Out of scope

- **Moving `composite_pages` anywhere.** It is already in `deckle/core/render.py`
  and already pure; the roadmap's "may need moving to core" is not the case.
- **B17** (Save PDF, Open project and Auto-crop all run synchronously on the
  GUI thread with no cancel). N11 puts the *composite* off-thread and leaves
  `_on_auto_crop` exactly as it is. Do not fix one and not the other silently —
  B17 is where the pattern gets applied uniformly.
- **B32's `ink_bbox` cache key omitting dpi.** `composite_pages` calls
  `_rasterize_for_bbox`, not `ink_bbox`, so N11 neither hits nor fixes it. But
  it is next door: if the composite is ever cached, read B32 first.
- **B33** (whole-file SHA-256 under the pdfium lock; image bytes held in
  memory). A whole-document composite makes pdfium contention more visible;
  same code, different spec.
- **Clicking the composite to set a crop by dragging.** That is a different,
  larger feature (briss's actual interaction), and it needs a decision about
  whether the panel or a dialog owns it.
- **M1.** N11 adds three more controls the panel maintains by hand in four
  places; M1 is what removes that tax.
- **D2** (GUIDE and the competitive-gaps doc still say "the desktop app has no
  crop controls at all"). Fix with the docs pass.

## 7. decisions.md entry

```
## 2026-09-05 — Eight crop spinboxes and no picture
- Symptom: The Crop & trim tab asks for eight numbers and shows nothing. The question a cropper actually has is not "what does page 1 look like" but "does this rectangle clip anything, on any page" -- and the Measure crop from the ink tooltip says exactly that ("a marginal note on one page in two hundred is what a number cannot show you") and then offered no way to look. `deckle crop-preview` has drawn that picture since it was written.
- Fix: A composite under the spinboxes, redrawn 300ms after the last edit on a background QThread, superseding any run still in flight -- the same worker shape `ThumbnailWorker` and `PreviewWorker` already use. `composite_pages` gained a `cancel` event, matching `render_sheet`, because it rasterises EVERY unskipped page and a superseded 300-page run is 300 wasted rasterisations. Rendered at 36 dpi, not the function's default 72: the picture is shown in a ~380px settings column.
- Surfaces: The roadmap expected the rendering function to need moving into `deckle.core`. It was already there -- `render.composite_pages`, pure, PIL imported lazily inside the body, with its own test module. The only thing missing was a caller.
- Surfaces: Which rectangle goes over which composite is a real decision, and the CLI had already made it: drawing the odd crop over a picture of every page shows the even pages' ink beside a rectangle never measured against it, so a crop that clips them looks safe. `composite_crop_for` states the rule once -- and under "All pages" with two different crops it draws nothing and says why.
- Watch: The panel now starts background renders, so it had to join `stop_background_work`'s list. A render still inside pdfium when the interpreter finalises takes the process down, and that list was two views long for a reason.
- Commit: <fill in>
```

## 8. Traps

- **`QImage` does not copy the buffer it is handed.** `rendered.rgba` is a
  `bytes` owned by a worker thread's result; `_icon_from_rendered`
  (`arrange_view.py:328-339`) records that the `.copy()` is what stops painting
  from freed memory, which shows as intermittent garbage rather than a crash.
- **`composite_pages` raises `ValueError` when everything is skipped or
  filtered out** (`render.py:520-524`), and that is a legitimate state a user
  can reach with N6's Skip range. Catch it and put its message in the caption;
  do not let it reach `QThread.run`, which has nowhere to deliver it.
- **A `QThread` has nowhere to deliver an exception.** `ThumbnailWorker.run`
  (`arrange_view.py:281-298`) records the consequence: Qt prints a traceback the
  user cannot act on and the view silently stays empty. Swallow, log, set
  `failed`.
- **Threads parented to the widget are never freed without
  `deleteLater`** (`preview_view.py:761-763`). Connect it, or scrubbing eight
  spinboxes leaks eight threads for the life of the window.
- **`stop_background_work` iterates a hard-coded pair of views**
  (`main.py:1160, 1168`). A third render source that is not on that list is the
  exact shutdown crash `_live_threads` was written for.
- **`_live_threads` reads `view._thread` first** because "tests inject a fake
  thread that is not a real `QObject`" (`main.py:243-245`). Expose
  `_thread`/`_worker` properties on the panel with those names, not
  `_composite_thread`, or shutdown will not see them.
- **`refresh_from_project` blocks signals on a hand-maintained widget list**
  (`layout_panel.py:1223-1231`, B12). The new checkbox and combo must go on it,
  or refreshing a project fires their handlers, which mutate and clear the redo
  stack.
- **`_on_unit_changed` is a second hand-maintained list** (B11). The composite
  controls carry no length, so they do not belong on it — but the crop
  *spinboxes* still do not either, and that is B11's bug, not this one's.
- **`python -m deckle` launches the GUI and blocks.** Use `python -m deckle.cli`
  for `crop-preview` comparisons.
