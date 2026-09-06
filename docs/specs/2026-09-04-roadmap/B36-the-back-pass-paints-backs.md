# B36 — The back pass must paint backs, turned, with the registration offset

**Roadmap item:** `docs/ROADMAP.md` B36
**Depends on:** —
**Blocks:** F9 (hardware duplex reuses this seam), B4 (touches the same method)
**Size:** S
**Decision needed first:** none

---

## 1. Context

Printing a book from the desktop app prints **the fronts twice**.

`PrintSession` drives manual duplex: pass 1 feeds every sheet and prints its
front, the user reloads, pass 2 feeds every sheet and prints its back. The
session computes both passes correctly — `plan_passes` returns a `PrintPass`
per side carrying `side`, `rotate_backs` and a reload instruction, and
`test_printing.py` asserts all of it. Then `_submit_sheets` throws that away.
It calls the backend's `submit` with five positional arguments and no
keywords, so `side` takes its default `"front"`, `rotate_backs` its default
`False`, and `pass_index` its default `0` — on **both** passes.

Three consequences, in the order they cost paper:

1. **Pass 2 rasterises the front face of every sheet.** The user reloads
   sixty sheets and prints the fronts onto the backs.
2. **`rotate_backs` never reaches the painter.** Even if the face were
   right, a printer whose reload flips about the short edge needs the back
   turned 180°; it would print upside down.
3. **`back_offset_x_pt` / `back_offset_y_pt` are never applied.** The
   front/back registration offset is the project's headline feature, the one
   thing the README says no other tool can do. From the desktop app it does
   nothing.

The backend already has the correct entry point. `QtPrintBackend.submit_pass`
(`deckle/app/backend.py:402-470`) takes a `PrintPass`, chunks it, and threads
`side`, `rotate_backs` and `pass_index` into every `submit` call. Nothing in
`deckle/` calls it — only tests do. The method was written, tested, and never
wired up.

**Why no test caught it.** `PrintBackend` (the Protocol in
`deckle/core/printing.py:82-92`) declares only the five-argument `submit`.
The fake backend in `tests/test_print_session.py:73-80` mirrors that
signature exactly, so a session test cannot observe a `side` it never
receives. The backend tests call `submit_pass` directly and pass. Each half
is green about its own half; the seam between them is what is broken.

**Verify it on the unfixed tree:**

```bash
.venv/bin/python - <<'PYEOF'
import inspect
from deckle.core.print_session import PrintSession
from deckle.app.backend import QtPrintBackend
src = inspect.getsource(PrintSession._submit_sheets)
print(src)
print("submit_pass called anywhere in deckle/:",
      __import__("subprocess").run(
          ["grep","-rn","submit_pass","deckle/"],
          capture_output=True, text=True).stdout or "NO")
PYEOF
```

`_submit_sheets` shows a bare five-argument call, and the only `submit_pass`
hits under `deckle/` are its own definition and two comments.

---

## 2. Current code

`deckle/core/print_session.py:559-564` — the whole defect:

```python
    def _submit_sheets(self, pass_: PrintPass, sheets: list[int]) -> PrintResult:
        result = self.backend.submit(
            self.plan, sheets, self.printer_name, self.copies, self.dpi
        )
        log_print_job(self.printer_name, self.profile, sheets, self.dpi, pass_.index)
        return result
```

`pass_` is in scope. It carries everything needed. It is used only for the
log line.

`deckle/core/printing.py:82-92` — the Protocol that made the omission
invisible:

```python
class PrintBackend(Protocol):
    """A pluggable print submission target. Never imports Qt."""

    def submit(
        self,
        plan: SheetPlan,
        sheets: Sequence[int],
        printer_name: str,
        copies: int,
        dpi: int,
    ) -> PrintResult: ...
```

`deckle/core/printing.py:96-103` — what a pass already knows:

```python
class PrintPass:
    """One physical pass through the printer: an ordered set of sheets."""

    index: int
    sheet_order: list[int]
    side: Literal["front", "back"]
    reload_instruction: str
    rotate_backs: bool
```

`deckle/app/backend.py:293-302` — the real signature, with the defaults that
silently take over:

```python
    def submit(
        self,
        plan: SheetPlan,
        sheets: Sequence[int],
        printer_name: str,
        copies: int,
        dpi: int,
        *,
        side: Literal["front", "back"] = "front",
        rotate_backs: bool = False,
        pass_index: int = 0,
    ) -> PrintResult:
```

Its own docstring at `backend.py:307-311` says callers "can omit them and get
front-side, unrotated behavior" — written as a convenience for a caller that
wants fronts, and taken up by the caller that wants backs.

**Call sites.**

