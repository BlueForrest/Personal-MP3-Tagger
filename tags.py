"""
==================================================================================
jstal-tagger - reading and safely writing MP3 / MP4 tags (tags.py)            V2.3
==================================================================================

Everything that touches the music and video files lives here; the web routes are in
app.py and the online searches in lookups.py.

  1. Settings and field definitions
  2. Paths and fingerprints        safe paths inside the music folder, SHA-256 of a file
  3. Reading                       one song row for the grid, other tags, covers
  4. Images                        uploaded / downloaded covers, thumbnails
  5. Checking edited values
  6. Changing the tags of a file   save (edited fields) or wipe (selected fields)
  7. Safe writing                  backup -> change a temporary copy -> verify -> replace
  8. Self-test                     python tags.py --self-test /music/song.mp3 (or .mp4)

V2.3: MP4 files (.mp4, .m4a, .m4v: iTunes-style atoms) are read, edited and wiped with the
same safety rules as MP3 files, and the ReplayGain / mp3rgain values are read (grid,
details) and NEVER removed by a save or a wipe (see REPLAYGAIN in section 1).

V2 combines the old tag_writer.py, maintenance.py and the reading part of app.py.
The fixes of the three repair scripts of V1 are built in:
  - add_save_diagnostics.py   a refused save says which frames would change
  - fix_save_and_cleanup.py   empty text frames that mutagen drops are not an error;
                              the failed attempt is kept next to the backup; a fully
                              verified SAVE batch removes its backups (KEEP_BACKUPS)
  - preserve_artist_values.py ID3v2.3 tags with several values in one frame (for
                              example two artists) keep them as they are
"""

import base64
import hashlib
import io
import json
import os
import re
import shutil
import stat
import tempfile
import time
from functools import lru_cache
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from mutagen.apev2 import APEv2, APENoHeaderError
from mutagen.id3 import (APIC, COMM, ID3, ID3NoHeaderError, TALB, TBPM, TCON, TDRC, TextFrame,
                         TIT2, TLEN, TPE1, TRCK, TXXX, USLT)
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm, AtomDataType
from PIL import Image, ImageOps


# =============================================================================
# 1. SETTINGS AND FIELD DEFINITIONS
# =============================================================================

Image.MAX_IMAGE_PIXELS = 20_000_000
MAX_FILE = 512 * 1024 * 1024            # larger files are refused
MAX_UPLOAD = 2 * 1024 * 1024            # an uploaded cover (after the browser made it smaller)
MAX_DOWNLOAD = 12 * 1024 * 1024         # a downloaded cover
COVER_SIZE = 600                        # covers are stored at most 600 x 600 (never enlarged)
LRC = re.compile(r"\[\d{1,3}:\d{2}(?:[.:]\d+)?\]")

# The fields of the grid that can be edited or wiped ("other" = every other ID3 frame)
FIELDS = ('artist', 'title', 'album', 'year', 'bpm', 'length', 'track', 'genre',
          'cover', 'lyrics', 'tvshow', 'video', 'comment', 'other')
EDITABLE = set(FIELDS) - {'other'}

# ID3 frame -> field. TXXX:TVSHOW and TXXX:VIDEO are handled in field_for_key().
FRAME_FIELDS = {
    'TPE1': 'artist', 'TIT2': 'title', 'TALB': 'album',
    'TDRC': 'year', 'TYER': 'year', 'TDAT': 'year', 'TIME': 'year',
    'TDOR': 'year', 'TORY': 'year', 'TDRL': 'year',
    'TBPM': 'bpm', 'TLEN': 'length', 'TRCK': 'track', 'TCON': 'genre', 'APIC': 'cover',
    'USLT': 'lyrics', 'SYLT': 'lyrics', 'COMM': 'comment',
}
TEXT_FRAMES = {'artist': TPE1, 'title': TIT2, 'album': TALB, 'year': TDRC,
               'bpm': TBPM, 'length': TLEN, 'track': TRCK, 'genre': TCON}

# Covers may only be downloaded from these HTTPS hosts (MusicBrainz / Discogs artwork)
COVER_HOSTS = ('coverartarchive.org', 'archive.org', 'i.discogs.com', 'images.discogs.com')


# V2.3: the file types that can be listed, played and tagged
MP3_TYPES = ('.mp3',)
MP4_TYPES = ('.mp4', '.m4a', '.m4v')
SUPPORTED = MP3_TYPES + MP4_TYPES

# V2.3: MP4 atom -> field (iTunes-style tags, as written by iTunes, MP3Tag, mp3rgain …)
MP4_FREEFORM = '----:com.apple.iTunes:'
MP4_FIELDS = {
    '\xa9ART': 'artist', '\xa9nam': 'title', '\xa9alb': 'album', '\xa9day': 'year',
    'tmpo': 'bpm', 'trkn': 'track', '\xa9gen': 'genre', 'gnre': 'genre', 'covr': 'cover',
    '\xa9lyr': 'lyrics', 'tvsh': 'tvshow', '\xa9cmt': 'comment',
}
MP4_TEXT_ATOMS = {'artist': '\xa9ART', 'title': '\xa9nam', 'album': '\xa9alb', 'year': '\xa9day',
                  'genre': '\xa9gen', 'lyrics': '\xa9lyr', 'tvshow': 'tvsh', 'comment': '\xa9cmt'}
MP4_VIDEO_KEY = MP4_FREEFORM + 'VIDEO'          # the VIDEO column (an MP3 has TXXX:VIDEO)
MP4_NO_FIELDS = {'length'}                      # MP4 has no stored-duration tag like TLEN

