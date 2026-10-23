JSTAL-TAGGER V2.3 - MP3 & MP4 TAG MAINTENANCE
=============================================

A small web application to check and maintain the tags of the MP3 and MP4 files in a music
folder and a video folder on the server: browse folders, list the files with their tags,
listen to a file, edit tags (with online lookups for year, cover, comment, YouTube link and
lyrics) and wipe tags. ReplayGain values (volume normalisation with mp3rgain) are shown and
never removed. It runs in Docker on port 5080. There is no login: use it on your own
network only, never on the internet.

What changed since V2.2: see CHANGES-V2.3.md. Since V2.1: CHANGES-V2.2.md. Since V2:
CHANGES-V2.1.md. Since V1: CHANGES-V2.md.
The user manual is built into the app: the ℹ️ button (top right) shows it in a window over the
page (or open http://<server>:5080/manual.pdf). The same manual is next to this file as
"jstal-tagger V2.3 - User Manual.pdf" and ".docx".


FILES
-----
  app.py                 web server: pages, folders, songs, audio, lookups, save / wipe jobs
  tags.py                reading and safely writing the MP3 / MP4 tags, ReplayGain (+ self-test)
  lookups.py             MusicBrainz, Cover Art Archive, Wikipedia, Discogs, LRCLIB, Megalobiz,
                         YouTube (+ the daily YouTube counter)
  templates/index.html   the page (markup only)
  static/app.js          everything the page does
  static/style.css       the look (dark and light theme)
  static/MP3Tag.png      logo, static/favicon.ico  browser icon
  static/manual/         the user manual the app shows: user-manual.pdf (/manual.pdf) and
                         user-manual.docx (/manual.docx). Same files as the two
                         "jstal-tagger V2.3 - User Manual" files in this folder.
  jstal-tagger V2.3 - User Manual.docx / .pdf   the user manual (Word / PDF)
  Dockerfile, docker-compose.yml, requirements.txt, .env.example


INSTALL / UPDATE FROM V2.2 (or V2.1 / V2)
-----------------------------------------
1. Back up the current application folder, including .env.
2. Replace the application files with the files of this folder (app.py, tags.py, lookups.py,
   templates/, static/ including static/manual/, Dockerfile, docker-compose.yml,
   requirements.txt). From V2.1: templates/manual.html is no longer used (delete it).
3. Add to your .env (V2.3, REQUIRED - docker compose refuses to start without it):
      VIDEO_HOST_PATH=/srv/videos
      VIDEO_READ_ONLY=true        (false = the videos can be tagged; also needs ENABLE_TAG_WRITES=true)
   Optionally set LOOKUP_USER_AGENT to jstal-tagger/2.3. Coming from V2: the optional V2.1
   settings are EDIT_MODE_DEFAULT, YOUTUBE_DAILY_LIMIT and MEGALOBIZ_API_KEY.
4. docker compose down
   docker compose up -d --build
5. Ctrl+F5 in the browser (http://<server>:5080).
See CHANGES-V2.3.md for the details and how to roll back.

Updating from V1: follow the V2 instructions in CHANGES-V2.md first (remove the old V1
Python files and static/stage1.js).

Check on the server (nothing is written):
  docker compose run --rm --no-deps --entrypoint python jstal-tagger -c "import app; print('Application imports successfully.')"
  docker compose run --rm --no-deps --entrypoint python jstal-tagger tags.py --self-test "/music/SOME SONG.mp3"
  docker compose run --rm --no-deps --entrypoint python jstal-tagger tags.py --self-test "/videos/SOME VIDEO.mp4"
The self-test writes only a temporary copy, never the file itself (MP3 or MP4).


SETTINGS (.env)
---------------
  MUSIC_HOST_PATH        music folder on the server (mounted as /music, "Music" in the page)
  VIDEO_HOST_PATH        (V2.3, required) video folder on the server (mounted as /videos,
                         "Videos" in the page)
  BACKUP_HOST_PATH       backup folder on the server, OUTSIDE the music and video folders (/backups)
  ENABLE_TAG_WRITES      false = read-only (default). true + MUSIC_READ_ONLY=false = writing on
                         in Music; true + VIDEO_READ_ONLY=false = writing on in Videos
  MUSIC_READ_ONLY        true (default) mounts the music folder read-only
  VIDEO_READ_ONLY        (V2.3) true (default) mounts the video folder read-only
  KEEP_BACKUPS           false (default): the backups of a save in which every file was saved
                         and verified are removed afterwards. Wipe backups and saves with a
                         failed file are always kept. true = keep every backup.
  EDIT_MODE_DEFAULT      true (default, V2.1): the page starts with "Edit tags" switched on.
                         false = start switched off (as in V2). Editing only makes drafts.
  LOOKUP_USER_AGENT      required: "jstal-tagger/2.3 (mailto:you@example.com)"
  YOUTUBE_API_KEY        YouTube Data API key (empty = YouTube lookup off)
  YOUTUBE_REGION         two letters, default BE
  YOUTUBE_PREFERRED_CHANNELS  channel names ranked higher, separated by |
  YOUTUBE_DAILY_LIMIT    100 (default, V2.1): YouTube searches per day; the free quota of
                         10 000 units allows about 100 searches. See THE YOUTUBE COUNTER.
  DISCOGS_TOKEN          Discogs personal token (empty = Discogs lookup off)
  DISCOGS_MASTER_OVERRIDES  {"artist|title": master id} for songs Discogs matches wrongly
  MEGALOBIZ_API_KEY      optional (V2.1). Megalobiz works without a key (30 requests a
                         minute, 1000 a day); a free key raises that to 300 / 20 000.

The header shows "Writing on" or "Read-only" (for the library that is open) and, when a
YouTube key is set, the YouTube counter. Discogs / YouTube without a key are shown as "not configured" in the tag editor.


USING IT
--------
Libraries   (V2.3) "🎵 Music" and "🎬 Videos" (top left) switch between the two folders.
            Both list .mp3, .mp4, .m4a and .m4v files. The browser remembers the last
            library, folder and "Include subfolders": the next visit opens there again
            (click "List files" to read it). A folder that no longer exists opens the top
            of that library.
Folders     Click a folder chip to open it, the breadcrumb or ↑ to go back. "List files"
            reads the files (tick "Include subfolders" to include all sub-folders).
Grid        Click a column title to sort. "Filter loaded songs" shows only songs that
            contain all typed words. "Columns" hides columns (remembered by the browser).
            Click a filename for all its tags (ID3 or MP4), the number under "Other tags"
            for the tags without a column, a cover to see it large. "Type" shows MP3, MP4,
            M4A or M4V.
ReplayGain  (V2.3, read only) "RG gain" and "RG peak" show the ReplayGain track values that
            mp3rgain (or another normaliser) stored. 🎚️ in front of the gain = mp3rgain
            changed the volume inside the file (it stored undo information); the gain is
            then only the small rest (less than 0.75 dB). Without 🎚️ only the tags were
            written (--tags-only). Click the value for all ReplayGain values and where
            they are stored (ID3, APEv2 or MP4). These values can not be edited, and
            Save / Wipe never remove or change them.
MP4 files   (V2.3) Edited like MP3 files, with the iTunes-style MP4 tags (Artist ©ART,
            Title ©nam, Album ©alb, Year ©day, BPM tmpo, Track trkn, Genre ©gen, Cover covr,
            Lyrics ©lyr, YouTube URL tvsh, VIDEO ----:com.apple.iTunes:VIDEO, Comment ©cmt).
            An MP4 file has no stored duration (TLEN): that cell cannot be edited; BPM is a
            whole number. A video plays its sound in the player bar.
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
            tags". "Other tags" removes every tag without a column (URLs, key, disc number,
            rating, compilation, custom TXXX, unknown frames), but (V2.3) never the
            ReplayGain / mp3rgain values: the window lists them under "Kept". Save or
            discard drafts first.
Report      "Last report" shows the result per file of the last save or wipe since the
            container started (download as JSON). The full report of a kept batch is
            BACKUP_ROOT/<batch>/write-report.json.
Export      "Export selected" downloads the tags of the selected songs as JSON.
Manual      (V2.2) The ℹ️ button in the header shows the user manual (PDF) in a window
            over the page. Close it with ✕, Esc or a click outside the window: you are
            back on the same page, with your selection and drafts. "Open in new tab"
            shows the PDF in its own tab, "Download .docx" gives the Word file.
            /manual (the V2.1 address) opens the PDF. To change the manual: edit the
            .docx, save it as PDF too, copy both to static/manual/ as user-manual.docx
            and user-manual.pdf and rebuild (docker compose up -d --build).


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
- MP3: only ID3v2.3 / ID3v2.4 (and untagged) files. ID3v1 copies of changed fields are
  updated. The APEv2 tag (where mp3rgain keeps its undo information) is never written;
  a wipe of "Other tags" refuses a file whose APEv2 tag holds other items than ReplayGain
  / mp3rgain values.
- MP4 (V2.3): only the tag box changes; the media data (audio and video) is checked
  byte-for-byte, and duration, channels and sample rate must stay the same.
- ReplayGain values (TXXX:REPLAYGAIN_*, MP3GAIN_*, RVA2, iTunNORM, the APEv2 tag, MP4
  replaygain_* / mp3rgain_*) are compared before the original is replaced: a change refuses
  the file.
- Refused: symlinks, hard-linked files, files larger than 512 MiB, paths outside /music
  and /videos.
- Covers are only downloaded from HTTPS artwork sites (Cover Art Archive / archive.org,
  Discogs images).
- Listening (/api/audio) streams only real .mp3/.mp4/.m4a/.m4v files inside /music or
  /videos, read-only.
- Changes and lookups are only accepted from the page itself (same-origin check).
- One save or wipe at a time. Do not move or edit the files with another program while
  a save runs, and do not stop the container during a save.
- Wipes are always backed up.

RESTORE (manual): stop the container, take the backup from
BACKUP_HOST_PATH/<batch>/music/<path> or BACKUP_HOST_PATH/<batch>/videos/<path> (V2.3; see
write-report.json), copy it back over the file, start the container and list the folder
again. There is no restore button. (Batches made by V2.2 or older have no music/ level.)
