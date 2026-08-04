# Deckle

Deckle is a desktop application for imposing and printing booklets from PDF
and image sources. It takes a stack of source pages, arranges them into
printer sheets according to a chosen binding and paper layout, and produces
a print-ready output — without needing a commercial print shop or a
dedicated imposition tool.

## Status

**Not yet released.** Deckle is under active development and does not yet
have a packaged build. Expect breaking changes.

## The page model

Deckle's core reasons about pages at four distinct levels, and keeps them
strictly separate so layout logic stays pure and testable:

1. **Source page** — a page as it exists in an input file (PDF page or
   image), before any placement decision is made.
2. **Output page** — a source page (or a blank filler) after it has been
   assigned a placement transform (scale, translation, rotation) for a
   specific position on a sheet.
3. **Sheet** — one physical piece of paper, with an optional front and
   back output page, ready to be printed as a duplex or single-sided pass.
4. **Pass** — the ordered sequence of sheets sent to the printer to
   produce the finished, foldable/bindable booklet.

This separation lets the imposition logic (which pages go where) be
implemented as pure functions over these data models, independent of PDF
I/O, rasterization, or the Qt-based UI.

## License

Deckle is released under the [MIT License](LICENSE).
