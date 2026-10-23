# jstal-tagger – changes in V2.3

V2.3 is built on V2.2. The requests of "jstal-Tagger change request for V.2.3.txt":

| Request | What was done |
|---|---|
| Tag files from **Videos** too, like `MUSIC_HOST_PATH=/srv/music` → `VIDEO_HOST_PATH=/srv/videos` | New library **🎬 Videos** next to **🎵 Music** (top left of the folder bar). `VIDEO_HOST_PATH` is mounted as `/videos`, with its own `VIDEO_READ_ONLY` (default `true`). See 1. |
| **Read / write / wipe MP4 tags** (like MP3) | `.mp4`, `.m4a` and `.m4v` files are listed, played, edited and wiped like MP3 files, with the same safety checks (backup → temporary copy → check → replace). See 2. |
| **See (not write) the ReplayGain metadata** in the grid | New read-only columns **RG gain** and **RG peak**, and a **ReplayGain** part in the details window. See 3. |
| **Find out which tags to keep** when wiping files normalised with mp3rgain, for MP3 and MP4 | Tested with the real mp3rgain 3.12. The ReplayGain and mp3rgain values (also the undo information) are now a protected group: no save or wipe removes or changes them. See 4. |
| **Remember the last setting** (folder, Music or Videos) | The browser remembers the library, the folder and "Include subfolders" and opens them again on the next visit. See 5. |

The user manual is updated (V2.3, Word and PDF, 16 pages: the new chapter 14 *ReplayGain and
mp3rgain*, the MP4 field table, two new screenshots and new screenshots of the main screen,
the header and the manual window).

## 1. The Videos library

- `.env`: `VIDEO_HOST_PATH=/srv/videos` (**required**: docker compose does not start without it,
  with the message "Set VIDEO_HOST_PATH in .env (V2.3), for example /srv/videos") and
  `VIDEO_READ_ONLY=true|false`. Writing in Videos needs `ENABLE_TAG_WRITES=true` **and**
  `VIDEO_READ_ONLY=false`; Music keeps its own `MUSIC_READ_ONLY`.
- The folder bar starts with **🎵 Music | 🎬 Videos**. The breadcrumb starts with the library
  (`🎬 Videos › Clips`). A library whose folder is not in the container is greyed out.
- Both libraries list `.mp3`, `.mp4`, `.m4a` and `.m4v` files. "List MP3 files" is now
  **List files**. A new **Type** column shows MP3 / MP4 / M4A / M4V, and the counter says
  "5 files (MP4)" or "12 files (10 MP3, 2 MP4)".
- The **Writing on / Read-only** badge is for the library that is open: Music can be writable
  while Videos is read-only. A plan prepared in a read-only library says why it cannot be written
  ("The Videos folder is mounted read-only. Set VIDEO_READ_ONLY=false …").
- Every request carries the library (`library=music|videos`); paths stay relative to the library.
  Old calls without it use Music, as before.
- Backups: `BACKUP_HOST_PATH/<batch>/music/<path>` or `…/videos/<path>` (V2.2 had no
  `music/` level). The backup folder must be outside both libraries.

## 2. MP4 tags

MP4 files have iTunes-style tags ("atoms"). The columns are the same as for MP3:

| Column | MP3 (ID3) | MP4 (atom) |
|---|---|---|
| Artist / Title / Album | TPE1 / TIT2 / TALB | ©ART / ©nam / ©alb |
| Year | TDRC / TYER | ©day |
| BPM | TBPM | tmpo (whole number) |
| Duration (stored) | TLEN | — (MP4 has none: the cell cannot be edited) |
| Track | TRCK | trkn (6 or 6/12) |
| Genre | TCON | ©gen (also the old numeric gnre) |
| Cover | APIC | covr (JPEG, max. 600 × 600) |
| Lyrics | USLT / SYLT | ©lyr |
| YouTube URL | TXXX:TVSHOW | tvsh |
| VIDEO | TXXX:VIDEO | ----:com.apple.iTunes:VIDEO |
| Comment | COMM | ©cmt |
| Other tags | every other frame | every other atom (©too, desc, cpil, …) |

- The online lookups, covers, Listen (an MP4 video plays its **sound** in the player bar),
  details, export and the report work for MP4 files too.
- Safety check for MP4: only the tag part may change. The content of the media data (`mdat`)
  must be byte-for-byte the same, and duration, channels and sample rate must stay the same.
  Also tested with a "fast start" MP4 (tags before the media data, so the media data moves):
  audio and video decode identically afterwards.
