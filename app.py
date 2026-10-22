"""
==================================================================================
jstal-tagger - web server (app.py)                                            V2.1
==================================================================================

A small web application to check and maintain the tags of the MP3 files in the mounted
music folder (/music): browse folders, list the songs with their tags, edit tags (with
online lookups for year, cover, comment, YouTube link and lyrics), listen to a song and
wipe tags.

Writing is off until ENABLE_TAG_WRITES=true and the music folder is mounted writable.
Every write makes a verified backup first (BACKUP_ROOT) and changes only what you
approved. There is no login: use it on your own network only.

  1. Settings
  2. Helpers
  3. Page, user manual and settings for the page
  4. Folders and songs
  5. Covers and audio (pre-listen)
  6. Lookups (+ the YouTube daily counter)
  7. Saving and wiping   prepare -> confirm -> background job -> status

Files: app.py (this file), tags.py (reading / writing tags), lookups.py (online lookups),
templates/index.html, templates/manual.html, static/app.js, static/style.css.

V2 (rebuild of V1): Stage 2 (batch collection) and Stage 3 (review and save) are removed;
their lookups are in the tag editor. 13 Python files became 3. See CHANGES-V2.md.
V2.1: edit mode at start (EDIT_MODE_DEFAULT), pre-listen (/api/audio), YouTube daily
counter, Megalobiz lyrics, Select all / Deselect all in the Wipe window, prefilled wipe
phrase, built-in user manual (/manual). See CHANGES-V2.1.md.
"""

import copy
import json
import os
import secrets
import threading
import time
from pathlib import Path

from flask import Flask, Response, abort, jsonify, render_template, request, send_file, send_from_directory

import lookups
import tags


# =============================================================================
# 1. SETTINGS  (environment variables, see .env.example / docker-compose.yml)
# =============================================================================

VERSION = '2.1'
ROOT = Path(os.environ.get('MUSIC_ROOT', '/music')).resolve()
BACKUP_ROOT = Path(os.environ.get('BACKUP_ROOT', '/backups'))
MAX_BATCH = 100                   # songs per save / wipe
PLAN_SECONDS = 1800               # a prepared save / wipe can be confirmed for 30 minutes
MAX_PLANS = 5
WIPE_PHRASE = 'WIPE SELECTED TAGS'

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024     # covers are made small in the browser first


def writes_enabled():
    return os.environ.get('ENABLE_TAG_WRITES', 'false').strip().lower() == 'true'


def keep_backups():
    """false (default): the backups of a SAVE in which every file was saved and verified are
    removed afterwards. Wipe backups and failed batches are always kept."""
    return os.environ.get('KEEP_BACKUPS', 'false').strip().lower() == 'true'


def edit_mode_default():
    """true (default): the page starts with "Edit tags" switched on. Editing only makes
    drafts in the browser; nothing is written without Save tags and its confirmation."""
    return os.environ.get('EDIT_MODE_DEFAULT', 'true').strip().lower() != 'false'


# =============================================================================
# 2. HELPERS
# =============================================================================

def fail(message, code=400):
    return jsonify(error=str(message)), code


def require_same_origin():
    """Changes and lookups only from this page (no other site can make the browser call us)."""
    origin = request.headers.get('Origin', '').rstrip('/')
    if origin != request.host_url.rstrip('/'):
        raise PermissionError('Same-origin request required. Open the app directly with its own address and port.')
    if not request.is_json:
        raise ValueError('JSON request required.')


def music_path(value):
    try:
        return tags.inside(ROOT, value)
    except ValueError:
        abort(403)


def rel(path):
    value = tags.relative(ROOT, path)
    return '' if value == '.' else value


# State of the save / wipe job and the prepared plans (one gunicorn worker: kept in memory)
lock = threading.Lock()
plans = {}
job = {'state': 'idle', 'action': '', 'batch': '', 'results': [], 'report_file': '', 'error': '',
       'started': '', 'finished': ''}


def busy():
    return job['state'] == 'running'


# =============================================================================
# 3. PAGE, USER MANUAL AND SETTINGS FOR THE PAGE
# =============================================================================

@app.get('/')
def index():
    return render_template('index.html', version=VERSION)


@app.get('/manual')
def manual():
    """The user manual (the book button in the header opens it in a new tab)."""
    return render_template('manual.html', version=VERSION, wipe_phrase=WIPE_PHRASE, max_batch=MAX_BATCH,
                           youtube_limit=lookups.youtube_daily_limit())


@app.get('/favicon.ico')
def favicon():
    return send_from_directory(app.static_folder, 'favicon.ico', mimetype='image/vnd.microsoft.icon')


