"""Qt-facing application package.

Everything under ``deckle.app`` may import PySide6. ``deckle.core`` must
not -- see ``tests/test_core_purity.py``. ``deckle.app.backend`` is the
only place that submits print jobs to a real printer via ``QPrinter``.
"""

from __future__ import annotations
