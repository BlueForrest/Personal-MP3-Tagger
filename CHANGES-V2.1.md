# jstal-tagger – changes in V2.1

V2.1 is V2 plus the improvements you asked for. The tag reading and writing code (`tags.py`)
is unchanged apart from version strings; the save and wipe operations write exactly what
V2 wrote. Your `.env` keeps working: the new settings are optional.

## 1. Your requests

| Request | What was done |
|---|---|
| Start directly in edit mode, as a setting in `.env` | New setting `EDIT_MODE_DEFAULT` (default `true`): the page starts with **Edit tags** switched on. `false` gives the V2 behaviour. Editing still only makes drafts; nothing is written without **Save tags** and its confirmation. |
| Pre-listen to a selected file | New **Listen** column with a ▶ button per song. A player bar appears above the grid (the browser's own play / pause, position and volume); the same button pauses, ▶ on another row switches the song, ✕ closes the player. The tag editor has a **▶ Listen** button next to the file name (handy for checking lyrics or a YouTube result). The server streams the file read-only (`/api/audio`, with range requests so seeking works); only real `.mp3` files inside the music folder are served. |
| YouTube API call counter on the screen, message when the 100 are used | The header shows **YouTube n / 100** (only when a key is configured). The app counts every search before it is sent; the count is saved in `BACKUP_HOST_PATH/.jstal-tagger/youtube-quota.json`, so it survives a restart of the container. Orange when fewer than about 15 % are left, **red "limit reached"** at the limit: the app then refuses further YouTube lookups with a clear message in the lookup column and a pop-up, and the YouTube source is unticked in the editor ("daily limit reached"). If Google itself answers `quotaExceeded` (other programs with the same key count too), the badge shows that until the reset. The day switches at **midnight Pacific time**, as Google does (about 09:00 in Belgium; the badge's tooltip shows the local time). Setting `YOUTUBE_DAILY_LIMIT` (default 100). Cached results (six hours) cost nothing. Click the badge to refresh it. |
| Select all / Deselect all in the Wipe window | Three buttons above the fields: **Select all**, **Deselect all**, **Default choice**. A line at the bottom shows how many fields are ticked and warns in red when Artist, Title or Album is among them; **Prepare wipe…** is disabled with nothing ticked. |
| Prefill "WIPE SELECTED TAGS" | The confirmation text is filled in. The server still checks it, so the button is disabled again if the text is changed. The **Cancel** button has the focus when the window opens, so Enter does not wipe by accident; the wipe happens when you click **Wipe these tags**. |
| User manual | `templates/manual.html`, served at `/manual`: 17 sections written from the code (settings, grid, editing, covers, every lookup source, the YouTube counter, saving, wiping, report, safety and restore, troubleshooting, quick reference). It follows the app's theme and has a **Print / save as PDF** button (prints on white). A PDF copy is in this folder. |
| Button to view the manual near the theme icon | The 📖 button in the header opens the manual in a new tab. |
| Investigate megalobiz.com/lrc/maker for LRC data | Done, see 2. Megalobiz is now a second lyrics source. |

## 2. Megalobiz

`https://megalobiz.com/lrc/maker` itself is a browser tool for making LRC files by tapping
along with a song, so it is of no use to the server. But Megalobiz publishes a **public API**
(`https://megalobiz.com/developers`): `GET https://api.megalobiz.com/api/public/v1/search`
with `title`, `artist`, `duration` and `limit`, which returns sheets with `id`, `title`,
`artist`, `duration`, `duration_diff`, `score`, `url` and `lrc_url`; and
`GET …/lrc/<id>`, which returns the full `lrc` text and the sheet's page `url`. No key is
needed (30 requests a minute, 1000 a day per address); a free key made after signing in
raises that to 300 / 20 000 (`MEGALOBIZ_API_KEY`). The fair-use rules ask for a descriptive
User-Agent (your `LOOKUP_USER_AGENT`), lookups only for the songs you work on, and a link
to the sheet's page where the lyrics are shown (the **View source** link in the editor).

In the tag editor the Lyrics lookup now offers **LRCLIB** and **Megalobiz (LRC)**:
- search on artist, title (without a trailing year) and duration; the best three sheets
  are fetched (one request each, 0.5 s apart, retried on 429 with the `Retry-After` wait);
- a sheet is shown only when artist and title are at least 90 % similar and the duration
  differs at most 3 s (LRCLIB uses 95 % and 2 s; Megalobiz sheets are timed by hand, so a
  little wider). A sheet without an artist name needs an almost exact title and 1.5 s;
- sheets without timestamps are skipped (Megalobiz is only used for LRC);
- results are cached for six hours; the label shows title, artist and duration, the
  warnings remind you to play the song and check that the lines keep up.

The live API was checked from here (the search and sheet answers above are real); the
app's own calls could not reach the internet from the test environment, so the Megalobiz
code was tested against those recorded answers (including the 429 and 401 cases). The
first real lookup on your server is the real test.

## 3. Other changes

- `templates/index.html` had SharePoint / OneDrive "Document ID" properties inserted into
  its `<head>` (`xmlns:mso`, `<!--[if gte mso 9]>…`). Removed; browsers ignored them anyway.
- `.env.example` was mentioned in the README but missing from the V2 folder; it is included
  now with every setting explained.
- `requirements.txt`: `tzdata` added (the Pacific-time reset of the YouTube counter).
- `/api/config` also returns `edit_mode_default` and `youtube_quota`; new `GET /api/audio`
  and `GET /api/youtube/quota`; `/api/lookup` returns the counter with every YouTube answer
  (also with an error).
- README updated; version 2.1 everywhere.

## 4. Tests that were run

- Browser test of the page (Chromium, 38 checks): edit mode on at start, header counter,
  manual button and page, listing, ▶ play / pause / switch / stop with the real audio
  element, editor Listen button, lyrics sources LRCLIB + Megalobiz, YouTube source label
  with the counter, a counted YouTube search updating the header, draft → save → file
  re-read, Wipe window Select all / Deselect all / Default choice with the warning line,
  prefilled confirmation (button disabled when the text is changed, Cancel has the focus),
  a real wipe of TDRC + Other tags, theme switch and the manual following it, no script errors.
- API checks: `/api/audio` full and range requests (206 with `Content-Range`), path
  traversal and non-MP3 refused (403), missing file 404; the YouTube counter with a limit
  of 2 (two counted, the third refused with the message, state file written and reloaded);
  cross-origin lookups refused; `EDIT_MODE_DEFAULT` true / false / unset.
- Megalobiz code path against the recorded live API answers (match, cache hit, duration
  and artist gates, 429, 401).
- `tags.py --self-test` passes; the app runs with gunicorn exactly as in the Dockerfile.

**Not tested here:** the real online lookups from the app (no internet access in the test
environment), building the Docker image, and your server.