# V2.3: REPLAYGAIN. mp3rgain (https://github.com/M-Igashi/mp3rgain, checked with version
# 3.12) changes the volume INSIDE the audio frames (lossless) and stores what it did:
#   MP3: ID3v2 TXXX:REPLAYGAIN_TRACK_GAIN / _TRACK_PEAK / _ALBUM_GAIN / _ALBUM_PEAK /
#        REPLAYGAIN_ALGORITHM, and an APEv2 tag (end of the file) with MP3GAIN_UNDO,
#        MP3GAIN_MINMAX, MP3GAIN_ALBUM_MINMAX. With "-s i" the MP3GAIN_* values are TXXX
#        frames too, with "-s a" everything is in the APEv2 tag.
#   MP4: freeform atoms ----:com.apple.iTunes:replaygain_track_gain … and mp3rgain_undo,
#        mp3rgain_minmax.
# MP3GAIN_UNDO / mp3rgain_undo is what "mp3rgain -u" needs to restore the original volume.
# These values (and RVA2 frames and the iTunes "iTunNORM" Sound Check value, which do the
# same job for other players) belong to the protected field 'replaygain': it is not in
# FIELDS, so no save and no wipe can touch it, and the write check refuses a file in
# which one of them would change. The APEv2 tag itself is never written.
REPLAYGAIN_PREFIXES = ('REPLAYGAIN_', 'MP3GAIN_', 'MP3RGAIN_')


def is_replaygain_name(name):
    """A ReplayGain / mp3rgain / Sound Check value name (upper or lower case)."""
    name = str(name or '').strip().upper()
    return name.startswith(REPLAYGAIN_PREFIXES) or name == 'ITUNNORM'


def user_agent():
    return os.environ.get('LOOKUP_USER_AGENT', '').strip() or 'jstal-tagger/2.3'


def file_kind(path):
    """'mp3' or 'mp4' (by extension), or None for other files."""
    suffix = Path(path).suffix.lower()
    return 'mp3' if suffix in MP3_TYPES else 'mp4' if suffix in MP4_TYPES else None


def field_for_key(key, kind='mp3'):
    """The grid field a tag key belongs to: an ID3 frame key ('TPE1', 'TXXX:VIDEO' …) or, for
    kind 'mp4', an MP4 atom ('\xa9ART', '----:com.apple.iTunes:VIDEO' …). 'other' for
    everything without a column, 'replaygain' for the protected ReplayGain values."""
    if kind == 'mp4':
        if key.startswith('----:'):
            name = key.rsplit(':', 1)[-1]
            if is_replaygain_name(name):
                return 'replaygain'
            if key.upper() == MP4_VIDEO_KEY.upper():
                return 'video'
            return 'other'
        return MP4_FIELDS.get(key, 'other')
    frame = key.split(':', 1)[0]
    if frame == 'TXXX':
        desc = key[5:]
        if is_replaygain_name(desc):
            return 'replaygain'
        if desc.upper() in ('TVSHOW', 'VIDEO'):
            return desc.lower()
    if frame in ('RVA2', 'RVAD', 'XRVA'):
        return 'replaygain'
    if frame == 'COMM' and is_replaygain_name(key.split(':')[1] if key.count(':') >= 1 else ''):
        return 'replaygain'                                 # COMM:iTunNORM:eng
    return FRAME_FIELDS.get(frame, 'other')


# =============================================================================
# 2. PATHS AND FINGERPRINTS
# =============================================================================

def digest_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def fingerprint(path):
    """SHA-256, size and modification time of a file. Used to make sure a file did not
    change between listing it and writing it."""
    before = Path(path).stat()
    if before.st_size > MAX_FILE:
        raise ValueError('File is larger than 512 MiB.')
    digest = digest_file(path)
    after = Path(path).stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise ValueError('File changed while reading it.')
    return {'sha256': digest, 'size': after.st_size, 'mtime_ns': after.st_mtime_ns}


def inside(root, relative):
    """A path inside the music folder (folders or files). Raises ValueError outside it."""
    root = Path(root).resolve()
    path = (root / (relative or '')).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Path is outside the music folder.')
    return path


def checked_path(root, relative):
    """An MP3 / MP4 file that may be written: inside the library folder, no symlinks, no hard links."""
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError('Expected a relative music path.')
    root = Path(root).resolve()
    path = root
    for part in Path(relative).parts:
        if part in ('..', '.'):
            raise ValueError('Path traversal is not allowed.')
        path = path / part
        if path.is_symlink():
            raise ValueError('Symlink paths are not writable.')
    path = path.resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.suffix.lower() not in SUPPORTED:
        raise ValueError('Invalid MP3 / MP4 path.')
    if path.stat().st_nlink != 1:
        raise ValueError('Hard-linked files are not writable.')
    return path


def relative(root, path):
    return Path(path).relative_to(Path(root).resolve()).as_posix()


def payload_digest(path, kind=None):
    """SHA-256 of the audio part only. It must be the same before and after a tag change.
    MP3: everything except the ID3v2 tag in front and the ID3v1 tag at the end (so also an
    APEv2 tag, which is never written). MP4: the content of the media data ('mdat') boxes,
    the audio and video themselves (the tags are in the 'moov' box)."""
    if (kind or file_kind(path)) == 'mp4':
        return mp4_media_digest(path)
    size = Path(path).stat().st_size
    start, end = 0, size
    with open(path, 'rb') as stream:
        header = stream.read(10)
        if header[:3] == b'ID3':
            if len(header) != 10 or any(x > 127 for x in header[6:10]):
                raise ValueError('Invalid ID3 header.')
            if header[3] == 4 and header[5] & 0x10:
                raise ValueError('ID3 footer tags require manual handling.')
            start = 10 + sum(x << shift for x, shift in zip(header[6:10], (21, 14, 7, 0)))
        if size >= 128:
            stream.seek(-128, 2)
            if stream.read(3) == b'TAG':
                end -= 128
        if start >= end:
            raise ValueError('No usable MP3 audio found.')
        stream.seek(start)
        remaining = end - start
        h = hashlib.sha256()
        while remaining:
            data = stream.read(min(1024 * 1024, remaining))
            if not data:
                raise ValueError('Truncated file.')
            h.update(data)
            remaining -= len(data)
    return h.hexdigest()