- Refused with a clear message: a stored duration for an MP4 ("MP4 files have no stored duration
  (TLEN) tag"), a BPM with decimals ("An MP4 file stores BPM as a whole number").
- `tags.py --self-test` accepts an MP4 file too.

## 3. ReplayGain in the grid and the details

- **RG gain**: the track gain, for example `🎚️ +0.21 dB` (no track gain: the album gain with
  "(album)"). **🎚️** = mp3rgain changed the volume inside the file (it stored undo information);
  the gain is then only the small rest (< 0.75 dB). Without 🎚️ only the tags were written
  (`--tags-only`). `—` = no ReplayGain. **RG peak**: the track peak (1.0 = full scale).
- Both columns are read only, sortable (by number) and can be hidden with **Columns**.
- Click the gain value (or the filename) for the **🎚️ ReplayGain (read only)** part of the details
  window: track / album gain and peak, the algorithm, "Volume in the file: changed by mp3rgain
  (lossless), undo value -012,-012,N" and every stored value with where it is (ID3, APEv2, MP4).
- The values are read from every place mp3rgain can use (names in upper or lower case).

## 4. Which tags are kept when wiping (investigation)

Tested with **mp3rgain 3.12.0** (built from the GitHub project) on MP3, MP4 and M4A files, with
its default settings, `-s i`, `-s a` and `--tags-only`:

| Where | Written by mp3rgain | Meaning |
|---|---|---|
| MP3, ID3v2 TXXX | `REPLAYGAIN_TRACK_GAIN`, `REPLAYGAIN_TRACK_PEAK`, `REPLAYGAIN_ALBUM_GAIN`, `REPLAYGAIN_ALBUM_PEAK` (with `-a`), `REPLAYGAIN_ALGORITHM` (`--rg2` / `--r128`) | gain in dB, peak, method |
| MP3, **APEv2 tag** at the end of the file | `MP3GAIN_UNDO`, `MP3GAIN_MINMAX`, `MP3GAIN_ALBUM_MINMAX` | how the frames were changed; `MP3GAIN_UNDO` is needed by `mp3rgain -u` |
| MP3 with `-s i` | the `MP3GAIN_*` values as ID3 TXXX frames | |
| MP3 with `-s a` | everything in the APEv2 tag (as the old mp3gain) | |
| MP4 / M4A / M4V | freeform atoms `----:com.apple.iTunes:replaygain_track_gain`, `…_track_peak`, `…_album_gain`, `…_album_peak`, `…_algorithm`, `mp3rgain_undo`, `mp3rgain_minmax` | the same values |

Important finding: with `mp3rgain -r` the **audio itself** is changed (losslessly) and the
`REPLAYGAIN_TRACK_GAIN` tag then holds only the **rest** that could not be applied (for
example +0.63 dB, as in your example). With `--tags-only` the audio is untouched and the tag
holds the full correction.

**What V2.3 protects** (a new protected group "replaygain", never part of a save or a wipe,
also not with every field ticked):

- ID3 TXXX frames whose name starts with `REPLAYGAIN_`, `MP3GAIN_` or `MP3RGAIN_`;
- ID3 `RVA2` (and `RVAD`/`XRVA`) frames and the `COMM:iTunNORM` comment (volume values of other
  programs, e.g. iTunes Sound Check; before V2.3 a comment edit or wipe removed iTunNORM);
- the **APEv2 tag**: never written. A wipe of "Other tags" is now allowed when the APEv2 tag only
  holds ReplayGain / mp3rgain values (V2.2 refused **every** file with an APEv2 tag, so every
  normalised MP3 was refused). A file with other APE items is still refused, with their names;
- MP4 freeform atoms named `replaygain_*`, `mp3rgain_*` and `iTunNORM`.

Before a file is replaced, the write check compares these values (and the APEv2 tag) before and
after: if one would change, the file is refused and the original kept.

The other tags in your example are **not** ReplayGain values: `TPOS` (disc number), `POPM`
(rating / play counter), `TCMP` (compilation) and `TKEY` (musical key). They are "Other tags"
and are removed when Other tags is ticked; untick it to keep them.

Checked after a wipe of **all** fields: `mp3rgain -s c` still shows every value, and
`mp3rgain -u` gives back exactly the original audio (decoded sample by sample) for MP3
(default, `-s a`, `-s i`, a file with ID3v1 + APEv2 + RVA2 + iTunNORM) and for MP4 (after an
edit **and** a wipe).

## 5. The last folder is remembered

The browser stores `{library, folder, Include subfolders}` (localStorage, key
`jstal-last-location`) every time a folder is opened. At the next visit the page opens that
library and folder again and says "Last folder opened again (Videos › Clips). Click List files."
The files are not read automatically (a big folder takes a moment). A folder that no longer
exists opens the top of its library; an unavailable library opens Music. It is per browser, like
the hidden columns.

## 6. Files

| File | Change |
|---|---|
| `app.py` | Version 2.3. `VIDEO_ROOT`, `LIBRARIES`, `library_root()`, `library_writable()`; every route takes the library; `/api/config` lists the libraries (available / writable) and the file types; `/api/audio` serves .mp4/.m4a/.m4v (video/mp4, audio/mp4); the wipe check refuses only APEv2 items that are not ReplayGain values; plans carry the library and say why a read-only library cannot be written; the apply refuses a read-only library. |
| `tags.py` | MP4 reading / writing (section 1: `MP4_FIELDS`, …; `apply_mp4_operation()`), `mp4_media_digest()` (the MP4 audio check), ReplayGain reading (`read_replaygain()`) and the protected field `replaygain` (`field_for_key()`), APEv2 check before replacing, MP4 stream check, backups per library, self-test for MP4. |
| `lookups.py` | Version strings only. |
| `templates/index.html` | Library switch, "List files", subtitle "MP3 & MP4 tag maintenance", wipe window text (ReplayGain always kept), hint line. The SharePoint "Document ID" block is removed again (see V2.2 note 7: SharePoint may add it again). |
| `static/app.js` | Libraries and remembered location (section 5), Type / RG gain / RG peak columns, ReplayGain details, "Kept" list in the wipe confirmation, MP4 notes in the editor, read-only badge per library. |
| `static/style.css` | Library switch, ReplayGain cells. |
| `Dockerfile`, `docker-compose.yml`, `env.example` | `VIDEO_ROOT=/videos`, the `/videos` mount, `VIDEO_HOST_PATH`, `VIDEO_READ_ONLY`, version 2.3. |
| `README.txt` | Version 2.3, libraries, MP4, ReplayGain, settings, restore path. |
| `static/manual/user-manual.docx/.pdf`, `jstal-tagger V2.3 - User Manual.docx/.pdf` | The V2.3 manual. |

Unchanged: `requirements.txt` (mutagen already reads/writes MP4), `static/MP3Tag.png`,
`static/favicon.ico`.

## 7. Install

1. **Backup**: `cp -a jstal-tagger jstal-tagger-V2.2-backup` (with your `.env`).
2. **Copy** the files of this V2.3 folder over the application folder (`app.py`, `tags.py`,
   `lookups.py`, `templates/`, `static/`, `Dockerfile`, `docker-compose.yml`). Keep your `.env`.
3. **Add to `.env`**:
   ```
   VIDEO_HOST_PATH=/srv/videos
   VIDEO_READ_ONLY=true
   ```
   (`false` when the videos may be tagged.) Optional: `LOOKUP_USER_AGENT=jstal-tagger/2.3 (mailto:…)`.
4. `docker compose down` then `docker compose up -d --build`.
5. Browser: **Ctrl+F5** on `http://<server>:5080`.
6. Check: header **V2.3**, the **🎵 Music | 🎬 Videos** switch, list a normalised folder (RG gain
   with 🎚️), open Videos and list it. Optional self-test:
   `docker compose run --rm --no-deps --entrypoint python jstal-tagger tags.py --self-test "/videos/SOME VIDEO.mp4"`.

**Roll back**: `docker compose down`, put the V2.2 backup back, `docker compose up -d --build`,
Ctrl+F5. The V2.2 docker-compose.yml does not need `VIDEO_HOST_PATH` (it may stay in `.env`).
Nothing in the files depends on the version.

## 8. Tests that were run

- `python -m py_compile`, `node --check static/app.js`; `tags.py --self-test` on an MP3 and an
  MP4 normalised with mp3rgain (PASS, "ReplayGain values kept").
- **Server test** (Flask test client, real files made with ffmpeg and normalised with mp3rgain
  3.12: default, `-s a`, `-s i`, `--tags-only`, ID3v1 + RVA2 + iTunNORM, a foreign APEv2 item;
  MP4 with and without ReplayGain, M4A, an untagged M4V): **81 checks, all passed** — libraries,
  path checks, listing counts, ReplayGain values per place, Other tags without ReplayGain, MP4
  fields, audio streaming (MP4/M4A, range requests), MP3 save keeps ReplayGain, wipe of all fields
  on 5 normalised MP3s (values kept, ID3v1 kept, `mp3rgain -s c` OK, `mp3rgain -u` restores the
  original audio exactly), foreign APEv2 refused only for Other tags, MP4 save of all 12 fields
  (values read back, audio identical, streams kept, thumbnail / cover), MP4 refusals, clearing
  fields, an untagged M4V, MP4 wipe (only the ReplayGain atoms left, `-s c` and `-u` OK after
  edit + wipe), read-only Videos (badge, plan, apply refused), Videos not mounted.
- A "fast start" MP4 with a 50 kB lyrics + cover save: audio and video decode identically.
- **Browser test** (Chromium, gunicorn as in the Dockerfile): **33 checks, all passed** — header,
  library switch, List files, Type / RG columns and tooltips, ReplayGain details, sorting, the wipe
  window text and the "Kept" list, a real wipe, Videos library, MP4 duration not editable, MP4
  edit + VIDEO tick + save (values in the grid and details), MP4 Listen, last folder remembered
  after reload, fallback for a removed folder and an unknown library, light theme, the manual
  window; no script errors.
- **Manual**: the .docx passes the Office XML validation; the PDF (LibreOffice) has 16 pages,
  the title "jstal-tagger V2.3 - User Manual", bookmarks and working table-of-contents links;
  the page numbers of the table of contents were checked against the PDF; pages looked at.

**Not tested here**: your server and real music / video folders, the Docker build, Edge /
Firefox / phones, Word on Windows (checked in LibreOffice), very large MP4 files (saving tags
may rewrite the file, which takes a few seconds for a big video), MP4 files from other taggers
with unusual atoms.
