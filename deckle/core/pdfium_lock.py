"""The one lock every pdfium call in the process holds.

A module of its own because of who needs it. ``loader`` touches pdfium to
measure an imported document's pages, and reached the guard by importing
:mod:`deckle.core.render` -- which imports :mod:`deckle.core.export`,
which imports pikepdf and the printing module. Importing a PDF does not
depend on rendering or exporting one, and an import graph that says it
does is read as though it were true.

Nothing here imports pypdfium2. The lock is a :class:`threading.RLock`
and the rule about it is a rule about callers, so this module is three
lines of code and a page of reasons.

Qt-free, like everything under ``deckle.core`` -- see
``tests/test_core_purity.py``.
"""

from __future__ import annotations

import threading

_PDFIUM_LOCK = threading.RLock()
"""Serialises every pdfium call in the process.

**pdfium rendering is not thread-safe, and it does not fail politely.**
Two threads rasterizing at once produce ``OSError: exception: access
violation reading 0x0`` -- a native fault, not a Python exception -- which
takes the whole application down with no traceback and nothing in the log.
Measured at roughly one in thirty concurrent renders here, which is
exactly the frequency that reads to a user as "Deckle randomly closes".

Deckle reaches that state through ordinary use, not through an unusual
one. Both views that render do it on a background ``QThread``, and both
supersede a running job by setting its ``cancel`` flag and starting the
next thread **without waiting for the old one to stop**. The flag is
cooperative and is checked between steps, never inside a pdfium call, so
the outgoing render is still inside pdfium when the incoming one begins.
Scrubbing the preview does it; so does scrolling thumbnails while a
preview renders, since the two views hold independent threads.

An ``RLock`` rather than a ``Lock`` because these regions nest:
``render_sheet`` holds it across a document's lifetime and calls
``rasterize_page``, which takes it again on the same thread.

The cost is that renders no longer overlap. They contended for the same
cores anyway, and a superseded render drops out at its next checkpoint --
against a fault that ends the process, this is not a close trade.

**Every module that touches pdfium must hold this**, not only this one --
see :func:`pdfium_guard`. Guarding rasterization alone is not enough: an
*open* racing another thread's render faults just as readily.
"""


def pdfium_guard():
    """Hold while touching pdfium from anywhere in Deckle.

    pdfium is a single global library and its state is process-wide, so
    the rule cannot be per-module: every document open, page render and
    close has to be inside this, or the ones that are gain nothing from
    the ones that are not.

    Every caller is outside this module, and the *printing* one is the
    reason it is public rather than private. The print dialog runs no
    thread of its own, so a print rasterizes **on the GUI thread** -- and
    printing while the preview is still drawing is an entirely ordinary
    thing to do, with a background render in flight the whole time.

    Usable as a context manager::

        with pdfium_guard():
            doc = pdfium.PdfDocument(path)
            ...
            doc.close()

    Reentrant, so a guarded region may call another one on the same
    thread. Hold it across the document's whole life rather than around
    the render alone: opening while another thread renders faults too --
    measured, not assumed.

    :returns: the process-wide pdfium lock.
    """
    return _PDFIUM_LOCK