def mp4_media_digest(path):
    """SHA-256 of the content of every top-level 'mdat' box of an MP4 file, in file order.
    Saving tags may move these boxes (mutagen then corrects the chunk offsets), but their
    content must stay byte-for-byte the same."""
    size = Path(path).stat().st_size
    h = hashlib.sha256()
    found = 0
    with open(path, 'rb') as stream:
        position = 0
        while position + 8 <= size:
            stream.seek(position)
            header = stream.read(8)
            length, name = int.from_bytes(header[:4], 'big'), header[4:8]
            start = position + 8
            if length == 1:                                 # 64-bit size
                length = int.from_bytes(stream.read(8), 'big')
                start += 8
            elif length == 0:                               # box runs to the end of the file
                length = size - position
            if length < start - position or position + length > size:
                raise ValueError('Damaged MP4 box structure.')
            if name == b'mdat':
                found += 1
                h.update(b'mdat')
                stream.seek(start)
                remaining = position + length - start
                while remaining:
                    data = stream.read(min(1024 * 1024, remaining))
                    if not data:
                        raise ValueError('Truncated file.')
                    h.update(data)
                    remaining -= len(data)
            position += length
    if not found:
        raise ValueError('No media data (mdat) found in this MP4 file.')
    return h.hexdigest()


# =============================================================================
# 3. READING
# =============================================================================

def read_tags(path, kind=None):
    """The tags of a file. MP3: the ID3v2 tag (an empty tag when the file has none; ID3v1
    is not merged). MP4 (V2.3): the MP4 tag dictionary (empty when the file has none)."""
    if (kind or file_kind(path)) == 'mp4':
        audio = MP4(path)
        if audio.tags is None:
            audio.add_tags()
        return audio.tags
    try:
        return ID3(path, load_v1=False)
    except ID3NoHeaderError:
        return ID3()


def unknown_frames(tags):
    """ID3 frames mutagen cannot read (an MP4 tag has none)."""
    return list(getattr(tags, 'unknown_frames', None) or [])


def _text(value):
    if isinstance(value, bytes):
        return value.decode('utf-8', 'replace')
    return str(value)


def mp4_text(key, value):
    """One MP4 tag as text (several values joined with NUL, shown as " / " in the page)."""
    if key == 'covr':
        return f'{len(value)} picture(s)' if len(value) != 1 else 'image/' + (
            'png' if getattr(value[0], 'imageformat', None) == MP4Cover.FORMAT_PNG else 'jpeg') + f', {len(value[0])} bytes'
    if key in ('trkn', 'disk'):
        try:
            number, total = value[0]
            return f'{number}/{total}' if total else str(number)
        except Exception:
            return str(value)
    if isinstance(value, (list, tuple)):
        return '\u0000'.join(_text(item) for item in value)
    return _text(value)


def tag_text(tags, key, kind):
    """The text of one tag key, for the grid, the details and the save check."""
    value = tags[key]
    if kind == 'mp4':
        return mp4_text(key, value)
    if key.startswith('APIC'):
        return f'{value.mime}, {len(value.data)} bytes'
    return str(value)


def other_info(tags, kind='mp3'):
    keys = [k for k in tags if field_for_key(k, kind) == 'other']
    unknown = len(unknown_frames(tags))
    return {'count': len(keys) + unknown, 'keys': keys, 'unknown_frames': unknown}


def ape_items(path):
    """{name: text} of the APEv2 tag of an MP3 ({} when it has none)."""
    try:
        return {str(k): str(v) for k, v in APEv2(path).items()}
    except APENoHeaderError:
        return {}


def ape_other_items(path):
    """APEv2 items that are NOT ReplayGain / mp3rgain values (a wipe of "Other tags"
    refuses such a file: the APEv2 tag is never written)."""
    return sorted(k for k in ape_items(path) if not is_replaygain_name(k))


def _rg_number(text, decibel=False):
    try:
        value = str(text).strip()
        if decibel:
            value = re.sub(r'\s*db\s*$', '', value, flags=re.IGNORECASE)
        number = float(value.replace(',', '.'))
        if number != number or abs(number) > 1000:
            return None
        return number
    except (TypeError, ValueError):
        return None


def read_replaygain(path, tags, kind):
    """V2.3: the ReplayGain / mp3rgain values of a file (read only), or None.
    {'track_gain', 'track_peak', 'album_gain', 'album_peak' (numbers or None), 'algorithm',
     'undo' (mp3rgain changed the audio: how to undo it), 'applied' (True when an undo value
     exists), 'values': [[place, name, text], ...]} - place is ID3, APEv2 or MP4."""
    found = []
    if kind == 'mp4':
        for key in tags:
            if key.startswith('----:') and is_replaygain_name(key.rsplit(':', 1)[-1]):
                found.append(['MP4', key.rsplit(':', 1)[-1], mp4_text(key, tags[key])])
    else:
        for key in tags:
            if field_for_key(key, 'mp3') == 'replaygain':
                frame = tags[key]
                name = key[5:] if key.startswith('TXXX:') else key
                found.append(['ID3', name, str(frame) if key.split(':', 1)[0] in ('TXXX', 'COMM') else frame._pprint()])
        for name, value in ape_items(path).items():
            if is_replaygain_name(name):
                found.append(['APEv2', name, value])
    if not found:
        return None
    found.sort(key=lambda item: (item[0], item[1].upper()))
    values = {}
    for _, name, text in found:
        values.setdefault(name.upper(), text.split('\u0000')[0])
    undo = values.get('MP3GAIN_UNDO') or values.get('MP3RGAIN_UNDO') or ''
    return {'track_gain': _rg_number(values.get('REPLAYGAIN_TRACK_GAIN'), True),
            'track_peak': _rg_number(values.get('REPLAYGAIN_TRACK_PEAK')),
            'album_gain': _rg_number(values.get('REPLAYGAIN_ALBUM_GAIN'), True),
            'album_peak': _rg_number(values.get('REPLAYGAIN_ALBUM_PEAK')),
            'algorithm': values.get('REPLAYGAIN_ALGORITHM', ''),
            'undo': undo, 'applied': bool(undo), 'values': found}


