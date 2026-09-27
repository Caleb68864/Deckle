# Architecture and binding contracts

## Repository facts and integration approach

Deckle is Python 3.11+, with PySide6 in `deckle/app` and Qt-free planning in `deckle/core`. `models.Side.pages` already supports multiple `OutputPage` values. `SheetPlan` is consumed by PDF export, preview, pass planning, and print sessions. Reuse that pipeline; implement a separate card planner instead of adapting booklet page arithmetic.

Existing seams to inspect:

| Concern | Existing source | Required extension |
|---|---|---|
| Value models | `deckle/core/models.py` | Append backward-compatible fields and card value objects |
| Image ingestion | `deckle/core/loader.py` | Reuse converters; retain per-image names and durable references |
| Book imposition | `deckle/core/layout.py` | Preserve strategies; add project-level dispatch in new `planning.py` |
| PDF placement | `deckle/core/export.py` | Optional card background and sheet-space artwork clip |
| Cache/session identity | `deckle/core/plan_digest.py` | Hash every new rendered property |
| Storage | `deckle/core/project_io.py`, `schema.py` | Version 2 card payload and source traversal |
| Undo/autosave | `deckle/app/state.py` | Whole-project snapshots, card-aware source/recovery helpers |
| Desktop | `main.py`, `project_actions.py`, `exporting.py`, `menus.py`, `views/*` | Card workspace and kind-aware routing |
| Printing | `core/printing.py`, `print_session.py`, `app/backend.py` | Consume existing SheetPlan and pass contracts |
| CLI | `deckle/cli/{commands,options,values}.py` | Keep complete Project through dispatch |

Do not add runtime dependencies. Use Pillow, img2pdf, pikepdf, and pypdfium2 already declared. Keep core Qt-free, preserve atomic writes, and reuse the pdfium lock and cache budgets. This specification does not authorize implementation now.

## Project and asset contract

Append `Project.kind: Literal['book','cards'] = 'book'` and `Project.card_deck: CardDeck | None = None`. Existing three-argument constructors remain valid. Book projects have no deck; card projects have `pages=[]` and a deck. `Project.layout.paper` remains the single sheet-size authority, including orientation. Book-only layout settings remain inert in card mode.

Add frozen dataclasses in models.py; all new collections are tuples:

| Type | Fields / exact defaults |
|---|---|
| `CardAsset` | `id: str`, `label: str` (original filename), `original_path: str`, `original_sha256: str`, `page: SourcePage` (durable one-page imported PDF, rotate 0, skipped false) |
| `CardFace` | `asset_id: str`, `rotate_deg: int = 0` (0/90/180/270 clockwise) |
| `CardEntry` | `id: str`, `front: CardFace`, `back: CardFace | None = None`, `copies: int = 1`, `skipped: bool = False` |
| `CardSettings` | `width_pt=180.0`, `height_pt=252.0`, `margin_x_pt=18.0`, `margin_y_pt=18.0`, `gap_x_pt=0.0`, `gap_y_pt=0.0`, `border_enabled=True`, `border_width_pt=9.0`, `bleed_pt=3.0`, `fit: Literal['contain','cover']='contain'`, `cut_marks=False` |
| `CardDeck` | `assets: tuple[CardAsset,...]`, `cards: tuple[CardEntry,...]`, `settings: CardSettings`, `back_mode: Literal['shared','individual']='individual'`, `shared_back: CardFace | None = None` |
| `CardDecoration` | `card_id: str`, `copy_index: int` (zero based), `trim_rect_pt`, `art_rect_pt`, `bleed_rect_pt` (each x,y,w,h tuple), `black_border: bool` |

Append `OutputPage.card: CardDecoration | None = None`. When present, it describes actual ink and clipping in sheet coordinates. No settings lookup is needed by the exporter. Every active card face has this decoration, even with its border disabled. Unused cells are filler pages without decoration. Existing crop semantics remain unchanged; cards use `crop_pt=None` and the new art clip.