| Symbol | Where | Note |
|---|---|---|
| `PrintSession._submit_sheets` | `print_session.py:559` | the only production caller of `backend.submit` |
| `QtPrintBackend.submit` | `backend.py:293` | called by `submit_pass` (with keywords) and by the session (without) |
| `QtPrintBackend.submit_pass` | `backend.py:402` | **no production caller** |
| `PrintBackend` Protocol | `printing.py:82` | five arguments; the contract that hides the bug |
| fake backend `submit` | `tests/test_print_session.py:73` | five arguments; cannot observe `side` |
| `submit_pass` tests | `test_backend.py:80-141`, `test_hardening_printing.py:484-622` | 11 call sites, all direct |

**Chunking overlap.** `PrintSession` already chunks: it submits
`sheets_per_chunk` at a time and advances `sheet_cursor`, which is what makes
sheet-granular resume work. `submit_pass` chunks too. The session must
therefore keep its own chunking and pass the per-chunk keywords itself; it
must **not** delegate to `submit_pass`, which would submit the whole pass and
destroy resume granularity. That is the design choice below, and the reason
`submit_pass` stays uncalled by the session.

---

## 3. Change

**Chosen design:** widen the `PrintBackend` Protocol to carry the three
keywords, and have `_submit_sheets` pass them from the `PrintPass` it already
holds. **Rejected:** having the session call `submit_pass` — it would give up
per-chunk cursor advancement and with it resume at sheet granularity, which
the README names as the reason the project exists.

1. **`deckle/core/printing.py`**, in the `PrintBackend` Protocol
   (`:82-92`): add the three keyword-only parameters with the same defaults
   the concrete backend uses, so existing five-argument implementations
   still satisfy the Protocol structurally:

   ```python
   class PrintBackend(Protocol):
       """A pluggable print submission target. Never imports Qt."""

       def submit(
           self,
           plan: SheetPlan,
           sheets: Sequence[int],
           printer_name: str,
           copies: int,
           dpi: int,
           *,
           side: Literal["front", "back"] = "front",
           rotate_backs: bool = False,
           pass_index: int = 0,
       ) -> PrintResult: ...
   ```

   `Literal` is already imported in that module (it types `PrintPass.side`).
   Add to the docstring, on its own line: `Backends must honour ``side`` —
   a back pass that paints fronts is a ruined stack of paper.`

2. **`deckle/core/print_session.py`**, `_submit_sheets` (`:559-564`): pass
   the pass's own fields.

   ```python
   def _submit_sheets(self, pass_: PrintPass, sheets: list[int]) -> PrintResult:
       result = self.backend.submit(
           self.plan,
           sheets,
           self.printer_name,
           self.copies,
           self.dpi,
           side=pass_.side,
           rotate_backs=pass_.rotate_backs,
           pass_index=pass_.index,
       )
       log_print_job(self.printer_name, self.profile, sheets, self.dpi, pass_.index)
       return result
   ```

   Keep the `log_print_job` line exactly as it is. **B4 deletes it**; if B4
   has already landed, this step edits the call only.

3. **`deckle/core/print_session.py`**, `_submit_sheets` docstring: it has
   none. Add one, three lines, recording why the keywords are passed here
   rather than delegating to `submit_pass`:

   ```python
       """Submit one chunk of ``pass_``, on that pass's own side.

       The session chunks rather than calling ``backend.submit_pass``,
       because the cursor must advance per chunk for resume to land on a
       sheet. The side, turn and pass index therefore have to be threaded
       through by hand -- omitting them prints the fronts twice.
       """
   ```

4. **`tests/test_print_session.py`**, the fake backend at `:73-80`: widen
   its `submit` to accept and record the three keywords. Record them on each
   call entry so every existing session test keeps working and the new ones
   can assert. Keep the defaults, so the widening alone changes no existing
   assertion.

5. **`deckle/app/backend.py`**, `submit` docstring at `:307-311`: the
   sentence "can omit them and get front-side, unrotated behavior" invited
   this bug. Replace it with: `The defaults exist for a single-face job;
   any caller submitting a pass must pass all three, or a back pass paints
   fronts.`

6. **`deckle/app/backend.py`**: leave `submit_pass` in place, uncalled. It
   is F9's raw material and is covered by 11 tests. Do not delete it. Do not
   add a caller.

---

## 4. Tests

Write these first; all four fail on the unfixed tree.

1. **`tests/test_print_session.py::test_the_back_pass_asks_for_the_back_side`**
   Build a two-sheet plan and a session with the fake backend and the
   `generic_face_down_reversed` preset. Call `start()`, then `advance()`
   until `pass_index` is 1, then `advance()` again to submit the back pass.
   Assert the recorded call for the back pass has `side == "back"`.
   *Fails now with:* `AssertionError: assert 'front' == 'back'`.

2. **`tests/test_print_session.py::test_a_reversed_printer_turns_its_backs`**
   Same setup with a profile whose `flip_axis` makes `plan_passes` return
   `rotate_backs=True`; assert the back-pass call recorded
   `rotate_backs is True`, and that the front-pass call recorded `False`.
   *Fails now with:* `assert False is True`.

