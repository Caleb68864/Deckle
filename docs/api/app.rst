deckle.app -- the Qt application
================================

.. automodule:: deckle.app

Every module here follows the same discipline: the plain-Python logic sits
at the top of the file and the Qt widgets at the bottom, with PySide6
imported *inside* the functions that need it. That is why importing any of
these modules -- which is exactly what this documentation build does --
requires neither PySide6 nor a display server.

.. toctree::
   :maxdepth: 1

   app.state
   app.menus
   app.main
   app.backend
   app.printer_capabilities
   app.shutdown
   app.views.import_view
   app.views.arrange_view
   app.views.layout_panel
   app.views.preview_view
   app.views.print_dialog