IDs are generated once at import/new-card creation using UUID hex strings and are persisted. IDs are references, never filename positions. Copies use the same entry ID plus copy index. Reordering or replacing a face must not regenerate unrelated IDs. Original paths and hashes describe import provenance. Only the durable converted PDF is a live required source; editing an original image does not silently change saved artwork. Explicit reimport creates a new asset.

## Back selection and pairing

In shared mode, every active entry resolves its back to `shared_back`; stored entry.back mappings are retained but inactive. In individual mode entry.back is authoritative. `None` means unresolved, not intentionally blank. Unresolved active cards block PDF export and print. Skipped cards may remain unresolved. A shared face rotation applies to all shared backs; individual mode allows different back rotations.

Changing modes preserves the individual map. “Use shared back for all pairs” is a separate explicit bulk action which materializes shared_back into each entry.back before selecting individual mode. Duplicate copies remain adjacent to their entry; changing order moves entire entries and never sorts backs independently.

## Planning API and validation levels

New `core/cards.py`: `validate_card_deck(deck)` checks structural/value errors; `card_output_problems(deck)` returns a tuple of human-readable unresolved/empty output reasons; `resolve_card_back(deck, entry)` returns CardFace or None; `impose_cards(deck, paper_pt)` returns SheetPlan or raises ValueError with an actionable reason.

Expose frozen `CardGrid` from card_layout.py with `columns`, `rows`, `capacity` (integers), `origin_pt: tuple[float,float]`, and `cell_pt: tuple[float,float]`. Public `card_grid(settings, paper_pt) -> CardGrid` validates settings/paper and computes the geometry without requiring any assets. It is the single capacity helper used by planning, UI, and info reports. Total sheet count is ceil(active copies / capacity); zero active copies gives zero sheets in reports and remains invalid for output.

New `core/planning.py`: `impose_project(project)` dispatches cards or existing book strategies; `project_has_content(project)` is true for any stored card entries (including skipped/unpaired), or existing book pages; `project_source_pages(project)` returns book pages or unique asset pages, including currently unused assets, for persistence/source protection. Source identity deduplication uses path plus page index. UI save eligibility is distinct from output readiness.

An incomplete deck is valid editable/savable state. Only output requires at least one non-skipped card, resolved backs, valid assets, and a grid containing at least one cell. Structural errors never produce a partial plan. UI catches ValueError, removes stale output availability, and shows the reason.

## Physical geometry (normative)

Use points: 72 pt/in; mm conversion is 72/25.4. PDF origin is lower left. `paper_pt=(W,H)` is already oriented. Let card dimensions be w,h; border width b is the saved border_width_pt if enabled, else 0; bleed e is saved bleed_pt if enabled, else 0. Artwork is inset b on all edges **inside** the finished card. Black extends e **outside** the finished card. Border off suppresses both black and bleed and preserves saved values for the next toggle. No physical card size changes when toggled; packing capacity can change because bleed changes.

The black fill rectangle is `(x-e,y-e,w+2e,h+2e)`. The art clip is `(x+b,y+b,w-2b,h-2b)`. First fill black (when on), then fill the art rectangle white, then paint the image clipped to that rectangle. White under the image makes transparent artwork and contain-mode letterboxing predictable. Black width is a minimum; contain mode can leave additional white space inside the art rectangle. Cover mode crops artwork centrally and must be named “Fill (crop)” in the UI.

Cut marks, when enabled, reserve m=3 pt additional clearance on each edge, independently of border. Otherwise m=0. Let p=e+m, cell dimensions cw=w+2p, ch=h+2p. Gaps are additional blank space between allocated cells, not between artwork edges. Require W>2mx and H>2my. Columns C=floor((W-2mx+gx)/(cw+gx)); rows R=floor((H-2my+gy)/(ch+gy)). Reject C<1 or R<1. Floating comparisons at exact fits may use at most 1e-7 pt tolerance; never round user dimensions to integers.

Used grid dimensions Gw=C*cw+(C-1)*gx, Gh=R*ch+(R-1)*gy. Center the grid on paper: x0=(W-Gw)/2, y0=(H-Gh)/2. The chosen margins are minimum symmetric margins. Fill row-major from top left. For row r, column c, front trim origin is `x=x0+c*(cw+gx)+p`, `y=H-y0-r*(ch+gy)-p-h`.

