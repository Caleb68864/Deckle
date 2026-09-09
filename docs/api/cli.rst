deckle.cli -- the headless command line
=======================================

.. automodule:: deckle.cli
   :members:
   :show-inheritance:

Reading order
-------------

The modules below are listed in the order one invocation travels through
them: the flags are declared (:mod:`~deckle.cli.options`), each flag's
text becomes a value (:mod:`~deckle.cli.values`), a subcommand does the
work (:mod:`~deckle.cli.commands`), and whatever it noticed is said out
loud (:mod:`~deckle.cli.report`). They import in that one direction and no
other, which is what keeps ``--help`` describable by reading one file.

.. toctree::
   :maxdepth: 1

   cli.options
   cli.values
   cli.commands
   cli.report
