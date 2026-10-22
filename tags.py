"""
==================================================================================
jstal-tagger - reading and safely writing MP3 tags (tags.py)                  V2.1
==================================================================================

Everything that touches the MP3 files lives here; the web routes are in app.py and
the online searches in lookups.py.

  1. Settings and field definitions
  2. Paths and fingerprints        safe paths inside the music folder, SHA-256 of a file
  3. Reading                       one song row for the grid, other tags, covers
  4. Images                        uploaded / downloaded covers, thumbnails
  5. Checking edited values
  6. Changing the tags of a file   save (edited fields) or wipe (selected fields)
  7. Safe writing                  backup -> change a temporary copy -> verify -> replace
  8. Self-test                     python tags.py --self-test /music/song.mp3

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


def user_agent():
    return os.environ.get('LOOKUP_USER_AGENT', '').strip() or 'jstal-tagger/2.1'


def field_for_key(key):
    """The grid field an ID3 frame key belongs to ('other' for everything not listed)."""
    if key.startswith('TXXX:') and key[5:].upper() in ('TVSHOW', 'VIDEO'):
        return key[5:].lower()
    return FRAME_FIELDS.get(key.split(':', 1)[0], 'other')


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
    """An MP3 file that may be written: inside the music folder, no symlinks, no hard links."""
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
    if not path.is_relative_to(root) or not path.is_file() or path.suffix.lower() != '.mp3':
        raise ValueError('Invalid MP3 path.')
    if path.stat().st_nlink != 1:
        raise ValueError('Hard-linked files are not writable.')
    return path


def relative(root, path):
    return Path(path).relative_to(Path(root).resolve()).as_posix()


def payload_digest(path):
    """SHA-256 of the audio part only (without the ID3v2 tag in front and the ID3v1 tag at
    the end). It must be the same before and after a tag change."""
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


# =============================================================================
# 3. READING
# =============================================================================

def read_tags(path):
    """The ID3v2 tag of a file (an empty tag when the file has none). ID3v1 is not merged."""
    try:
        return ID3(path, load_v1=False)
    except ID3NoHeaderError:
        return ID3()


def other_info(tags):
    keys = [k for k in tags if field_for_key(k) == 'other']
    return {'count': len(keys) + len(tags.unknown_frames), 'keys': keys,
            'unknown_frames': len(tags.unknown_frames)}


def read_song(root, path):
    """One row of the grid. The file is hashed once; when it changes while it is read the
    row gets an error instead of values that may not belong together."""
    path = Path(path)
    row = {'path': relative(root, path), 'filename': path.name, 'error': ''}
    try:
        before = path.stat()
        source = fingerprint(path)
        audio = MP3(path)
        tags = audio.tags

        def value(key):
            frame = tags.get(key) if tags else None
            return str(frame) if frame is not None else ''

        row.update(artist=value('TPE1'), title=value('TIT2'), album=value('TALB'),
                   year=value('TDRC'), bpm=value('TBPM'), track=value('TRCK'),
                   genre=value('TCON'), length_tag=value('TLEN'))
        seconds = round(audio.info.length)
        row['duration'] = f'{seconds // 60}:{seconds % 60:02d}'
        row['seconds'] = round(audio.info.length, 3)
        row['cover'] = bool(tags and tags.getall('APIC'))
        lyrics = tags.getall('USLT') if tags else []
        lyric = next((f for f in lyrics if f.lang == 'eng'), lyrics[0] if lyrics else None)
        row['lyrics_text'] = lyric.text if lyric else ''
        row['lyrics'] = ('LRC' if LRC.search(lyric.text) else 'Plain') if lyric else ''
        comments = tags.getall('COMM') if tags else []
        comment = next((f for f in comments if f.lang == 'eng' and not f.desc), comments[0] if comments else None)
        row['comment'] = str(comment) if comment else ''
        custom = {f.desc.upper(): str(f) for f in tags.getall('TXXX')} if tags else {}
        row['tvshow'] = custom.get('TVSHOW', '')
        row['video'] = custom.get('VIDEO', '')
        row['all_tags'] = {k: (f'{v.mime}, {len(v.data)} bytes' if k.startswith('APIC') else str(v))
                           for k, v in tags.items()} if tags else {}
        row['other_info'] = other_info(read_tags(path))
        row['other'] = row['other_info']['count']
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError('File changed while listing. List the folder again.')
        row['_source'] = source
    except Exception as exc:
        row['error'] = str(exc)
    return row


def embedded_cover(path):
    """(data, mime) of the front cover (or the first picture), or None."""
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

def validate_values(values):
    """Edited fields from the browser -> clean values (cover -> JPEG bytes, '' = delete)."""
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
        result[field] = normalise_upload(value) if field == 'cover' and value else value
    return result


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

def targeted(key, operation):
    """Does this ID3 frame belong to a field the operation changes?"""
    fields = operation['fields'] if operation['action'] == 'wipe' else operation['values']
    return field_for_key(key) in fields


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


def apply_operation(path, operation):
    tags = read_tags(path)
    version = tags.version[1] if tags.version else 4
    if version not in (3, 4):
        raise ValueError('Only ID3v2.3, ID3v2.4 or untagged files are supported.')
    legacy = legacy_block(path)
    for key in list(tags):
        if targeted(key, operation):
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


def verify_operation(path, operation):
    tags = read_tags(path)
    if operation['action'] == 'wipe':
        remaining = [k for k in tags if targeted(k, operation)]
        if remaining:
            raise ValueError('Wipe verification failed: ' + ', '.join(remaining))
        if 'other' in operation['fields'] and tags.unknown_frames:
            raise ValueError('Unknown ID3 frames remain.')
    else:
        for field, value in operation['values'].items():
            frames = [tags[k] for k in tags if field_for_key(k) == field]
            if value == '':
                if frames:
                    raise ValueError('Field deletion verification failed: ' + field)
                continue
            if field == 'cover':
                ok = len(frames) == 1 and frames[0].data == value
            elif field == 'lyrics':
                ok = len(frames) == 1 and frames[0].text == value
            else:
                ok = len(frames) == 1 and str(frames[0]) == value
            if not ok:
                raise ValueError('Verification of the new value failed: ' + field)
    MP3(path)


def plan_summary(tags, operation):
    """What the confirmation window shows for one file."""
    if operation['action'] == 'wipe':
        return {'wipe_fields': operation['fields'],
                'remove_frames': [k for k in tags if targeted(k, operation)],
                'remove_unknown_frames': len(tags.unknown_frames) if 'other' in operation['fields'] else 0,
                'protected_fields': [f for f in FIELDS if f not in operation['fields']]}
    changes = {}
    for field, value in operation['values'].items():
        old = [('Embedded picture' if field == 'cover' else str(tags[k])) for k in tags if field_for_key(k) == field]
        if field == 'cover' and value:
            new = {'jpeg_bytes': len(value), 'sha256': hashlib.sha256(value).hexdigest()}
        else:
            new = value
        changes[field] = {'old': old, 'new': new}
    return {'edited_fields': changes}


def plan_warnings(operation):
    if operation['action'] == 'wipe':
        return ['Other ID3 frames and unknown frames are only removed when "Other tags" is ticked. '
                'The measured audio duration does not change.']
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
    """Every frame as comparable text (the text encoding is ignored)."""
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
    source_stat = path.stat()

    # 1. verified backup copy with the same permissions / owner
    backup = backup_root / batch / item['path']
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
    unknown_before = list(before.unknown_frames)
    payload_before = payload_digest(path)
    fd, name = tempfile.mkstemp(prefix='.jstal-tagger-', suffix='.tmp', dir=path.parent)
    os.close(fd)
    temp = Path(name)
    replaced = False
    try:
        shutil.copyfile(path, temp)
        apply_operation(temp, operation)
        verify_operation(temp, operation)

        # 3. only the approved frames may differ
        after = read_tags(temp)
        after_signatures = tag_signatures(after)
        untouched_before = {k: v for k, v in before_signatures.items() if not targeted(k, operation)}
        untouched_after = {k: v for k, v in after_signatures.items() if not targeted(k, operation)}
        # mutagen does not write text frames whose text is empty: that is not a change
        normalized_empty = sorted(k for k in untouched_before
                                  if k not in after_signatures and isinstance(before[k], TextFrame)
                                  and str(before[k]) == '')
        for key in normalized_empty:
            del untouched_before[key]
        unknown_allowed = operation['action'] == 'wipe' and 'other' in operation['fields']
        if untouched_before != untouched_after or (unknown_before != list(after.unknown_frames) and not unknown_allowed):
            changed = sorted(k for k in set(untouched_before) & set(untouched_after)
                             if untouched_before[k] != untouched_after[k])
            details = {'removed_frames': sorted(set(untouched_before) - set(untouched_after)),
                       'added_frames': sorted(set(untouched_after) - set(untouched_before)),
                       'changed_frames': changed,
                       'changed_frame_values': {k: {'before': json.loads(untouched_before[k]),
                                                    'after': json.loads(untouched_after[k])} for k in changed},
                       'unknown_frames_before': len(unknown_before),
                       'unknown_frames_after': len(after.unknown_frames),
                       'tag_version': list(before.version)}
            raise ValueError('Tags that were not approved would change; the original was kept. '
                             + json.dumps(details, ensure_ascii=True, sort_keys=True))
        if payload_digest(temp) != payload_before:
            raise ValueError('The audio part would change; the original was kept.')

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
# 8. SELF-TEST  (a temporary copy only - the given MP3 is never changed)
# =============================================================================

def self_test(mp3):
    source = Path(mp3).resolve()
    original = digest_file(source)
    with tempfile.TemporaryDirectory(prefix='jstal-tagger-selftest-') as folder:
        root, backups = Path(folder) / 'music', Path(folder) / 'backups'
        root.mkdir()
        backups.mkdir()
        target = root / 'test.mp3'
        shutil.copy2(source, target)
        payload = payload_digest(target)
        operation = {'action': 'save', 'values': {'comment': 'jstal-tagger V2.1 temporary-copy test.'}}
        item = {'path': 'test.mp3', 'source': fingerprint(target), 'operation': operation,
                'summary': plan_summary(read_tags(target), operation)}
        result = write_one(root, backups, item, 'test')
        assert result['status'] == 'saved', result
        assert payload_digest(target) == payload, 'audio changed'
        assert digest_file(backups / 'test' / 'test.mp3') == original, 'backup differs'
        assert str(read_tags(target)['COMM::eng']) == operation['values']['comment']
        assert digest_file(source) == original, 'the original file changed'
    print('PASS: temporary-copy tag write, verified backup, unchanged audio, original file untouched.')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='jstal-tagger tag functions')
    parser.add_argument('--self-test', metavar='MP3', help='write a test comment into a TEMPORARY COPY of this MP3')
    args = parser.parse_args()
    if args.self_test:
        self_test(args.self_test)
    else:
        parser.print_help()
