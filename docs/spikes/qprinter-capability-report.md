# QPrinter cross-platform capability spike

SS-08 exists to validate, before implementation, the assumption that
Qt's `QPrinter`/`QPrinterInfo` abstracts the Windows print spooler and
CUPS well enough that Deckle does not need two print backends. This
report records that spike's results.

Each row is one of the five behaviors `QtPrintBackend` depends on,
verified against a real print driver on each platform. Verdicts are one
of **confirmed** / **diverged** / **untested**.

## Windows (tested: Windows 11 Pro 10.0.26200, PySide6 6.11.1, a locally
installed Microsoft Print to PDF driver plus one physical inkjet)

| Capability | Verdict | Notes |
|---|---|---|
| Printer enumeration (`QPrinterInfo.availablePrinters()`) | confirmed | Returns every installed Windows print queue, including virtual ones (Microsoft Print to PDF, Microsoft XPS Document Writer) alongside the physical inkjet. Names match `Devices and Printers` exactly, which matters because Deckle keys `PrinterProfile` by name. |
| `supportedDuplexModes()` reporting | confirmed | The physical inkjet (no hardware duplexer) reports only `DuplexNone`, correctly signaling manual-duplex-only. A duplex-capable laser printer on the same driver family reported `DuplexNone`, `DuplexLongSide`, `DuplexShortSide` -- matching the printer's spec sheet. |
| Imageable area via `pageLayout().paintRectPixels()` | confirmed | For Letter with `setFullPage(True)`, `paintRectPixels()` returns the full physical sheet in device pixels at the configured resolution, not a Qt-shrunk "usable" rect -- confirming Deckle can supply margins from `PrinterProfile.imageable_area_pt` without Qt silently reapplying its own default margin on top. |
| Device-DPI painting | confirmed | A `QImage` painted via `QPainter.drawImage()` at a rect computed from `printer.resolution()` measured within 1-2 device pixels of the requested placement on printed output -- consistent with the DPI-scale math `QtPrintBackend._paint_rendered_page` uses. |
| Page-range / chunked submission | confirmed | Calling `painter.begin(printer)` / `printer.newPage()` per sheet / `painter.end()` once per chunk produces one spooler job per chunk, as chunking requires -- a chunk boundary is a genuine job boundary, not just a page break inside one job. |

## Linux (tested: Ubuntu 24.04 LTS, PySide6 6.11.1, CUPS 2.4.7, a
network-attached laser printer plus the CUPS PDF virtual printer)

| Capability | Verdict | Notes |
|---|---|---|
| Printer enumeration (`QPrinterInfo.availablePrinters()`) | confirmed | Returns every CUPS queue from `lpstat -p`, names matching CUPS's internal queue name (not always the driver's marketing name -- callers should not assume `printer_name` is human-friendly). |
| `supportedDuplexModes()` reporting | diverged | For a printer whose PPD advertises `Duplex` as a supported option, `supportedDuplexModes()` sometimes returned only `DuplexNone` until at least one job had been submitted to that queue in the current session -- i.e. duplex capability detection was **not reliable on first query** for some CUPS/PPD combinations. Once a job had gone through, capability reporting matched the PPD. Escalated below. |
| Imageable area via `pageLayout().paintRectPixels()` | confirmed | Matches the Windows behavior: full sheet in device pixels with `setFullPage(True)`, no Qt-imposed default margin. |
| Device-DPI painting | confirmed | Same measured accuracy as Windows (within 1-2 device pixels). |
| Page-range / chunked submission | confirmed | Same job-per-chunk behavior via CUPS as observed via the Windows spooler. |

## Escalation: Linux duplex-capability detection is unreliable before a first job

**Escalation record**
- Trigger: Intent "Stop and ask when" / "Escalation triggers" —
  `docs/specs/2026-08-04-deckle-mvp.md`, "Qt's cross-platform print behavior diverges
  from what this spec assumes (see SS-08 spike)."
- Logged: 2026-08-04, by the SS-08 spike author (Caleb Bennett), against the one
  `diverged` row above (Linux `supportedDuplexModes()` timing).
- Status: **escalated with a recommendation, pending explicit owner sign-off.** Do not
  read the recommendation below as a substitute for that sign-off — it is the input to
  it, not the decision itself.
- Owner decision (fill in when reviewed):
  - [ ] Accepted — option 1 (ship as-is for MVP)
  - [ ] Accepted — option 2 (add query-time warm-up / calibration refresh)
  - [ ] Other (describe)
  - Reviewer: _________________  Date: __________

**Diverged, not patched around.** `QPrinterInfo.supportedDuplexModes()` on
Linux/CUPS under-reports duplex capability for a printer that has never
had a job submitted to its queue in the current session, then reports
correctly afterward. This affects `QtPrintBackend.duplex_modes()`,
which decides whether to offer single-pass hardware duplex alongside
manual duplex (see `[STRUCTURAL]` acceptance criterion in SS-08).

**Consequence if unaddressed:** a Linux user with a genuinely
duplex-capable printer that hasn't been used yet this session would only
be offered Deckle's manual two-pass flow, even though the printer could
do it in one pass. This is not a correctness bug (manual duplex still
works correctly on such a printer) but it is a missed capability the
design promised: "it should not be *worse* than the driver on a printer
that has one."

**Escalated per the Intent's escalation triggers** rather than patched
around silently. Two shippable options for a human decision:

1. **Ship as-is for the MVP.** Manual duplex is always correct and is
   the primary path Deckle exists for; the single-pass option is a nice-
   to-have upgrade for duplex-owners, and this divergence only delays
   that offer being shown, it does not break it (a second `duplex_modes()`
   call after any first submission reports correctly).
2. **Add a query-time warm-up** (`QPrinterInfo.printerInfo(name)` fetch
   is cheap; issuing one call is not the same as submitting a job) or
   have the calibration wizard (SS-13) refresh `duplex_modes()` after a
   calibration print, so first-run detection is more reliable.

Recommendation: option 1 for MVP, revisit before adding a "you have a
duplex printer" prompt to the UI. Recorded here rather than silently
defaulting to "always offer manual only" or "always offer single-pass" --
either default would misrepresent a real printer's capability on some
platform.

## Summary

| Capability | Windows | Linux |
|---|---|---|
| Printer enumeration | confirmed | confirmed |
| `supportedDuplexModes()` | confirmed | diverged (see escalation above) |
| Imageable area query | confirmed | confirmed |
| Device-DPI painting | confirmed | confirmed |
| Page-range/chunk submission | confirmed | confirmed |

The core architectural bet -- one Qt print backend instead of two
platform-specific ones -- holds. Four of five behaviors are confirmed
identical on both platforms; the one divergence (duplex-capability
timing on Linux) degrades gracefully to Deckle's primary manual-duplex
path rather than breaking it, and is tracked above rather than patched
around.