def _rg_columns(row):
    rg = row.get('rg')
    gain = rg and (rg['track_gain'] if rg['track_gain'] is not None else rg['album_gain'])
    if rg and gain is not None:
        row['rg_gain'] = ('🎚️ ' if rg['applied'] else '') + f'{gain:+.2f} dB' + ('' if rg['track_gain'] is not None else ' (album)')
    else:
        row['rg_gain'] = '🎚️' if rg else ''
    peak = rg and (rg['track_peak'] if rg['track_peak'] is not None else rg['album_peak'])
    row['rg_peak'] = f'{peak:.6f}' if rg and peak is not None else ''


def read_song(root, path):
    """One row of the grid. The file is hashed once; when it changes while it is read the
    row gets an error instead of values that may not belong together."""
    path = Path(path)
    kind = file_kind(path)
    row = {'path': relative(root, path), 'filename': path.name, 'error': '', 'kind': kind,
           'type': path.suffix[1:].upper(), 'rg': None, 'rg_gain': '', 'rg_peak': ''}
    try:
        before = path.stat()
        source = fingerprint(path)
        if kind == 'mp4':
            _read_mp4_row(path, row)
        else:
            _read_mp3_row(path, row)
        seconds = round(row.pop('_length'))
        row['duration'] = f'{seconds // 60}:{seconds % 60:02d}'
        tags = read_tags(path)
        row['all_tags'] = {k: tag_text(tags, k, kind) for k in tags}
        row['other_info'] = other_info(tags, kind)
        row['other'] = row['other_info']['count']
        row['rg'] = read_replaygain(path, tags, kind)
        _rg_columns(row)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError('File changed while listing. List the folder again.')
        row['_source'] = source
    except Exception as exc:
        row['error'] = str(exc)
    return row


def _read_mp3_row(path, row):
    audio = MP3(path)
    tags = audio.tags

    def value(key):
        frame = tags.get(key) if tags else None
        return str(frame) if frame is not None else ''

    row.update(artist=value('TPE1'), title=value('TIT2'), album=value('TALB'),
               year=value('TDRC'), bpm=value('TBPM'), track=value('TRCK'),
               genre=value('TCON'), length_tag=value('TLEN'))
    row['_length'] = audio.info.length
    row['seconds'] = round(audio.info.length, 3)
    row['cover'] = bool(tags and tags.getall('APIC'))
    lyrics = tags.getall('USLT') if tags else []
    lyric = next((f for f in lyrics if f.lang == 'eng'), lyrics[0] if lyrics else None)
    row['lyrics_text'] = lyric.text if lyric else ''
    row['lyrics'] = ('LRC' if LRC.search(lyric.text) else 'Plain') if lyric else ''
    comments = [f for f in tags.getall('COMM') if not is_replaygain_name(f.desc)] if tags else []
    comment = next((f for f in comments if f.lang == 'eng' and not f.desc), comments[0] if comments else None)
    row['comment'] = str(comment) if comment else ''
    custom = {f.desc.upper(): str(f) for f in tags.getall('TXXX')} if tags else {}
    row['tvshow'] = custom.get('TVSHOW', '')
    row['video'] = custom.get('VIDEO', '')


def _read_mp4_row(path, row):
    """V2.3: the grid row of an MP4 / M4A / M4V file (iTunes-style atoms)."""
    audio = MP4(path)
    tags = audio.tags or {}

    def value(key):
        return mp4_text(key, tags[key]) if key in tags else ''

    genre = value('\xa9gen')
    if not genre and 'gnre' in tags:                      # old numeric genre (ID3v1 number + 1)
        genre = value('gnre')
    video = ''
    for key in tags:
        if key.upper() == MP4_VIDEO_KEY.upper():
            video = mp4_text(key, tags[key])
    row.update(artist=value('\xa9ART'), title=value('\xa9nam'), album=value('\xa9alb'),
               year=value('\xa9day'), bpm=value('tmpo'), track=value('trkn'), genre=genre,
               length_tag='', comment=value('\xa9cmt'), tvshow=value('tvsh'), video=video)
    row['_length'] = audio.info.length
    row['seconds'] = round(audio.info.length, 3)
    row['cover'] = bool(tags.get('covr'))
    lyrics = value('\xa9lyr')
    row['lyrics_text'] = lyrics
    row['lyrics'] = ('LRC' if LRC.search(lyrics) else 'Plain') if lyrics else ''


def embedded_cover(path):
    """(data, mime) of the front cover (or the first picture), or None."""
    if file_kind(path) == 'mp4':
        pictures = read_tags(path).get('covr') or []
        if not pictures:
            return None
        picture = pictures[0]
        return bytes(picture), 'image/png' if picture.imageformat == MP4Cover.FORMAT_PNG else 'image/jpeg'
    pictures = read_tags(path).getall('APIC')
    picture = next((f for f in pictures if f.type == 3), pictures[0] if pictures else None)
    if picture is None:
        return None
    return picture.data, picture.mime


# =============================================================================
# 4. IMAGES
# =============================================================================

def to_cover_jpeg(data):
    """Any JPEG / PNG / WEBP -> JPEG of at most 600 x 600 (not enlarged), transparency on white."""
    with Image.open(io.BytesIO(data)) as image:
        if image.format not in ('JPEG', 'PNG', 'WEBP'):
            raise ValueError('Use a JPEG, PNG or WEBP image.')
        image = ImageOps.exif_transpose(image)
        image.thumbnail((COVER_SIZE, COVER_SIZE), Image.Resampling.LANCZOS)
        canvas = Image.new('RGB', image.size, 'white')
        if image.mode in ('RGBA', 'LA', 'P') or 'transparency' in image.info:
            rgba = image.convert('RGBA')
            canvas.paste(rgba, mask=rgba.getchannel('A'))
        else:
            canvas.paste(image.convert('RGB'))
        output = io.BytesIO()
        canvas.save(output, 'JPEG', quality=92)
        return output.getvalue(), canvas.size


