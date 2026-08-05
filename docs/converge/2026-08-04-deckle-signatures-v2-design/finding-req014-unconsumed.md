# FROZEN FINDING — REQ-014's hash is correct but unconsumed

**Status:** `requires-human-review`. Not fixed in this converge run, by the
spec's own scope rule. Excluded from the clean-streak calculation.
**Score:** Partial — defined-but-unwired.

## What SS-04 asked for, and got

SS-04 required `_hash_plan` to become content-aware so that two page orderings
over the same sheet count hash differently. `fixB` delivered that: the payload
now carries the full ordered sequence of `source_ref.page_index` per side, and
all four of SS-04's `[MECHANICAL]` criteria now exit 0, including
`hash_plan_distinguishes_absent_side_from_present_side`, which had never been
written despite SS-04 being marked `complete`.

## The harm SS-04 cites

> A `PrintSession` persists that hash and a resumed session could bind to a
> document whose content has changed underneath it — the user reloads the stack
> and prints the wrong backs onto the right fronts.

## Why that harm is still live

`plan_hash` is **computed, persisted, and never compared.**

- `_hash_plan(plan)` is called once, at `print_session.py:198`, and its only
  other use is as an ingredient of `session_id` (`print_session.py:201`).
- `PrintSession.load(cls, plan, profile, backend, session_id)` accepts a
  **fresh plan**, reads the state file, and wires the session straight up:
  `session._passes = plan_passes(plan, profile, sheets=state.sheets)`. It never
  evaluates `_hash_plan(plan) == state.plan_hash`.
- `SessionSummary` does not even carry `plan_hash`, so `list_resumable()` cannot
  surface a mismatch to a picker UI either.

Net effect: you can re-impose a document, resume a session recorded against the
*old* imposition, and the session will print the *new* plan's content at the old
sheet cursor — precisely "the wrong backs onto the right fronts". Making the hash
content-aware did not close this, because no code path asks the hash anything.

## Why it was not fixed here

SS-04 scopes itself explicitly:

> `print_session.py` changes in **exactly one place** — `_hash_plan` — and
> `PrintSession`'s public surface and behaviour are unchanged. Anything beyond
> that one function is an escalation.

Adding a guard to `load()` changes `PrintSession`'s runtime behaviour: a resume
that silently succeeds today would begin to refuse. That is a product decision
about what should happen to a stale session — fail hard, warn and continue, or
offer re-imposition — and it is not the converge loop's call to make silently.

## Recommended resolution (for the author)

Smallest change that serves the intent: in `PrintSession.load`, compare
`_hash_plan(plan)` to `state.plan_hash` and raise a distinct, catchable error on
mismatch. Then decide at the call site in `deckle/app/views/print_dialog.py`
(which already owns the resume-offer prompts as injectable callables) how to
present it. Adding `plan_hash` to `SessionSummary` would additionally let
`list_resumable()` mark stale sessions in the picker before the user commits.

Worth a `STATE_VERSION` bump to 2 at the same time: this run changed the hash
payload keys (`front_page` → `front_pages`), so every pre-existing session's
stored hash would mismatch a recomputed one. Harmless while nothing compares
them; the moment a comparison exists, every old session would read as "document
changed" rather than "written by an older Deckle".
