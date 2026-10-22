JSTAL-TAGGER V2.1 - MP3 TAG MAINTENANCE
=======================================

A small web application to check and maintain the tags of the MP3 files in a music folder
on the server: browse folders, list the songs with their tags, listen to a song, edit tags
(with online lookups for year, cover, comment, YouTube link and lyrics) and wipe tags. It
runs in Docker on port 5080. There is no login: use it on your own network only, never on
the internet.

What changed since V2: see CHANGES-V2.1.md. Since V1: CHANGES-V2.md.
The full user manual is built into the app: the book button (top right) or http://<server>:5080/manual.
A PDF copy is next to this file.


FILES
-----
  app.py                 web server: pages, folders, songs, audio, lookups, save / wipe jobs
  tags.py                reading and safely writing the MP3 tags (+ self-test)
  lookups.py             MusicBrainz, Cover Art Archive, Wikipedia, Discogs, LRCLIB, Megalobiz,
                         YouTube (+ the daily YouTube counter)
  templates/index.html   the page (markup only)
  templates/manual.html  the user manual (served at /manual)
  static/app.js          everything the page does
  static/style.css       the look (dark and light theme)
  static/MP3Tag.png      logo, static/favicon.ico  browser icon
  Dockerfile, docker-compose.yml, requirements.txt, .env.example


INSTALL / UPDATE FROM V2
------------------------
1. Back up the V2 application folder, including .env.
2. Replace the application files with the files of this folder (app.py, tags.py, lookups.py,
   templates/, static/, Dockerfile, docker-compose.yml, requirements.txt).
   Keep your own .env. New optional settings (see below): EDIT_MODE_DEFAULT,
   YOUTUBE_DAILY_LIMIT, MEGALOBIZ_API_KEY. Nothing in .env has to change.
3. docker compose up -d --build --force-recreate
4. Ctrl+F5 in the browser (http://<server>:5080).

Updating from V1: follow the V2 instructions in CHANGES-V2.md first (remove the old V1
Python files and static/stage1.js).

Check on the server (nothing is written):
  docker compose run --rm --no-deps --entrypoint python jstal-tagger -c "import app; print('Application imports successfully.')"
  docker compose run --rm --no-deps --entrypoint python jstal-tagger tags.py --self-test "/music/SOME SONG.mp3"
The self-test writes only a temporary copy, never the song itself.


SETTINGS (.env)
---------------
  MUSIC_HOST_PATH        music folder on the server (mounted as /music)
  BACKUP_HOST_PATH       backup folder on the server, OUTSIDE the music folder (/backups)
  ENABLE_TAG_WRITES      false = read-only (default). true + MUSIC_READ_ONLY=false = writing on
  MUSIC_READ_ONLY        true (default) mounts the music folder read-only
  KEEP_BACKUPS           false (default): the backups of a save in which every file was saved
                         and verified are removed afterwards. Wipe backups and saves with a
                         failed file are always kept. true = keep every backup.
  EDIT_MODE_DEFAULT      true (default, V2.1): the page starts with "Edit tags" switched on.
                         false = start switched off (as in V2). Editing only makes drafts.
  LOOKUP_USER_AGENT      required: "jstal-tagger/2.1 (mailto:you@example.com)"
  YOUTUBE_API_KEY        YouTube Data API key (empty = YouTube lookup off)
  YOUTUBE_REGION         two letters, default BE
  YOUTUBE_PREFERRED_CHANNELS  channel names ranked higher, separated by |
  YOUTUBE_DAILY_LIMIT    100 (default, V2.1): YouTube searches per day; the free quota of
                         10 000 units allows about 100 searches. See THE YOUTUBE COUNTER.
  DISCOGS_TOKEN          Discogs personal token (empty = Discogs lookup off)
  DISCOGS_MASTER_OVERRIDES  {"artist|title": master id} for songs Discogs matches wrongly
  MEGALOBIZ_API_KEY      optional (V2.1). Megalobiz works without a key (30 requests a
                         minute, 1000 a day); a free key raises that to 300 / 20 000.

The header shows "Writing on" or "Read-only" and, when a YouTube key is set, the YouTube
counter. Discogs / YouTube without a key are shown as "not configured" in the tag editor.


USING IT
--------
Folders     Click a folder chip to open it, the breadcrumb or ↑ to go back. "List MP3
            files" reads the songs (tick "Include subfolders" to include all sub-folders).
Grid        Click a column title to sort. "Filter loaded songs" shows only songs that
            contain all typed words. "Columns" hides columns (remembered by the browser).
            Click a filename for all its ID3 tags, the number under "Other tags" for the
            frames without a column, a cover to see it large.
Listening   (V2.1) Click ▶ in the "Listen" column: a player bar appears above the grid
            (play / pause, position, volume). The same button pauses; ✕ closes the player.
            The tag editor has a "▶ Listen" button too. Listening never changes a file.
Selecting   Tick songs (or the box in the header for all shown songs). Select all /
            Deselect all also include songs hidden by the filter.
Editing     "Edit tags" is on at start (EDIT_MODE_DEFAULT). Double-click a cell (or select
            it and press Enter). OK makes a DRAFT (yellow cell) in the browser; nothing is
            written yet. A song with a draft is selected automatically. Typing the old value
            back removes the draft. "Clear field" makes a draft that deletes the field. The
            VIDEO column is a tick box (ticked = Yes, unticked = the tag is removed).
            Duration shows the measured audio time; editing it changes only the stored
            TLEN value (milliseconds).
Covers      In the cover editor choose a JPEG / PNG / WEBP picture. Big pictures are made
            smaller (600 x 600) in the browser; the server stores a JPEG of at most
            600 x 600 (never enlarged) and replaces all embedded pictures with it.
Lookups     For year, cover, comment, YouTube URL (TVSHOW) and lyrics the editor has
            "Lookup data": adjust artist / title if needed, tick the sources and click
            Lookup Data. Click "Use" on a result (it fills the field or shows the cover),
            then OK. For the year the earliest year found is chosen already.
            Lyrics come from LRCLIB and (V2.1) Megalobiz, a collection of user-made LRC
            sheets: only sheets with timestamps that match artist, title (90 %) and
            duration (3 s) are shown. Play the song to check the timing before you save.
            MusicHoarders opens in a new tab: download a cover there and choose it with
            "Choose a picture". Matches are heuristic: always check the source link.
Saving      "Save tags (n)" saves the drafts of the SELECTED songs (at most 100 at once).
            A window shows every field: now in the file -> new. "Save these changes"
            writes them. Saved songs are read again, their drafts disappear and they are
            deselected. Drafts are lost when the page is reloaded (the browser warns).
Wiping      Select songs, "Wipe tags…", tick the fields to remove (Artist, Title and Album
            are protected by default; (V2.1) "Select all" / "Deselect all" tick or untick
            every field, "Default choice" restores the default), "Prepare wipe…". The
            window lists the exact ID3 frames per song. (V2.1) The confirmation text
            WIPE SELECTED TAGS is already filled in: check the list and click "Wipe these
            tags". "Other tags" removes every frame without a column (URLs, key,
            ReplayGain, custom TXXX, unknown frames). Save or discard drafts first.
