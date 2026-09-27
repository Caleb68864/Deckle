# Step 02 — Import durable, individually named image assets

Dependencies: 01. Requirements: R01, R02, R07.

## Read first

architecture.md; `deckle/core/loader.py` (especially IMAGE_EXTENSIONS, _scan_image_dir, _single_image_pdf_bytes, import_store_dir); `core/paths.py`; `tests/test_loader.py`, `test_hardening_io.py`, `test_atomic_writes.py`.

## Deliverable and files

Create `deckle/core/card_import.py` and `tests/test_card_import.py`. Extract a reusable loader helper only when needed; keep load_image_dir behavior unchanged for books. Public API: `import_card_images(paths: Sequence[str]) -> CardImportResult`, and `import_card_folder(path: str) -> CardImportResult`. Define frozen CardImportResult here with `assets: tuple[CardAsset,...]`, `warnings: tuple[LayoutWarning,...]`. Return one asset per file, in deterministic natural filename order.

## Work sequence

1. Reuse IMAGE_EXTENSIONS and directory scanning policy. Scan direct child files only; ignore subdirectories. Surface existing skipped-non-image advisories. Empty selections/folders fail with a useful message. Explicitly selected unsupported files fail rather than disappearing.
2. Sort using natsort, with a deterministic case-sensitive full-path tie breaker for otherwise equal natural keys. Deduplicate identical resolved paths within a request. Distinct files with identical pixels remain distinct assets and filenames.
3. Validate each image before committing deck state. Honor EXIF orientation exactly once. Reject multi-frame/animated sources (`n_frames > 1`) rather than assigning one filename to several cards. Retain current DPI fallback handling; dimensions will be fitted to physical card size later.
4. Convert each image to a one-page PDF using existing lossless conversion where supported; use existing Pillow fallback only when conversion requires it. Normalize transparency consistently so the exported art white background defined in architecture remains authoritative. Never mutate the source image.
5. Cache each converted PDF independently in the durable import store using content-derived names prefixed `card-`. Atomic write, validate one page, then obtain SourcePage via the PDF loader. A source asset must never point at a temporary directory that is removed when the dialog closes. Reuse a valid existing conversion without rewriting it; a corrupt cached conversion must be rebuilt atomically.
6. Record the absolute original path, original filename including extension, original file SHA-256, converted SourceRef, and a newly generated asset ID. Detect changes during reading/conversion (hash before and after; fail on mismatch) so provenance and artwork cannot refer to different versions.
7. Import is transactional at the deck level: any bad supported image rejects the entire import result and names the failing file. Durable content-addressed cache files created along the way may remain harmlessly; no partial deck or half-valid asset list is returned. GUI step 07 commits only a completed result.

## Acceptance tests

Use generated small Pillow images in pytest tmp_path; do not add licensed sample art.

- `card1.png`, `card2.png`, `card10.png` retain that order and distinct original names; mixed-case collisions have deterministic order.
- One shared image, multiple selected images from different folders, JPEG, PNG transparency, and EXIF-rotated JPEG work. An unrotated dimension assertion and raster landmark prove EXIF was applied once.
- Non-image directory entries warn; corrupt supported image fails the batch; missing/unreadable input names the file; multi-page TIFF/animated WebP gives an explicit unsupported-frame message.
- Import twice reuses converted bytes; two identical images with different names remain independently pairable. Source files remain byte-identical.
- After deleting/moving original images, imported PDF references still export. Deleting the converted PDF yields the normal missing-source error later.
- Simulate conversion failure, interrupted cache write, corrupt cache, and a source change during import. No half-written PDF is published.
- Book load_image_dir ordering, warnings, and merged-source behavior remain unchanged.

## Gate

Run new import tests and existing loader/atomic-write tests. Inspect that each CardAsset label names the original image, not `card-<hash>.pdf`. This is essential for filename matching in step 03.
