"""GUI entry point: ``python -m deckle`` launches ``MainWindow``.

``deckle/cli.py`` remains the headless entry point (``python -m deckle.cli``
/ the ``deckle`` console script) -- two entry points, one Qt-free core. This
module is the only place that starts the Qt event loop for the desktop app.
"""

from __future__ import annotations

import sys

from deckle.app.main import main

if __name__ == "__main__":
    sys.exit(main())