@app.get('/api/config')
def config():
    return jsonify(version=VERSION, writes_enabled=writes_enabled(), keep_backups=keep_backups(),
                   edit_mode_default=edit_mode_default(),
                   music_root=str(ROOT), max_batch=MAX_BATCH, wipe_phrase=WIPE_PHRASE,
                   fields=list(tags.FIELDS), providers={k: [list(p) for p in v] for k, v in lookups.PROVIDERS.items()},
                   browser_only=sorted(lookups.BROWSER_ONLY), sources=lookups.providers_status(),
                   youtube_quota=lookups.youtube_quota())


# =============================================================================
# 4. FOLDERS AND SONGS
# =============================================================================

@app.get('/api/folders')
def folders():
    path = music_path(request.args.get('path', ''))
    if not path.is_dir():
        return fail('This folder does not exist or cannot be read.')
    try:
        children = []
        for child in path.iterdir():
            try:
                if not child.is_symlink() and child.is_dir() and child.resolve().is_relative_to(ROOT):
                    children.append({'name': child.name, 'path': rel(child)})
            except OSError:
                continue
        return jsonify(path=rel(path), parent=None if path == ROOT else rel(path.parent),
                       folders=sorted(children, key=lambda x: x['name'].casefold()))
    except OSError as exc:
        return fail(exc)


@app.get('/api/scan')
def scan():
    path = music_path(request.args.get('path', ''))
    if not path.is_dir():
        return fail('Invalid folder.')
    recursive = request.args.get('recursive') == '1'
    found, warnings = [], []
    try:
        for base, dirs, files in os.walk(path, followlinks=False, onerror=lambda e: warnings.append(str(e))):
            dirs[:] = sorted(d for d in dirs if not (Path(base) / d).is_symlink()) if recursive else []
            for name in files:
                candidate = Path(base) / name
                if (candidate.suffix.lower() == '.mp3' and not candidate.is_symlink()
                        and candidate.resolve().is_relative_to(ROOT)):
                    found.append(candidate)
        rows = [tags.read_song(ROOT, f) for f in sorted(found, key=lambda f: str(f).casefold())]
        return jsonify(songs=rows, warnings=warnings)
    except OSError as exc:
        return fail(exc)


@app.post('/api/songs')
def refresh_songs():
    """Read some songs again (after a save)."""
    try:
        require_same_origin()
        paths = (request.get_json() or {}).get('paths')
        if not isinstance(paths, list) or len(paths) > MAX_BATCH:
            raise ValueError(f'At most {MAX_BATCH} songs at once.')
        return jsonify(songs=[tags.read_song(ROOT, tags.checked_path(ROOT, p)) for p in paths])
    except PermissionError as exc:
        return fail(exc, 403)
    except Exception as exc:
        return fail(exc)


# =============================================================================
# 5. COVERS AND AUDIO
# =============================================================================

@app.get('/api/audio')
def audio():
    """Pre-listen: stream one MP3 file of the music folder (read only). Range requests are
    supported, so the browser can seek. Only real .mp3 files inside /music are served."""
    path = music_path(request.args.get('path', ''))          # same rules as /api/cover
    if path.suffix.lower() != '.mp3' or not path.is_file():
        abort(404)
    response = send_file(path, mimetype='audio/mpeg', conditional=True, download_name=path.name)
    response.headers['Cache-Control'] = 'private, max-age=3600'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Content-Disposition'] = 'inline'
    return response

@app.get('/api/thumbnail')
def thumbnail():
    try:
        path = tags.checked_path(ROOT, request.args.get('path', ''))
        st = path.stat()
        data = tags.thumbnail(str(path), st.st_mtime_ns, st.st_size)
    except Exception:
        abort(404)
    if data is None:
        abort(404)
    return Response(data, content_type='image/jpeg', headers={'Cache-Control': 'private, max-age=3600'})


@app.get('/api/cover')
def cover():
    path = music_path(request.args.get('path', ''))
    if path.suffix.lower() != '.mp3' or not path.is_file():
        abort(404)
    found = tags.embedded_cover(path)
    if not found:
        abort(404)
    data, mime = found
    if mime not in ('image/jpeg', 'image/png', 'image/webp'):
        abort(415)
    return Response(data, content_type=mime, headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})


# =============================================================================
# 6. LOOKUPS  (read only: a result becomes a draft in the browser when it is picked)
# =============================================================================

@app.get('/api/youtube/quota')
def youtube_quota():
    """The YouTube counter shown in the header: searches today / daily limit."""
    return jsonify(lookups.youtube_quota())


