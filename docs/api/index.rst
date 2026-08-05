Deckle API reference
====================

Deckle imposes and prints booklets from PDF and image sources. This
reference is generated from the source tree by ``sphinx.ext.autodoc``, so
it is the docstrings themselves -- there is no second, hand-maintained copy
to fall out of step with the code.

The narrative documents live elsewhere and are hand-written:
``docs/decisions.md`` records why things are the way they are,
``docs/specs/`` holds the specifications, ``docs/plans/`` the plans, and
``docs/converge/`` the convergence records. None of them are part of this
build.

The shape of the codebase
-------------------------

Two packages, one hard boundary:

``deckle.core``
   Pure model, layout arithmetic, and file/print plumbing. **No Qt.**
   ``tests/test_core_purity.py`` imports every module in this package and
   fails if any of them pulls a Qt binding into ``sys.modules``. That is
   what lets the CLI, the regression fixtures, and this documentation
   build all run headlessly.

``deckle.app``
   Everything Qt-facing: the window, the views, and the one print backend
   that talks to a real printer. May import PySide6 -- but does so lazily,
   inside the functions that need it, so importing a view module still
   costs nothing and needs no display server.

``deckle.cli`` sits above ``deckle.core`` alone and never touches
``deckle.app``.

.. toctree::
   :maxdepth: 2
   :caption: Contents

   core
   cli
   app

Indices
-------

* :ref:`genindex`
* :ref:`modindex`
