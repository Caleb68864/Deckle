"""``python -m deckle.cli`` -- the headless entry point.

``deckle.cli`` used to be a single module and carried its own
``if __name__ == "__main__"`` block. A package cannot: ``runpy`` looks for
a ``__main__`` submodule instead, and without this file
``python -m deckle.cli`` fails with "'deckle.cli' is a package and cannot
be directly executed" -- which would break ``run.bat cli`` and every test
that shells out to the CLI.

``python -m deckle`` launches the GUI and blocks; this is the one to run
from a script or from CI.
"""

from __future__ import annotations

import sys

from deckle.cli import main

if __name__ == "__main__":
    sys.exit(main())