For each front trim `(x,y,w,h)`, the paired back trim is `(W-x-w,y,w,h)`. Reflect **positions**, not artwork pixels. Backs remain normally readable when each printed face is viewed upright after turning the sheet about its vertical edge. Leave sheet-level 180-degree compensation to existing pass planning for printers that turn about the other edge. No new user flip-edge switch in this version.

Both Side.pages sequences contain C*R slots in row-major order **as viewed on that face**. A back slot at row r,column c contains the front slot at r,C-1-c. Pad a partial sheet with undecorated fillers in mirrored positions. Both sides always exist. Sheets use consecutive zero-based indices; signatures=().

Cut marks are front-only 0.25 pt black tick segments, length 2 pt, separated by 1 pt from the effective bleed rectangle. At each trim corner, one horizontal tick aligned to the trim y and outside the bleed x, and one vertical tick aligned to the trim x and outside the bleed y. Reuse Mark(kind='cut_line'); reserve the full 3 pt clearance so ticks fit without entering neighboring bleed. Deduplicate identical segments. Back pages never carry these marks; cut against fronts.

Fit source upright dimensions after the CardFace rotation into the art rectangle. Contain scale=min(art_w/src_w,art_h/src_h); cover scale=max(...); center both axes; scale_x=scale_y. Clip in destination sheet coordinates; rotate/scale the source within the clip using existing Placement semantics. Do not apply book-wide document_scale, source DPI-based finished sizes, gutter, binding, creep, or fold rules.

## Registration guarantee and its limits

For cuts made on the front trim, uniform back displacement `(dx,dy)` remains covered by black when `abs(dx)<=e` and `abs(dy)<=e`. Remaining border on the tightest edge is at least `b-max(abs(dx),abs(dy))`; a positive all-around border additionally requires that maximum displacement be strictly less than b. This assumes uniform translation, correct cut positions, matching scale, and printable ink coverage. It cannot guarantee correction of skew, scaling, inaccurate cuts, or printer dead zones.

Expose border width as “Black border inside cut” and bleed as “Black beyond cut (alignment allowance).” Explain that increasing bleed may reduce cards per sheet. Reuse printer profile registration offsets as a separate correction; never add those offsets to the card dimensions or apply them twice.

## Persistence and compatibility

Book saves retain version 1 and their existing payload. Card saves use version 2 with top-level version/pages/layout/printer plus kind='cards' and card_deck. Encode the dataclasses above using their field names; tuples become JSON lists. Missing kind means book. Missing card_deck on kind=cards, unknown kind, or a card_deck with missing kind is an explicit load error. New readers must not silently discard card data. Older builds only warn for newer versions and will see empty pages; document that they cannot edit card projects and may lose deck data if they resave them. Do not claim backward compatibility with old card-unaware writers.

Persist incomplete pairing, unused assets, settings, original names, IDs, rotations, copy counts, and mode. No SheetPlan, thumbnails, widget state, or cached preview is persisted. Recompute the plan after load. Use existing source checks on every asset PDF once; original image presence is not required. Preserve the existing outside-root advisory/callback policy and atomic writes.

## Output contracts

Full PDF: front0,back0,front1,back1, canonical orientation, no implicit printer correction (same portable output semantics as existing book export). Print and profile-specific pass export: existing `plan_passes` / `pass_export`, whole-sheet reversal/rotation/offset only. Card placement is never reversed a second time by UI, exporter, or backend. Preview defaults to the canonical artifact and labels it “Before printer correction”; if existing profile preview applies corrections, explicitly label that view and test it against the corresponding pass artifact.

Any drawn property changes the digest: source, face rotation/placement, trim/art/bleed rectangles, border flag, marks, paper size, slot content and filler presence. Include card_id/copy_index with length framing for unambiguous slot identity. Do not change legacy book digest input when OutputPage.card is None. Existing print-session validation still protects source/profile changes.
