# N9 — Dirty indicator and save prompts (see B10)

**Roadmap item:** `docs/ROADMAP.md` N9
**Specified in:** [`B10-N9-dirty-and-save-prompts.md`](B10-N9-dirty-and-save-prompts.md)

---

N9 and B10 are one piece of work. The roadmap says so itself: N9's entry reads
*"Dirty indicator + save prompts (`setWindowModified`, "save before closing /
opening?"). **This is B10 seen as a feature.**"*

There is no separate implementation, no separate test file and no separate
`docs/decisions.md` entry. Implementing B10 satisfies N9 in full:

| N9 asks for | B10 delivers it in |
|---|---|
| `setWindowModified` | §3.5, steps 19-21 |
| `[*]` in the window title | §3.5, step 19 |
| "save before closing?" | §3.4, step 10 |
| "save before opening?" | §3.4, step 12 |
| (also) "save before importing?" | §3.4, steps 14-16 |
| a definition of "dirty" | §3.1 |

Do not open a second branch or a second commit for N9. Close both roadmap
rows with the one change.
</content>