Report      "Last report" shows the result per file of the last save or wipe since the
            container started (download as JSON). The full report of a kept batch is
            BACKUP_ROOT/<batch>/write-report.json.
Export      "Export selected" downloads the tags of the selected songs as JSON.
Manual      (V2.1) The book button in the header opens the user manual in a new tab
            (/manual); "Print / save as PDF" is on that page.


THE YOUTUBE COUNTER (V2.1)
--------------------------
The YouTube Data API gives 10 000 quota units a day; one search costs 100 units, so about
100 searches a day are possible. The header shows "YouTube n / 100": the searches used
today and the limit (YOUTUBE_DAILY_LIMIT). Orange when fewer than about 15 % are left, red
when the limit is reached; then the app does not call YouTube any more and says so in the
lookup column and in a message. If Google itself reports that the quota is used up, the
badge shows that too. Google resets the quota at midnight Pacific time (about 09:00 in
Belgium; the badge's tooltip shows the local time). Click the badge to refresh it. A
result that is still in the cache (six hours) costs nothing. The counter is saved in
BACKUP_HOST_PATH/.jstal-tagger/youtube-quota.json so it survives a restart of the container.


SAFETY
------
- Nothing is written unless ENABLE_TAG_WRITES=true AND the music folder is writable.
- Every save / wipe is prepared first: the server checks every file and value and shows
  the exact plan. The plan is valid for 30 minutes and can be used once.
- A file that changed since it was listed (SHA-256 + size) is refused: list the folder again.
- Per file: a verified backup copy is made first, the change is made on a temporary copy,
  then it is checked that only the approved frames changed and that the audio part is
  byte-for-byte the same. Only then the original is replaced (same permissions / owner).
  A failure leaves the original untouched (the failed attempt is kept next to the backup).
- Several values in one ID3v2.3 frame (for example two artists) stay as they are.
- A failure is per file: other files of the same batch can still be saved.
- Only ID3v2.3 / ID3v2.4 (and untagged) files. ID3v1 copies of changed fields are updated.
  A wipe of "Other tags" refuses files with APEv2 tags (not ID3).
- Refused: symlinks, hard-linked files, files larger than 512 MiB, paths outside /music.
- Covers are only downloaded from HTTPS artwork sites (Cover Art Archive / archive.org,
  Discogs images).
- Listening (/api/audio) streams only real .mp3 files inside /music, read-only.
- Changes and lookups are only accepted from the page itself (same-origin check).
- One save or wipe at a time. Do not move or edit the files with another program while
  a save runs, and do not stop the container during a save.
- Wipes are always backed up.

RESTORE (manual): stop the container, take the backup from BACKUP_HOST_PATH/<batch>/<path>
(see write-report.json), copy it back over the song, start the container and list the
folder again. There is no restore button.
