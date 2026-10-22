# jstal-tagger – changes in V2

V2 is a rebuild of V1 (the version in the `jstal-tagger` folder). Stage 2 and Stage 3 are
removed: everything they did is now in the tag editor (Stage 1). The tags written to the
files are the same as in V1: the same save and wipe operations on test songs gave
byte-identical tags and audio in V1 and V2.

## 1. Removed

| V1 | Why |
|---|---|
| Stage 2 "Collect year, cover, lyrics, YouTube, comments" (batch collection, `/api/collect`) | The same lookups are in the tag editor ("Lookup data") per field. |
| Stage 3 "Review and save approved fields" (`/api/write/prepare` with a collection, typed `SAVE APPROVED CHANGES`) | A lookup result now becomes a draft and is saved with Save tags. |
| The three repair scripts `add_save_diagnostics.py`, `fix_save_and_cleanup.py`, `preserve_artist_values.py` | Their fixes were **not applied** to the `tag_writer.py` / `maintenance.py` in the V1 folder. V2 has them built in (see 3). |
| `self_test.py` | Now `python tags.py --self-test <mp3>`. |
| Setting `YOUTUBE_SEARCH_EXISTING` | Only for Stage 2. The editor always searches (see 4). |

## 2. Files

13 Python files became 3; the page has no script in the HTML any more.

| V2 | Contains (from V1) |
|---|---|
| `app.py` | web routes: `app.py`, the routes of `maintenance.py` and `field_lookup.py`, `/api/current-cover` of `enrichment.py` |
| `tags.py` | `tag_writer.py` (fingerprints, backup, verified write), `maintenance.py` (save / wipe operations, thumbnails, uploads), the song reader of `app.py`, `self_test.py`, the 3 repair scripts |
| `lookups.py` | `enrichment.py` (MusicBrainz + Cover Art Archive), `comment_lookup.py`, `discogs_lookup.py`, `lyrics_lookup.py`, `youtube_lookup.py`, `field_lookup.py` |
| `templates/index.html` | markup only |
| `static/app.js` | `stage1.js` + the three `<script>` blocks of `index.html` (Stage 2/3 parts removed), rewritten as readable, numbered sections |
| `static/style.css` | new layout, dark and light theme |
| `static/favicon.ico` | V1's `templates/favicon.ico` was a 1 MB PNG that was never served; now a real 10 KB icon at `/favicon.ico` |

The matching rules of the lookups (scores, thresholds, rate limits, caches) are unchanged.
New API names: `/api/write/*` (save / wipe), `/api/lookup`, `/api/lookup/cover`,
`/api/thumbnail`, `/api/cover`, `/api/songs`, `/api/config`.

## 3. Fixes (from the repair scripts, now built in)

- **Two values in one ID3v2.3 frame** (for example artist "Toto" + "Guest Singer"): V1 joined
  them with "/" on every save, the check saw an unapproved change and refused the file
  ("Unapproved tags would change…"). Tested: V1 refuses such a file, V2 saves it and keeps
  both values.
- **Empty text frames** that mutagen does not write back are no longer seen as a change.
- **A refused save says why**: removed / added / changed frames with their values.
- **A failed attempt** is kept next to the backup (`…failed-attempt.tmp`) for diagnosis.
- **Backups of a fully successful save are removed** afterwards (as the repair script did).
  New setting `KEEP_BACKUPS=true` keeps them. Wipe backups and saves with a failed file are
  always kept.

## 4. Improvements

Page
- New layout: header with a **Writing on / Read-only** badge, folder breadcrumb, toolbar
  grouped by purpose, counters (files, selected, drafts, read errors), sticky first columns.
- **Wipe fields are chosen in the Wipe window** instead of the tick boxes in the column
  titles (V1 needed a paragraph of text to explain those). Artist, Title and Album are
  unticked by default, as before.
- The **save / wipe confirmation is readable**: per song a table "now in the file → new",
  covers as pictures; the raw JSON is under "Technical details".
- **Song details** (click a filename) and **Last report** open in a window instead of a
  text block at the bottom of the page.
- **Columns** can be hidden (remembered by the browser).
- Progress bar while saving; messages pop up at the bottom right.

Editing
- OK without a change makes **no draft**; typing the old value back **removes** the draft.
- The editor shows the value saved in the file when a cell has a draft.
- **Covers are made smaller in the browser** (600 × 600) before they are sent: pictures up
  to 25 MiB can be chosen and many covers can be saved at once (V1: 2 MiB per picture and
  "save fewer covers" errors because of the 4 MiB request limit).
- Lookups: the **artist / title to search with can be adjusted** without changing the tags;
  "Use" on a result fills the field (you can still change it before OK).
- **YouTube lookup also searches when the song already has a TVSHOW value** (V1 showed no
  results). The old value only changes when you pick a result and save.
- Discogs / YouTube without a key are shown as "not configured".
- Several values in one tag are shown as "Toto / Guest Singer" (V1 showed an invisible
  separator).
- After a save, the saved songs are **deselected** (they were only selected because of their
  drafts), so a following wipe cannot hit them by accident.

Server
- **Listing is about twice as fast**: every MP3 is hashed once instead of twice
  (100 songs of 4.8 MB: 1.0 s → 0.55 s).
- All POST requests (also lookups) need the same-origin check.
- Thumbnails can be cached by the browser (the address changes when the file changes).
- Backup batches are named `save-…` or `wipe-…` (V1: `maintenance-…`).

## 5. Tests that were run

- V1 and V2 side by side on the same test songs (ID3v2.3 and v2.4, cover, LRC and plain
  lyrics, SYLT, comment, TVSHOW / VIDEO, URL, key, ReplayGain, custom TXXX, two artists,
  empty frame, ID3v1 tail, APEv2 tags, untagged): same save and wipe → identical tags,
  identical audio, identical ID3v1 tail. V2 also saves the two-artist file that V1 refuses.
- Lookups of V1 and V2 against the same recorded answers of MusicBrainz, Cover Art Archive,
  Wikipedia/Wikidata, Discogs, LRCLIB and YouTube: identical results (two labels reworded).
- Browser test of the page (Chromium): 65 checks (folders, listing, filter, sort, selection,
  editing, drafts, cover upload, lookups, save, wipe with phrase, APEv2 refusal, cancel,
  details, report, columns, export, theme, no errors).
- 28 API checks: path traversal, other-site requests, invalid values, cover hosts, wrong
  phrase, read-only mode. Tag text with HTML is shown as text.
- `tags.py --self-test` passes; the app runs with gunicorn exactly as in the Dockerfile.

**Not tested here:** the real online lookups (no internet access in the test environment;
they use the V1 code unchanged), building the Docker image (Docker Hub not reachable from
the test environment) and your server.