3. **`tests/test_print_session.py::test_each_pass_submits_under_its_own_index`**
   Assert the front-pass calls recorded `pass_index == 0` and the back-pass
   calls `pass_index == 1`.
   *Fails now with:* `assert 0 == 1`.

4. **`tests/test_backend.py::test_the_protocol_carries_the_side`**
   A structural test that closes the seam permanently: assert
   `inspect.signature(PrintBackend.submit).parameters` contains `side`,
   `rotate_backs` and `pass_index`, and that
   `inspect.signature(QtPrintBackend.submit)` agrees with it on those three
   names and their defaults. This is what stops the Protocol and the backend
   drifting apart again.
   *Fails now with:* `KeyError: 'side'`.

Do **not** add a rasterisation test here. Whether `side="back"` paints the
back face is already asserted by `tests/test_print_painting.py` against
`_render_sheet_side`; this spec is about the argument reaching it.

---

## 5. Acceptance

| Check | Command |
|---|---|
| The four new tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_print_session.py -k "back_side or turns_its_backs or own_index" tests/test_backend.py -k "protocol_carries"` |
| The session passes the side | `grep -n 'side=pass_.side' deckle/core/print_session.py` |
| The Protocol declares it | `grep -n 'side: Literal\["front", "back"\] = "front"' deckle/core/printing.py` |
| No bare five-argument submit remains in the session | `! grep -n 'self.dpi$' deckle/core/print_session.py` |
| `submit_pass` still exists and is still uncalled | `grep -n 'def submit_pass' deckle/app/backend.py && ! grep -rn 'backend.submit_pass\|self.submit_pass' deckle/` |
| Full suite | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |
| `[HUMAN]` Print a four-sheet job on scrap. Pass 2 must show pages 2, 4, 6, 8 the right way up on the backs of 1, 3, 5, 7. | — |

The human row is the only check that proves the whole path. Every mechanical
row above can be green while the printer still produces a ruined stack, for
the same reason the folded dummy exists.

---

## 6. Out of scope

- **B4** (`log_print_job` called twice) edits the same four lines. Land B4
  first or this one first, not both at once; whichever is second rebases
  onto a `_submit_sheets` that has already changed.
- **B6** (the sheet is scaled into the imageable area rather than drawn 1:1)
  is a different defect in the same print path and needs an owner decision.
- **F9** (hardware duplex) is the reason `submit_pass` survives. Not here.
- The `back_offset` arithmetic itself is already correct and tested in
  `tests/test_registration.py`; this spec only makes it reachable.

---

## 7. decisions.md entry

```
## 2026-09-05 — The back pass was printing fronts, and no test could see it
- Symptom: PrintSession._submit_sheets called backend.submit with five positional
  arguments, so side, rotate_backs and pass_index took their front-side defaults on
  both passes. The desktop app's pass 2 rasterised the front of every sheet,
  unturned, with the measured back offset unapplied. QtPrintBackend.submit_pass,
  which threads all three, had no caller anywhere in deckle/.
- Fix: widened the PrintBackend Protocol to carry the three keywords and passed them
  from the PrintPass the session already holds. The session keeps its own chunking:
  delegating to submit_pass would submit a whole pass and give up the per-chunk
  cursor that makes resume land on a sheet.
- Surfaces: deckle/core/printing.py (Protocol), deckle/core/print_session.py
  (_submit_sheets), deckle/app/backend.py (docstring), tests/test_print_session.py
  (fake backend widened, three tests), tests/test_backend.py (signature parity test).
- Watch: the fake backend mirrored the narrow Protocol exactly, which is why eleven
  green submit_pass tests and a full session suite both passed over a book that would
  print wrong. A Protocol narrower than its only implementation hides the seam
  between them; the new signature-parity test is what keeps the two in step.
- Commit: <fill in>
```

---

## 8. Traps

- **`python -m deckle` launches the GUI and blocks.** Use
  `python -m deckle.cli` for anything scripted.
- **Do not "simplify" by calling `submit_pass` from the session.** It chunks
  internally and returns once per pass. The session's `sheet_cursor` must
  advance per chunk or resume goes back to pass granularity, which is the
  behaviour the project was built to replace. The docstring added in step 3
  is what stops the next reader from making that edit.
- **The fake backend widening is load-bearing.** If step 4 is skipped, the
  new tests fail with `TypeError: submit() got an unexpected keyword
  argument 'side'` rather than the assertion they are written for, which
  reads like a bug in the fix.
- **`Literal` must be imported** in `printing.py` for step 1. It already is;
  confirm rather than assume, because a missing import here fails at import
  time and takes the whole CLI with it.
- **The CLI is not affected.** `deckle export --pass back` goes through
  `export()` directly and already passes `side`; only the desktop session
  path is broken. Do not "fix" the CLI to match.
- Eleven tests call `submit_pass` directly and must keep passing untouched.
  If a change of yours requires editing them, you have changed `submit_pass`,
  which this spec does not ask for.