@app.post('/api/lookup')
def lookup():
    provider = None
    try:
        require_same_origin()
        data = request.get_json() or {}
        field, provider = data.get('field'), data.get('provider')
        known = [p for p, _ in lookups.PROVIDERS.get(field, [])]
        if provider not in known or provider in lookups.BROWSER_ONLY:
            raise ValueError('Unsupported lookup source for this field.')
        path = tags.inside(ROOT, data.get('path') if isinstance(data.get('path'), str) else '')
        if not path.is_file() or path.suffix.lower() != '.mp3':
            raise ValueError('Invalid MP3 file.')
        meta = tags.read_song(ROOT, path)
        if meta.get('error'):
            raise ValueError(meta['error'])
        overrides = data.get('metadata') or {}
        if not isinstance(overrides, dict):
            raise ValueError('Invalid search text.')
        for key in ('artist', 'title', 'album'):
            if key in overrides:
                if not isinstance(overrides[key], str) or len(overrides[key]) > 1000:
                    raise ValueError('Invalid search text.')
                meta[key] = overrides[key]
        if not meta.get('artist') or not meta.get('title'):
            raise ValueError('Artist and title are needed for a lookup.')
        result = lookups.field_candidates(field, provider, meta, meta['seconds'])
        if provider == 'youtube':
            result['quota'] = lookups.youtube_quota()      # the header counter is updated after every search
        return jsonify(result)
    except PermissionError as exc:
        return fail(exc, 403)
    except Exception as exc:
        response, code = fail(exc)
        if provider == 'youtube':
            response = jsonify(error=str(exc), quota=lookups.youtube_quota())
        return response, code


@app.post('/api/lookup/cover')
def lookup_cover():
    """Download a cover result (allowed artwork sites only) as a 600 px JPEG draft."""
    try:
        require_same_origin()
        url = (request.get_json() or {}).get('url')
        if not isinstance(url, str) or len(url) > 8192:
            raise ValueError('Invalid cover address.')
        image, info = tags.download_cover(url)
        return jsonify(value=tags.data_url(image), info=info)
    except PermissionError as exc:
        return fail(exc, 403)
    except Exception as exc:
        return fail(exc)


# =============================================================================
# 7. SAVING AND WIPING
# =============================================================================
# 1. prepare: the browser sends the selected songs (+ edited values or the wipe fields).
#    The server checks every file and value and returns a plan (token) with an exact
#    summary. Nothing is written.
# 2. apply: after confirmation in the browser (typed phrase for a wipe) the plan runs as a
#    background job, file by file (tags.write_one). The browser polls /api/write/status.

@app.get('/api/write/status')
def write_status():
    with lock:
        return jsonify(enabled=writes_enabled(), **copy.deepcopy(job))


@app.post('/api/write/prepare')
def prepare():
    try:
        require_same_origin()
        data = request.get_json() or {}
        action, specs = data.get('action'), data.get('items')
        if action not in ('save', 'wipe') or not isinstance(specs, list) or not 1 <= len(specs) <= MAX_BATCH:
            raise ValueError(f'Choose save or wipe and between 1 and {MAX_BATCH} songs.')
        fields = data.get('fields') or []
        if action == 'wipe' and (not isinstance(fields, list) or not fields or any(f not in tags.FIELDS for f in fields)):
            raise ValueError('Choose at least one field to wipe.')
        with lock:
            if busy():
                raise ValueError('A save or wipe is still running.')
        items, seen = [], set()
        for spec in specs:
            relative = spec.get('path')
            if not isinstance(relative, str) or relative in seen:
                raise ValueError('Invalid or duplicate song.')
            seen.add(relative)
            path = tags.checked_path(ROOT, relative)
            source = tags.fingerprint(path)
            claimed = spec.get('source') or {}
            # SHA-256 + size identify the listed file (a browser cannot keep nanosecond times exactly)
            if source['sha256'] != claimed.get('sha256') or source['size'] != claimed.get('size'):
                raise ValueError('File changed since it was listed. List the folder again: ' + relative)
            current = tags.read_tags(path)
            if action == 'wipe':
                if 'other' in fields and tags.has_apev2(path):
                    raise ValueError('APEv2 tags found (not ID3). This wipe refuses the file rather than '
                                     'leave hidden tags behind: ' + relative)
                operation = {'action': 'wipe', 'fields': [f for f in tags.FIELDS if f in fields]}
            else:
                operation = {'action': 'save', 'values': tags.validate_values(spec.get('values'))}
            items.append({'path': relative, 'source': source, 'operation': operation,
                          'summary': tags.plan_summary(current, operation),
                          'warnings': tags.plan_warnings(operation)})
        with lock:
            now = time.monotonic()
            for token in [t for t, p in plans.items() if now > p['expires']]:
                del plans[token]
            if len(plans) >= MAX_PLANS:
                raise ValueError('Too many prepared plans. Cancel them or wait 30 minutes.')
            token = secrets.token_urlsafe(32)
            plans[token] = {'action': action, 'items': items, 'expires': now + PLAN_SECONDS}
        return jsonify(token=token, action=action, enabled=writes_enabled(), expires_in_seconds=PLAN_SECONDS,
                       items=[{'path': i['path'], 'changes': i['summary'], 'warnings': i['warnings']} for i in items])
    except PermissionError as exc:
        return fail(exc, 403)
    except Exception as exc:
        return fail(exc)


