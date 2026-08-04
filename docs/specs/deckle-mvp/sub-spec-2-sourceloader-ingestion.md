---
type: phase-spec
master_spec: "../2026-08-04-deckle-mvp.md"
sub_spec_id: SS-02
sub_spec_number: 2
title: "SourceLoader — PDF and image ingestion"
depends_on: ['SS-01']
date: 2026-08-04
---

# SS-02 — SourceLoader: PDF and image ingestion

## Context

Turn files on disk into `SourcePage` records. Two properties matter more than anything else
here:

1. **Import is metadata-only.** No rasterization, no page copying. A 300-page PDF must open
   in under 2 seconds. Everything downstream is lazy.
2. **Images are normalized to PDF pages at ingestion**, via `img2pdf`. Nothing downstream
   ever sees an image. This collapses what would otherwise be two parallel code paths
   through the imposer, rasterizer, and exporter.

**Read before writing image code:** `Caleb's Vault/Software/img2pdf/` — particularly
`img2pdf - DPI and Physical Page Sizing`, `img2pdf - EXIF Orientation Handling`, and
`img2pdf - Direct Embedding of JPEG and JPEG2000`. These are verified findings, not
documentation summaries.

**Why img2pdf and not pikepdf for this:** pikepdf's `Canvas.draw_image` stores
*uncompressed* samples — measured at ~12.6× the size of the same image as JPEG. pikepdf's
own documentation recommends img2pdf by name. See
`[[pikepdf - Images In and Out]]`.

## Provides

| Symbol | Consumed by |
|---|---|
| `load_pdf(path) -> list[SourcePage]` | SS-07, SS-09, SS-14 |
| `load_image_dir(path) -> list[SourcePage]` | SS-07, SS-09, SS-14 |
| `EncryptedPdfError` | SS-09, SS-14 |

## Requires

- `SourcePage`, `SourceRef`, `LayoutWarning` from SS-01.

## Implementation Steps

### Step 1. Write failing natural-sort test

`tests/test_loader.py`: build a temp directory with `img1.jpg`, `img2.jpg`, `img10.jpg`;
assert `load_image_dir` returns them in order 1, 2, 10.

This is the single most commonly botched behavior in image-to-PDF tools. Test it first.

### Step 2. Run and confirm failure

```bash
python -m pytest tests/test_loader.py -q
```

### Step 2b. Three verified img2pdf requirements — read before writing the converter

Each is a measured failure mode from `[[img2pdf Research Hub]]`, not a style preference:

1. **`rotation=img2pdf.Rotation.ifvalid` is mandatory.** EXIF Orientation 0 — common from
   phones and scanners — otherwise raises and **kills the entire batch**, not just the
   offending image.
2. **Missing DPI silently defaults to 96.0, not 72.** Unhandled, this is a
   wrong-size-in-print hazard that survives all the way to paper. Detect absent DPI
   explicitly and apply the fit-to-page fallback rather than inheriting 96.0.
3. **Never let Pillow write the PDF.** A q95 JPEG round-tripped through Pillow's
   `PdfImagePlugin` was measured to change **68% of pixels**, and `quality='keep'` does not
   help. Pass original bytes to img2pdf; materialize through Pillow only for pages that
   genuinely need pixel work.

Also: **img2pdf always centers images with symmetric borders, so the gutter cannot come from
it.** Gutter placement happens later, in SS-03/SS-04.

### Step 3. Implement image loading

Create `deckle/core/loader.py`. `load_image_dir`:

1. Glob the directory for image extensions.
2. Order with `natsort.natsorted`.
3. Convert the whole set to one PDF via `img2pdf.convert(...)`, written into a cache
   directory under the OS temp dir.
4. Return `SourcePage` records whose `SourceRef` points at that cache PDF with the right
   `page_index`.

Honor EXIF orientation and infer physical size from DPI metadata. Where DPI is absent, fall
back to fit-to-page. Where DPI **varies across the set**, emit a `LayoutWarning` of kind
`mixed_dpi`.

### Step 4. Green on natural sort, then add EXIF test

Assert an image with EXIF orientation 6 produces an upright page.

### Step 5. Write failing PDF-loading tests

Assert `load_pdf` returns one `SourcePage` per page, each `SourceRef` carries a `sha256` of
the file, and **no rasterization occurs** — patch the pypdfium2 render entry point and
assert zero calls.

### Step 6. Implement PDF loading

Use `pikepdf.Pdf.open` for page count and geometry only. Do not copy page content. Keep the
handle lifetime short — open, read metadata, close.

**Populate `SourceRef.width_pt` and `height_pt` from each page's own media box.** Both
loaders must do this. `Imposer` (SS-03) depends on it and is forbidden from reading it
itself — this loader is the only place that geometry enters the system.

**Trap:** `page.mediabox` returns a `pikepdf.Array`, not a `Rectangle`. `.width` raises.
Always wrap: `Rectangle(page.mediabox)`. See `[[pikepdf - Page Boxes and Geometry]]`.

### Step 7. Handle encrypted and corrupt input

Raise `EncryptedPdfError` carrying the path rather than leaking a library exception. For
corrupt files, attempt pikepdf's recovery pass; if it fails, name the offending page and
import the remainder.

### Step 8. Performance test

Assert `load_pdf` on a 300-page PDF completes under 2 seconds.

### Step 9. Full suite green, then commit

```bash
python -m pytest -q
git add -A && git commit -m "feat(SS-02): SourceLoader for PDF and image ingestion"
```

## Interface Contracts

### load_pdf
- Direction: SS-02 → SS-07, SS-09, SS-14
- Owner: SS-02
- Shape: `load_pdf(path: str) -> list[SourcePage]`. Metadata-only; raises
  `EncryptedPdfError` on password-protected input.

### load_image_dir
- Direction: SS-02 → SS-07, SS-09, SS-14
- Owner: SS-02
- Shape: `load_image_dir(path: str) -> list[SourcePage]`. Naturally sorted, EXIF-corrected,
  normalized to a cache PDF via img2pdf.

## Verification Commands

```bash
python -m pytest tests/test_loader.py -q
python -m pytest tests/test_core_purity.py -q
```

## Checks

| Criterion | Type | Command |
|---|---|---|
| load_pdf exposed | [STRUCTURAL] | `grep -q "def load_pdf" deckle/core/loader.py \|\| (echo "FAIL: load_pdf missing" && exit 1)` |
| load_image_dir exposed | [STRUCTURAL] | `grep -q "def load_image_dir" deckle/core/loader.py \|\| (echo "FAIL: load_image_dir missing" && exit 1)` |
| natsort used for ordering | [MECHANICAL] | `grep -q "natsorted" deckle/core/loader.py \|\| (echo "FAIL: natural sort not used" && exit 1)` |
| img2pdf used for image conversion | [MECHANICAL] | `grep -q "img2pdf" deckle/core/loader.py \|\| (echo "FAIL: img2pdf not used" && exit 1)` |
| EncryptedPdfError defined | [STRUCTURAL] | `grep -q "class EncryptedPdfError" deckle/core/loader.py \|\| (echo "FAIL: EncryptedPdfError missing" && exit 1)` |
| No image handling leaks downstream | [MECHANICAL] | `! grep -rn "PIL\|Image.open\|img2pdf" deckle/core/layout.py deckle/core/export.py 2>/dev/null \|\| (echo "FAIL: image code leaked past the loader" && exit 1)` |
| Loader tests pass | [MECHANICAL] | `python -m pytest tests/test_loader.py -q \|\| (echo "FAIL: loader tests failed" && exit 1)` |
