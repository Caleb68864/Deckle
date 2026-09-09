deckle.core -- the Qt-free core
===============================

.. automodule:: deckle.core

Reading order
-------------

The modules below are listed roughly in the order a page travels through
them: it is described (:mod:`~deckle.core.models`), loaded off disk
(:mod:`~deckle.core.loader`), imposed onto sheets
(:mod:`~deckle.core.layout`, :mod:`~deckle.core.signatures`,
:mod:`~deckle.core.marks`), written out or rasterized
(:mod:`~deckle.core.export`, :mod:`~deckle.core.render`), persisted
(:mod:`~deckle.core.project_io`), and finally printed
(:mod:`~deckle.core.printing`, :mod:`~deckle.core.profiles`,
:mod:`~deckle.core.print_session`). :mod:`~deckle.core.report` describes
any of that as JSON for the CLI's ``--json`` flags, and the two log
modules sit underneath all of it.

.. toctree::
   :maxdepth: 1

   core.models
   core.loader
   core.layout
   core.signatures
   core.marks
   core.export
   core.plan_digest
   core.render
   core.dummy
   core.locate
   core.paths
   core.schema
   core.paper
   core.project_io
   core.outputs
   core.printing
   core.recent
   core.profiles
   core.print_session
   core.schedule
   core.report
   core.session_log
   core.diagnostics