@app.post('/api/write/cancel')
def cancel():
    try:
        require_same_origin()
        with lock:
            plans.pop((request.get_json() or {}).get('token', ''), None)
        return jsonify(cancelled=True)
    except PermissionError as exc:
        return fail(exc, 403)
    except Exception as exc:
        return fail(exc)


def worker(plan):
    results = []
    try:
        batch = plan['action'] + '-' + time.strftime('%Y%m%d-%H%M%S') + '-' + secrets.token_hex(4)
        backup_root = BACKUP_ROOT.resolve()
        if backup_root.is_relative_to(ROOT) or ROOT.is_relative_to(backup_root):
            raise ValueError('The backup folder must be separate from the music folder.')
        batch_dir = backup_root / batch
        batch_dir.mkdir(parents=True, exist_ok=False)
        report_file = batch_dir / 'write-report.json'

        def report():
            document = {'version': VERSION, 'action': plan['action'], 'batch': batch,
                        'approved_items': [{'path': i['path'], 'changes': i['summary'], 'warnings': i['warnings']}
                                           for i in plan['items']],
                        'results': results}
            temporary = report_file.with_suffix('.tmp')
            with open(temporary, 'w', encoding='utf-8') as stream:
                json.dump(document, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, report_file)

        report()
        with lock:
            job.update(batch=batch, report_file=str(report_file))
        for item in plan['items']:
            try:
                entry = tags.write_one(ROOT, backup_root, item, batch)
            except Exception as exc:
                entry = {'path': item['path'], 'status': 'failed', 'error': str(exc)}
            results.append(entry)
            with lock:
                job['results'] = copy.deepcopy(results)
            report()
        if plan['action'] == 'save' and not keep_backups():
            try:
                if tags.cleanup_successful_batch(batch_dir, backup_root, results):
                    with lock:
                        job.update(report_file='', backups_removed=True, results=copy.deepcopy(results))
            except Exception as exc:
                with lock:
                    job['cleanup_warning'] = str(exc)
        with lock:
            job.update(state='complete', finished=time.strftime('%Y-%m-%d %H:%M:%S'))
    except Exception as exc:
        with lock:
            job.update(state='failed', error=str(exc), finished=time.strftime('%Y-%m-%d %H:%M:%S'))


@app.post('/api/write/apply')
def apply():
    try:
        require_same_origin()
        if not writes_enabled():
            raise ValueError('Writing is off. Set ENABLE_TAG_WRITES=true and MUSIC_READ_ONLY=false in .env.')
        data = request.get_json() or {}
        with lock:
            if busy():
                raise ValueError('A save or wipe is still running.')
            token = data.get('token', '')
            plan = plans.get(token)
            if not plan or time.monotonic() > plan['expires']:
                raise ValueError('This plan expired or was already used. Prepare it again.')
            if plan['action'] == 'wipe':
                if data.get('confirmation') != WIPE_PHRASE:
                    raise ValueError('The confirmation text does not match.')
            elif data.get('confirmed') is not True:
                raise ValueError('Confirm the save first.')
            plans.pop(token)
            job.clear()
            job.update(state='running', action=plan['action'], batch='', results=[], report_file='', error='',
                       started=time.strftime('%Y-%m-%d %H:%M:%S'), finished='', total=len(plan['items']))
        threading.Thread(target=worker, args=(plan,), daemon=True).start()
        return jsonify(started=True), 202
    except PermissionError as exc:
        return fail(exc, 403)
    except Exception as exc:
        return fail(exc)


if __name__ == '__main__':
    # Development only; the container runs gunicorn (see Dockerfile).
    app.run(host='127.0.0.1', port=int(os.environ.get('PORT', '5080')))
