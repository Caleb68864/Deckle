"""Pure data models and layout logic. No I/O, no Qt.

Every module in this package must be importable without pulling in PySide6,
PyQt, pikepdf, or any other I/O-performing dependency. See
``tests/test_core_purity.py`` for the enforcing test.
"""
