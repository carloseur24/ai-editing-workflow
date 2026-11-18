# Labeling footage fast

`fenix label` is the intake loop for a folder that arrives as a pile of
`VID_2026...` / `lv_...` files. It answers "what is each clip?" without opening
forty players, and renames with a manifest you can reverse.

It is **local folder intake**, not stock search: no upload, no index, no cloud
vision, no licence tracking.

## The loop

```bash
./fenix label scan  <folder>                  # ~10 s for 40 clips
# read <folder>/.fenix-label/grids/*.jpg (one batch), fill names.tsv
./fenix label apply <folder> --dry-run        # optional check
./fenix label apply <folder>                  # <1 s, manifest + undo
```

The work directory is `<folder>/.fenix-label/` — hidden, disposable, never
committed. Everything the rename needs travels with the folder.

## The 30-second budget (measured)

Benchmarked on 40 files / 408 MB (33 video, 7 photo) from the Windows drive,
8 workers, this box:

| Phase | Time | What happens |
|---|---|---|
| `scan` — facts | ~2 s | one parallel `ffprobe` per file (duration, rotation, fps, codec, audio) |
| `scan` — sheets | ~7 s | **keyframe-only** decode, 4 samples spread over the timeline, one 2×2 JPEG per video |
| `scan` — grids + dupes | ~1 s | 8-up JPEG grids, 64-bit average hashes, duplicate clusters |
| vision | one batch | read **5 grids** (not 40 files); open a sheet only when a tile is ambiguous |
| `apply` | <1 s | slug + validate + two-phase rename + manifest |

The first version decoded every frame and took 68 s. Keyframe-only sampling with
`select` (instead of `fps`, which duplicates the nearest keyframe) is the 7×
win. The scan prints its own elapsed time so the claim is checkable, not a vibe.

The trap to avoid: reading one image per clip. Eight files per grid is the
density where a 300 px cell is still readable; 20 per grid is not.

## What the agent does

1. Run `scan`. It prints the grid paths and the index range of each grid.
2. Read all grids in **one** tool batch. Each cell is labelled `#01`, `#02`…
   left-to-right, top-to-bottom. The number is the only key: the agent never
   needs to hold the old filename in context.
3. If a cell is ambiguous (two similar phone shots), read
   `.fenix-label/sheets/<NN>.jpg` — that is the 4-frame detail sheet.
4. Fill `names.tsv`, one line per file, tab-separated:
   ```text
   01	anuncio-fenixsoft-iphone-14-azul-128gb-330usd
   02	galaxy-s22-ultra-bateria-adaptador-45w
   03	
   ```
   Empty second column = skip. The extension comes from the source file; do not
   type it.
5. `apply`. It slugifies (`--exact` disables), validates, refuses collisions,
   renames in two phases (so `a→b, b→a` is safe) and writes
   `renames-<stamp>.csv` + `undo-<stamp>.sh`.

An apply that finds any invalid name changes nothing — no partial renames.

## Naming grammar

`<device>-<scene>-<detail>`, ASCII kebab-case, no timestamps.

| Part | Examples |
|---|---|
| device | `iphone-se`, `iphone-14`, `galaxy-s22-ultra`, `galaxy-s25-ultra`, `local-fenixsoft` |
| scene | `unboxing`, `trasera`, `soporte`, `camara`, `interfaz`, `carga-rapida`, `s-pen` |
| detail | `tienda`, `exterior`, `caja`, `128gb`, `copia`, `tiktok` |

Write what the frame proves: a model name read off the screen or the box, not a
guess from a silhouette. If the generation is not provable (iPhone SE 2020 vs
2022 look identical), stop at `iphone-se` — a wrong generation is worse than a
generic one.

Duplicates keep both files and mark the copy: `...-copia` for a byte-identical
copy, `...-tiktok` for the re-encoded download. `scan` already flags the pair, so
the label only has to say which suffix applies.

Do not put the capture date in the name. `mv` preserves mtime and both Windows
and `ls` show it; the date is already there.

## How duplicates are decided

`scan` builds a 64-bit average hash for each of the four sheet frames and
compares all four. Two files match only when **every** frame is within
`labeling.duplicate_threshold` bits (default 8) and, for video, the durations are
within 3 %. One matching frame is not enough: two shots of the same phone on the
same stand would collapse into a false positive.

The primary of a cluster is the older mtime (the original capture); ties fall
back to the lower index. The copy is flagged `dup #NN` in the grid.

## Safety rails

- No overwrite: an existing target aborts the whole apply.
- Case-insensitive comparison, because the destination is usually `/mnt/c`.
- Windows reserved stems (`CON`, `NUL`, `COM1`…) and `<>:"/\|?*` are rejected.
- Paths longer than 250 characters are rejected before any rename.
- Two-phase rename (`source → temp → target`) makes swaps and chains safe.
- `apply` updates `scan.json`, so re-running it is a no-op instead of a second
  rename.
- `undo` reverses the latest manifest; the generated `undo-*.sh` does the same
  without the tool.

## Tuning (`config/pipeline.json → labeling`)

| Key | Default | Effect |
|---|---|---|
| `jobs` | 8 | parallel ffprobe/ffmpeg workers; `--jobs` overrides one run |
| `sheet_tile_width` | 400 | width of one frame inside a 2×2 detail sheet |
| `grid_cols` / `grid_rows` | 4 / 2 | files per vision grid (8) |
| `grid_cell_width` | 300 | width of one cell; 300×533 keeps 9:16 readable |
| `duplicate_threshold` | 8 | max Hamming distance per frame; raise only if re-encodes are missed |
| `name_max_len` | 80 | filename budget before the extension |

## Limits

- Vision is the agent's job. The tool cannot name "a red case on a stand"
  without a model looking at the grid; it makes that look cheap.
- No OCR. Model numbers are read from the sheet when legible, not extracted.
- Photos depend on the decoder: JPEG/PNG/WebP always, HEIC only if the local
  ffmpeg/Pillow build supports it.
- `--recursive` walks subfolders, but skips dot-directories (including its own
  work directory).
