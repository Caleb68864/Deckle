deckle.cli -- the headless command line
=======================================

.. automodule:: deckle.cli
   :members:
   :show-inheritance:

Reading order
-------------

Each flag's text becomes a value in :mod:`~deckle.cli.values`, a
subcommand does the work in :mod:`~deckle.cli.commands`, and whatever it
noticed is said out loud by :mod:`~deckle.cli.report`.

.. toctree::
   :maxdepth: 1

   cli.values
   cli.commands
   cli.report