def data_url(jpeg):
    return 'data:image/jpeg;base64,' + base64.b64encode(jpeg).decode('ascii')


def normalise_upload(value):
    """A cover chosen in the browser (data URL) -> JPEG bytes."""
    if not isinstance(value, str) or not value.startswith('data:image/') or ';base64,' not in value:
        raise ValueError('Expected an uploaded image.')
    try:
        data = base64.b64decode(value.split(';base64,', 1)[1], validate=True)
    except Exception:
        raise ValueError('Invalid image data.') from None
    if len(data) > MAX_UPLOAD:
        raise ValueError('Cover image is larger than 2 MiB.')
    return to_cover_jpeg(data)[0]


def download_cover(url):
    """Download a cover found by a lookup. Only HTTPS artwork hosts are allowed, also after
    redirects. Returns (jpeg bytes, info)."""
    def permitted(value):
        parsed = urlparse(value)
        host = (parsed.hostname or '').lower()
        return (parsed.scheme == 'https' and not parsed.username and not parsed.password
                and parsed.port in (None, 443)
                and (host in COVER_HOSTS or host.endswith('.archive.org')))

    for _ in range(6):
        parsed = urlparse(url)
        host = (parsed.hostname or '').lower()
        # Cover Art Archive data can contain old http:// links: use https for those hosts only
        if (parsed.scheme == 'http' and (host in ('coverartarchive.org', 'archive.org') or host.endswith('.archive.org'))
                and not parsed.username and not parsed.password and parsed.port in (None, 80)):
            url = parsed._replace(scheme='https', netloc=host).geturl()
        if not permitted(url):
            raise ValueError('Cover address is not one of the allowed HTTPS artwork sites.')
        with requests.get(url, stream=True, allow_redirects=False, timeout=(8, 25),
                          headers={'User-Agent': user_agent()}) as response:
            if response.status_code in (301, 302, 303, 307, 308):
                url = urljoin(url, response.headers.get('Location', ''))
                continue
            response.raise_for_status()
            data = bytearray()
            for chunk in response.iter_content(65536):
                data.extend(chunk)
                if len(data) > MAX_DOWNLOAD:
                    raise ValueError('Cover image is larger than 12 MiB.')
        jpeg, (width, height) = to_cover_jpeg(bytes(data))
        return jpeg, {'width': width, 'height': height, 'bytes': len(jpeg),
                      'sha256': hashlib.sha256(jpeg).hexdigest()}
    raise ValueError('Too many redirects for the cover image.')


@lru_cache(maxsize=256)
def thumbnail(path, mtime_ns, size):
    """Small JPEG of the embedded cover for the grid (cached per file version)."""
    cover = embedded_cover(path)
    if not cover:
        return None
    with Image.open(io.BytesIO(cover[0])) as image:
        image = ImageOps.exif_transpose(image)
        image.thumbnail((96, 96), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        image.convert('RGB').save(output, 'JPEG', quality=80)
        return output.getvalue()


# =============================================================================
# 5. CHECKING EDITED VALUES
# =============================================================================

def validate_values(values, kind='mp3'):
    """Edited fields from the browser -> clean values (cover -> JPEG bytes, '' = delete).
    kind 'mp4' (V2.3): no stored duration, BPM a whole number, track numbers up to 65535."""
    if not isinstance(values, dict) or not values or any(k not in EDITABLE for k in values):
        raise ValueError('No supported edited fields supplied.')
    result = {}
    for field, value in values.items():
        if not isinstance(value, str):
            raise ValueError('Field values must be text.')
        limit = 500_000 if field == 'lyrics' else 3_000_000 if field == 'cover' else 10_000
        if len(value) > limit:
            raise ValueError('Field is too long: ' + field)
        if field not in ('lyrics', 'comment', 'cover'):
            value = value.strip()
        if value and field == 'year' and not re.fullmatch(r'\d{4}', value):
            raise ValueError('Year must be four digits or empty.')
        if value and field == 'bpm' and not re.fullmatch(r'\d+(?:\.\d+)?', value):
            raise ValueError('BPM must be a number or empty.')
        if value and field == 'length' and not re.fullmatch(r'\d+', value):
            raise ValueError('Stored length (TLEN) must be milliseconds or empty.')
        if value and field == 'track' and not re.fullmatch(r'\d+(?:/\d+)?', value):
            raise ValueError('Track must be a number, number/total, or empty.')
        if kind == 'mp4':
            if field in MP4_NO_FIELDS:
                raise ValueError('MP4 files have no stored duration (TLEN) tag; the duration is measured from the audio.')
            if value and field == 'bpm' and not re.fullmatch(r'\d{1,5}', value):
                raise ValueError('An MP4 file stores BPM as a whole number (for example 120).')
            if value and field in ('bpm', 'track') and any(int(n) > 65535 for n in value.split('/')):
                raise ValueError(FIELD_NAMES.get(field, field) + ' is too large for an MP4 tag (at most 65535).')
        result[field] = normalise_upload(value) if field == 'cover' and value else value
    return result


FIELD_NAMES = {'bpm': 'BPM', 'track': 'Track'}


def has_apev2(path):
    try:
        APEv2(path)
    except APENoHeaderError:
        return False
    return True


# =============================================================================
# 6. CHANGING THE TAGS OF A FILE
# =============================================================================
# An "operation" is {'action': 'save', 'values': {field: value}} or
# {'action': 'wipe', 'fields': [field, ...]}.

def targeted(key, operation, kind='mp3'):
    """Does this tag key (ID3 frame or MP4 atom) belong to a field the operation changes?
    The ReplayGain values (field 'replaygain') are never targeted."""
    fields = operation['fields'] if operation['action'] == 'wipe' else operation['values']
    return field_for_key(key, kind) in fields


def legacy_block(path):
    """The 128-byte ID3v1 tag at the end of the file, or None."""
    with open(path, 'rb') as stream:
        stream.seek(0, 2)
        if stream.tell() >= 128:
            stream.seek(-128, 2)
            data = stream.read()
            if data[:3] == b'TAG':
                return bytearray(data)
    return None


def apply_operation(path, operation, kind=None):
    """kind: 'mp3' / 'mp4' of the original (the temporary copy has a .tmp name)."""
    if (kind or file_kind(path)) == 'mp4':
        apply_mp4_operation(path, operation)
        return
    tags = read_tags(path)
    version = tags.version[1] if tags.version else 4
    if version not in (3, 4):
        raise ValueError('Only ID3v2.3, ID3v2.4 or untagged files are supported.')
    legacy = legacy_block(path)
    for key in list(tags):
        if targeted(key, operation, 'mp3'):
            del tags[key]
    if operation['action'] == 'wipe' and 'other' in operation['fields']:
        tags.unknown_frames = []
    values = operation.get('values', {}) if operation['action'] == 'save' else {}
    encoding = 1 if version == 3 else 3
    for field, value in values.items():
        if value in ('', None):
            continue                                    # empty = the field is deleted
        if field in TEXT_FRAMES:
            tags.add(TEXT_FRAMES[field](encoding=encoding, text=[value]))
        elif field in ('tvshow', 'video'):
            tags.add(TXXX(encoding=encoding, desc=field.upper(), text=[value]))
        elif field == 'comment':
            tags.add(COMM(encoding=encoding, lang='eng', desc='', text=[value]))
        elif field == 'lyrics':
            tags.add(USLT(encoding=encoding, lang='eng', desc='', text=value))
        elif field == 'cover':
            tags.add(APIC(encoding=encoding, mime='image/jpeg', type=3, desc='', data=value))
    if version == 3:
        tags.update_to_v23()
    else:
        tags.update_to_v24()
    # ID3v2.3 normally joins several values with "/". Keep frames that already hold
    # several values exactly as they are (for example two artists).
    keep_multiple = version == 3 and any(
        isinstance(getattr(frame, 'text', None), list) and len(frame.text) > 1 for frame in tags.values())
    tags.save(path, v2_version=version, v1=0, v23_sep=None if keep_multiple else '/')
    if legacy is not None:
        # The old ID3v1 copy of a changed / wiped field is updated too, so players do not
        # fall back to stale values. Untouched ID3v1 fields stay as they are.
        changed = set(operation['fields'] if operation['action'] == 'wipe' else values)
        for field, start, count in (('title', 3, 30), ('artist', 33, 30), ('album', 63, 30), ('year', 93, 4)):
            if field in changed:
                text = values.get(field, '')
                legacy[start:start + count] = text.encode('latin-1', errors='replace')[:count].ljust(count, b'\x00')
        if 'comment' in changed:
            count = 28 if legacy[125] == 0 and legacy[126] != 0 else 30
            legacy[97:97 + count] = values.get('comment', '').encode('latin-1', errors='replace')[:count].ljust(count, b'\x00')
        if 'track' in changed and legacy[125] == 0:
            number = values.get('track', '').split('/')[0]
            legacy[126] = min(int(number), 255) if number else 0
        if 'genre' in changed:
            legacy[127] = 255                           # "no genre": the ID3v2 text is used
        with open(path, 'ab') as stream:
            stream.write(legacy)


def apply_mp4_operation(path, operation):
    """V2.3: the same operation on an MP4 / M4A / M4V file (iTunes-style atoms). Only the
    tag box changes; the media data is checked afterwards (payload_digest)."""
    audio = MP4(path)
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags
    for key in list(tags):
        if targeted(key, operation, 'mp4'):
            del tags[key]
    values = operation.get('values', {}) if operation['action'] == 'save' else {}
    for field, value in values.items():
        if value in ('', None):
            continue                                    # empty = the field is deleted
        if field in MP4_TEXT_ATOMS:
            tags[MP4_TEXT_ATOMS[field]] = [value]
        elif field == 'bpm':
            tags['tmpo'] = [int(value)]
        elif field == 'track':
            number, _, total = value.partition('/')
            tags['trkn'] = [(int(number), int(total or 0))]
        elif field == 'video':
            tags[MP4_VIDEO_KEY] = [MP4FreeForm(value.encode('utf-8'), AtomDataType.UTF8)]
        elif field == 'cover':
            tags['covr'] = [MP4Cover(value, imageformat=MP4Cover.FORMAT_JPEG)]
        else:
            raise ValueError('This field cannot be written to an MP4 file: ' + field)
    audio.save()


def verify_operation(path, operation, kind=None):
    kind = kind or file_kind(path)
    tags = read_tags(path, kind)
    if operation['action'] == 'wipe':
        remaining = [k for k in tags if targeted(k, operation, kind)]
        if remaining:
            raise ValueError('Wipe verification failed: ' + ', '.join(remaining))
        if 'other' in operation['fields'] and unknown_frames(tags):
            raise ValueError('Unknown ID3 frames remain.')
    else:
        for field, value in operation['values'].items():
            keys = [k for k in tags if field_for_key(k, kind) == field]
            if value == '':
                if keys:
                    raise ValueError('Field deletion verification failed: ' + field)
                continue
            if kind == 'mp4':
                if field == 'cover':
                    ok = len(keys) == 1 and len(tags['covr']) == 1 and bytes(tags['covr'][0]) == value
                else:
                    ok = len(keys) == 1 and mp4_text(keys[0], tags[keys[0]]) == value
            elif field == 'cover':
                ok = len(keys) == 1 and tags[keys[0]].data == value
            elif field == 'lyrics':
                ok = len(keys) == 1 and tags[keys[0]].text == value
            else:
                ok = len(keys) == 1 and str(tags[keys[0]]) == value
            if not ok:
                raise ValueError('Verification of the new value failed: ' + field)
    (MP4 if kind == 'mp4' else MP3)(path)


def plan_summary(tags, operation, kind='mp3', path=None):
    """What the confirmation window shows for one file."""
    if operation['action'] == 'wipe':
        kept = [k for k in tags if field_for_key(k, kind) == 'replaygain']
        if path is not None and kind == 'mp3':
            kept += ['APEv2:' + k for k in ape_items(path) if is_replaygain_name(k)]
        return {'wipe_fields': operation['fields'],
                'remove_frames': [k for k in tags if targeted(k, operation, kind)],
                'remove_unknown_frames': len(unknown_frames(tags)) if 'other' in operation['fields'] else 0,
                'protected_fields': [f for f in FIELDS if f not in operation['fields']],
                'kept_replaygain': kept}
    changes = {}
    for field, value in operation['values'].items():
        old = [('Embedded picture' if field == 'cover' else tag_text(tags, k, kind)) for k in tags if field_for_key(k, kind) == field]
        if field == 'cover' and value:
            new = {'jpeg_bytes': len(value), 'sha256': hashlib.sha256(value).hexdigest()}
        else:
            new = value
        changes[field] = {'old': old, 'new': new}
    return {'edited_fields': changes}


def plan_warnings(operation, kind='mp3'):
    if operation['action'] == 'wipe':
        return ['Other tags (ID3 frames / MP4 atoms without a column) and unknown frames are only removed when '
                '"Other tags" is ticked. The measured audio duration does not change.',
                'ReplayGain and mp3rgain values (volume normalisation, also the undo information) are never wiped.']
    values = operation['values']
    warnings = []
    if 'cover' in values:
        warnings.append('A cover change replaces all embedded pictures with one front cover (or removes them when cleared).')
    if 'lyrics' in values:
        warnings.append('A lyrics change replaces all USLT/SYLT lyrics, also timestamped ones.')
    if 'comment' in values:
        warnings.append('A comment change replaces all COMM comments.')
    if 'tvshow' in values:
        warnings.append('TVSHOW is changed independently from VIDEO.')
    return warnings


# =============================================================================
# 7. SAFE WRITING
# =============================================================================

def frame_value(value):
    if isinstance(value, (MP4Cover, MP4FreeForm)):
        return {'bytes': len(value), 'sha256': hashlib.sha256(bytes(value)).hexdigest(),
                'format': int(getattr(value, 'imageformat', getattr(value, 'dataformat', 0)) or 0)}
    if isinstance(value, bytes):
        return {'bytes': len(value), 'sha256': hashlib.sha256(value).hexdigest()}
    if isinstance(value, (list, tuple)):
        return [frame_value(x) for x in value]
    if isinstance(value, dict):
        return {str(k): frame_value(v) for k, v in value.items()}
    if isinstance(value, (int, float, str)) or value is None:
        return value
    return str(value)


def tag_signatures(tags):
    """Every frame / atom as comparable text (the ID3 text encoding is ignored)."""
    if not isinstance(tags, ID3):                     # MP4 tag: {atom: list of values}
        return {k: json.dumps(frame_value(list(v) if isinstance(v, (list, tuple)) else v),
                              sort_keys=True, ensure_ascii=False) for k, v in tags.items()}
    return {k: json.dumps({a: frame_value(v) for a, v in vars(f).items() if a != 'encoding'},
                          sort_keys=True, ensure_ascii=False)
            for k, f in tags.items()}


def fsync_directory(directory):
    fd = os.open(directory, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_one(root, backup_root, item, batch):
    """Change one file. Order: verified backup -> change a temporary copy -> check that only
    the approved frames changed and the audio is identical -> replace the original.
    A failure leaves the original untouched."""
    path = checked_path(root, item['path'])
    if fingerprint(path) != item['source']:
        raise ValueError('File changed since it was listed. List the folder again.')
    root = Path(root).resolve()
    backup_root = Path(backup_root).resolve()
    if backup_root.is_relative_to(root) or root.is_relative_to(backup_root):
        raise ValueError('The backup folder must be separate from the music folder.')
    operation = item['operation']
    kind = file_kind(path)
    source_stat = path.stat()

    # 1. verified backup copy with the same permissions / owner
    # V2.3: <batch>/music/<path> or <batch>/videos/<path> (the library the file belongs to)
    backup = backup_root / batch / item.get('library', 'music') / item['path']
    backup.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'rb') as src, open(backup, 'xb') as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)
        dst.flush()
        os.fsync(dst.fileno())
    if digest_file(backup) != item['source']['sha256']:
        raise ValueError('Backup verification failed.')
    shutil.copystat(path, backup)
    if hasattr(os, 'chown'):
        st = backup.stat()
        if (st.st_uid, st.st_gid) != (source_stat.st_uid, source_stat.st_gid):
            os.chown(backup, source_stat.st_uid, source_stat.st_gid)
    directory = backup.parent
    while True:
        fsync_directory(directory)
        if directory == backup_root:
            break
        directory = directory.parent

    # 2. change a temporary copy next to the original
    before = read_tags(path)
    before_signatures = tag_signatures(before)
    unknown_before = unknown_frames(before)
    payload_before = payload_digest(path, kind)
    fd, name = tempfile.mkstemp(prefix='.jstal-tagger-', suffix='.tmp', dir=path.parent)
    os.close(fd)
    temp = Path(name)
    replaced = False
    try:
        shutil.copyfile(path, temp)
        apply_operation(temp, operation, kind)
        verify_operation(temp, operation, kind)

        # 3. only the approved frames may differ
        after = read_tags(temp, kind)
        after_signatures = tag_signatures(after)
        # (the ReplayGain values are never targeted, so they are always compared here)
        untouched_before = {k: v for k, v in before_signatures.items() if not targeted(k, operation, kind)}
        untouched_after = {k: v for k, v in after_signatures.items() if not targeted(k, operation, kind)}
        # mutagen does not write text frames whose text is empty: that is not a change
        normalized_empty = sorted(k for k in untouched_before
                                  if k not in after_signatures and isinstance(before[k], TextFrame)
                                  and str(before[k]) == '')
        for key in normalized_empty:
            del untouched_before[key]
        unknown_allowed = operation['action'] == 'wipe' and 'other' in operation['fields']
        if untouched_before != untouched_after or (unknown_before != unknown_frames(after) and not unknown_allowed):
            changed = sorted(k for k in set(untouched_before) & set(untouched_after)
                             if untouched_before[k] != untouched_after[k])
            details = {'removed_frames': sorted(set(untouched_before) - set(untouched_after)),
                       'added_frames': sorted(set(untouched_after) - set(untouched_before)),
                       'changed_frames': changed,
                       'changed_frame_values': {k: {'before': json.loads(untouched_before[k]),
                                                    'after': json.loads(untouched_after[k])} for k in changed},
                       'unknown_frames_before': len(unknown_before),
                       'unknown_frames_after': len(unknown_frames(after)),
                       'tag_version': list(before.version) if kind == 'mp3' else 'MP4'}
            raise ValueError('Tags that were not approved would change; the original was kept. '
                             + json.dumps(details, ensure_ascii=True, sort_keys=True))
        if payload_digest(temp, kind) != payload_before:
            raise ValueError('The audio part would change; the original was kept.')
        if kind == 'mp3' and ape_items(temp) != ape_items(path):
            raise ValueError('The APEv2 tag (mp3rgain undo information) would change; the original was kept.')
        if kind == 'mp4':
            old_info, new_info = MP4(path).info, MP4(temp).info
            if (round(old_info.length, 3), old_info.channels, old_info.sample_rate) != \
                    (round(new_info.length, 3), new_info.channels, new_info.sample_rate):
                raise ValueError('The MP4 stream information would change; the original was kept.')

        # 4. same permissions, owner and a newer modification time, then replace
        shutil.copystat(path, temp)
        if hasattr(os, 'chown'):
            st = temp.stat()
            if (st.st_uid, st.st_gid) != (source_stat.st_uid, source_stat.st_gid):
                os.chown(temp, source_stat.st_uid, source_stat.st_gid)
        if stat.S_IMODE(temp.stat().st_mode) != stat.S_IMODE(source_stat.st_mode):
            os.chmod(temp, stat.S_IMODE(source_stat.st_mode))
        now = max(time.time_ns(), (source_stat.st_mtime_ns // 1_000_000_000 + 1) * 1_000_000_000)
        os.utime(temp, ns=(source_stat.st_atime_ns, now))
        with open(temp, 'rb') as stream:
            os.fsync(stream.fileno())
        if fingerprint(path) != item['source']:
            raise ValueError('File changed just before replacing it; the original was kept.')
        after_digest = digest_file(temp)
        os.replace(temp, path)
        replaced = True
        try:
            fsync_directory(path.parent)
            warning = ''
        except OSError as exc:
            warning = 'Replaced, but the folder could not be synced to disk: ' + str(exc)
        return {'path': item['path'], 'status': 'saved', 'backup': str(backup),
                'before_sha256': item['source']['sha256'], 'after_sha256': after_digest,
                'fields': item['summary'], 'warning': warning,
                'normalized_empty_frames': normalized_empty}
    finally:
        if not replaced and temp.exists():
            # keep the failed attempt next to the verified backup, for diagnosis
            try:
                shutil.copy2(temp, backup.with_name(backup.name + '.failed-attempt.tmp'))
                temp.unlink()
            except OSError:
                pass


def cleanup_successful_batch(batch_dir, backup_root, results):
    """Remove the backups of one batch when every file was saved and verified without a
    warning. Never touches other batches."""
    if not results or any(r.get('status') != 'saved' or r.get('warning') for r in results):
        return False
    directory = Path(batch_dir)
    if directory.is_symlink() or directory.resolve().parent != Path(backup_root).resolve():
        raise ValueError('Refusing to clean up outside the current backup batch.')
    shutil.rmtree(directory)
    for result in results:
        result['backup'] = None
        result['backup_removed'] = True
    return True


# =============================================================================
# 8. SELF-TEST  (a temporary copy only - the given file is never changed)
# =============================================================================

def self_test(mp3):
    source = Path(mp3).resolve()
    original = digest_file(source)
    with tempfile.TemporaryDirectory(prefix='jstal-tagger-selftest-') as folder:
        root, backups = Path(folder) / 'music', Path(folder) / 'backups'
        root.mkdir()
        backups.mkdir()
        kind = file_kind(source)
        if kind is None:
            raise SystemExit('Give an .mp3, .mp4, .m4a or .m4v file.')
        name = 'test' + source.suffix.lower()
        target = root / name
        shutil.copy2(source, target)
        payload = payload_digest(target)
        replaygain = read_replaygain(target, read_tags(target), kind)
        operation = {'action': 'save', 'values': {'comment': 'jstal-tagger V2.3 temporary-copy test.'}}
        item = {'path': name, 'source': fingerprint(target), 'operation': operation,
                'summary': plan_summary(read_tags(target), operation, kind)}
        result = write_one(root, backups, item, 'test')
        assert result['status'] == 'saved', result
        assert payload_digest(target) == payload, 'audio changed'
        assert digest_file(backups / 'test' / 'music' / name) == original, 'backup differs'
        comment = read_tags(target)['\xa9cmt'][0] if kind == 'mp4' else str(read_tags(target)['COMM::eng'])
        assert comment == operation['values']['comment']
        assert read_replaygain(target, read_tags(target), kind) == replaygain, 'ReplayGain values changed'
        assert digest_file(source) == original, 'the original file changed'
    print('PASS: temporary-copy tag write (' + kind.upper() + '), verified backup, unchanged audio, '
          + ('ReplayGain values kept, ' if replaygain else '') + 'original file untouched.')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='jstal-tagger tag functions')
    parser.add_argument('--self-test', metavar='FILE', help='write a test comment into a TEMPORARY COPY of this MP3 / MP4 file')
    args = parser.parse_args()
    if args.self_test:
        self_test(args.self_test)
    else:
        parser.print_help()
