"""
==================================================================================
jstal-tagger - online lookups for the tag editor (lookups.py)                 V2.3
==================================================================================

The "Lookup Data" part of the tag editor asks these sources for a value of one field:

  field     sources
  -------   ------------------------------------------------------------
  year      MusicBrainz, Discogs
  cover     MusicBrainz (Cover Art Archive), Discogs   (+ MusicHoarders, opened in the browser)
  comment   MusicBrainz + Wikipedia, Discogs artist profile
  tvshow    YouTube Data API, Discogs release videos
  lyrics    LRCLIB, Megalobiz (synchronised LRC sheets)

Nothing here writes tags: a result only becomes a draft in the browser when you pick it.

  1. Shared helpers                text matching
  2. MusicBrainz                   recordings, first-release year, Cover Art Archive
  3. Comment                       MusicBrainz artist facts + English Wikipedia introduction
  4. Discogs                       masters / releases, years, covers, videos, artist profile
  5. LRCLIB                        synchronised (LRC) or plain lyrics
  5b. Megalobiz                    synchronised (LRC) lyrics (public API)
  6. YouTube                       music video candidates + the daily search counter
  7. Field lookup                  one field + one source -> list of {value, label, source}

V2 combines the old enrichment.py, comment_lookup.py, discogs_lookup.py, lyrics_lookup.py,
youtube_lookup.py and field_lookup.py. The matching rules are unchanged from V1.
V2.1 adds Megalobiz as a second lyrics source and counts the YouTube searches per day
(the Data API allows about 100 searches a day; the counter is kept in a small file so it
survives a restart of the container).
Every source identifies itself with LOOKUP_USER_AGENT, is rate limited and results are
cached in memory for six hours (lost when the container restarts).
"""

import copy
import json
import os
import re
import threading
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

import requests

CACHE_SECONDS = 21600


# =============================================================================
# 1. SHARED HELPERS
# =============================================================================

def user_agent():
    return os.environ.get('LOOKUP_USER_AGENT', '').strip()


def title_without_trailing_year(value):
    """'Take On Me (1985)' -> 'Take On Me' (for matching only; the tag is not changed)."""
    return re.sub(r'\s*\((?:18|19|20)\d{2}\)\s*$', '', value or '').strip()


def norm(value):
    return re.sub(r'[^\w]+', ' ', (value or '').casefold()).strip()


def similarity(a, b):
    return SequenceMatcher(None, norm(a), norm(b)).ratio()


def retry_delay(value, default):
    """Retry-After in seconds or as an HTTP date. None when it cannot be read."""
    if not value:
        return default
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        try:
            target = parsedate_to_datetime(value)
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            return max(0.0, (target - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


class Cache:
    """A small in-memory cache (six hours, at most 1000 entries)."""

    def __init__(self):
        self.data = {}
        self.lock = threading.Lock()

    def get(self, key):
        with self.lock:
            saved = self.data.get(key)
            if saved and time.monotonic() - saved[0] < CACHE_SECONDS:
                return copy.deepcopy(saved[1])
        return None

    def put(self, key, value):
        with self.lock:
            if len(self.data) >= 1000:
                self.data.pop(next(iter(self.data)))
            self.data[key] = (time.monotonic(), copy.deepcopy(value))


# =============================================================================
# 2. MUSICBRAINZ
# =============================================================================

_mb_lock = threading.Lock()
_mb_last = 0.0


def get_json(url, params=None):
    """GET JSON (MusicBrainz, Cover Art Archive, Wikipedia, Wikidata). MusicBrainz requests
    are spaced 1.1 s apart; busy servers are retried twice. 404 -> None."""
    global _mb_last
    headers = {'User-Agent': user_agent() or 'jstal-tagger/2.3 (metadata lookup)'}
    for attempt in range(3):
        if 'musicbrainz.org/ws/' in url:
            with _mb_lock:
                time.sleep(max(0, 1.1 - (time.monotonic() - _mb_last)))
                _mb_last = time.monotonic()
        response = requests.get(url, params=params, headers=headers, timeout=(8, 25))
        if response.status_code == 404:
            return None
        if response.status_code in (429, 502, 503, 504) and attempt < 2:
            time.sleep(2 ** (attempt + 1))
            continue
        response.raise_for_status()
        return response.json()


def quoted(value):
    return '"' + re.sub(r'([+\-!(){}\[\]^"~*?:\\/])', r'\\\1', value) + '"'


def artist_name(record):
    return ''.join(c.get('name', c.get('artist', {}).get('name', '')) + c.get('joinphrase', '')
                   for c in record.get('artist-credit', []) if isinstance(c, dict))


def lookup_musicbrainz(meta, duration, include_cover=True):
    """Recording candidates (ranked on title, artist and duration), the proposed first-release
    year and, when asked, a front cover from the Cover Art Archive."""
    result = {'status': 'needs_review', 'candidates': [], 'cover': None, 'warnings': []}
    if not meta.get('artist') or not meta.get('title'):
        result['status'] = 'skipped'
        result['warnings'] = ['Artist or title missing; no filename guessing.']
        return result
    original_title = meta['title']
    clean_title = title_without_trailing_year(original_title)

    def search_recordings(search_title):
        query = 'recording:' + quoted(search_title) + ' AND artist:' + quoted(meta['artist'])
        return get_json('https://musicbrainz.org/ws/2/recording/', {'query': query, 'fmt': 'json', 'limit': 12}) or {}

    data = search_recordings(original_title)
    if not data.get('recordings') and clean_title and clean_title != original_title:
        data = search_recordings(clean_title)
        result['warnings'].append('MusicBrainz search retried without the trailing year: ' + repr(clean_title)
                                  + '. The title in the file is not changed.')
    candidates = []
    for rec in data.get('recordings', []):
        artist = artist_name(rec)
        title = rec.get('title', '')
        delta = abs(rec['length'] / 1000 - duration) if rec.get('length') is not None else None
        textscore = (0.55 * similarity(clean_title, title_without_trailing_year(title))
                     + 0.45 * similarity(meta['artist'], artist))
        rank = textscore * 100 + (10 if delta is not None and delta <= 3 else 0) - (min(delta / 5, 30) if delta is not None else 0)
        candidates.append({'id': rec['id'], 'title': title, 'artist': artist,
                           'disambiguation': rec.get('disambiguation', ''),
                           'first_release_date': rec.get('first-release-date', ''),
                           'duration_difference_seconds': round(delta, 2) if delta is not None else None,
                           'duration_seconds': rec.get('length', 0) / 1000 if rec.get('length') is not None else None,
                           'ranking_score': round(rank, 1), 'source': 'https://musicbrainz.org/recording/' + rec['id'],
                           'releases': rec.get('releases', []), 'text_similarity': round(textscore, 3)})
    candidates.sort(key=lambda c: c['ranking_score'], reverse=True)
    result['candidates'] = candidates[:5]
    if not candidates:
        result['status'] = 'not_found'
        return result
    best = candidates[0]
    result['suggested_recording'] = best['source']
    date = best['first_release_date']
    result['proposed_year'] = date[:4] if re.match(r'^\d{4}', date) else ''
    close = [c for c in candidates if c['ranking_score'] >= best['ranking_score'] - 3]
    years = {c['first_release_date'][:4] for c in close if c['first_release_date']}
    strong = (best['text_similarity'] >= .96 and best['duration_difference_seconds'] is not None
              and best['duration_difference_seconds'] <= 3)
    result['status'] = 'strong_candidate' if strong and len(close) == 1 else 'needs_review'
    if len(close) > 1:
        result['warnings'].append('Several similarly ranked recordings; verify the version.')
    if len(years) > 1:
        result['warnings'].append('Similarly ranked recordings have different first-release years.')
    result['warnings'].append('Year is the first-release date of the recording, not necessarily of this album edition. '
                              'Ranking is heuristic, not a probability.')
    releases = best['releases']
    releases.sort(key=lambda r: (-similarity(meta.get('album', ''), r.get('title', '')), r.get('date', '9999')))
    for release in (releases[:3] if include_cover else []):
        try:
            art = get_json('https://coverartarchive.org/release/' + release['id'])
            front = next((im for im in (art or {}).get('images', []) if im.get('front')), None)
            if front:
                thumbs = front.get('thumbnails', {})
                result['cover'] = {'url': thumbs.get('500') or thumbs.get('large') or front['image'],
                                   'original_url': front['image'], 'release_title': release.get('title', ''),
                                   'source': 'https://musicbrainz.org/release/' + release['id']}
                break
        except Exception as exc:
            result['warnings'].append('Cover lookup failed: ' + str(exc))
    return result


# =============================================================================
# 3. COMMENT  (MusicBrainz artist facts + the English Wikipedia introduction)
# =============================================================================

_comment_cache = Cache()
MB_UUID = re.compile(r'^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$')

# Confirmed MusicBrainz artist identities for names that match several artists.
# Keys: lower case without spaces or punctuation (see normalized()).
ARTIST_OVERRIDES = {
    'milkinc': '67bb73ee-e70c-4bfd-9bf1-e7a09768f9a9',
}


def normalized(value):
    value = unicodedata.normalize('NFKC', value or '').casefold()
    return re.sub(r'[^\w]+', '', value)


def cached_get(url, params):
    key = (url, tuple(sorted(params.items())))
    saved = _comment_cache.get(key)
    if saved is not None:
        return saved
    data = get_json(url, params)
    if data is not None:
        _comment_cache.put(key, data)
    return data or {}


def mb_entity(kind, identifier):
    if not MB_UUID.fullmatch(identifier):
        raise ValueError('Invalid MusicBrainz identifier.')
    params = {'fmt': 'json'}
    if kind == 'recording':
        params['inc'] = 'artists'
    elif kind == 'artist':
        params['inc'] = 'url-rels'
    return cached_get(f'https://musicbrainz.org/ws/2/{kind}/{identifier}', params)


def find_artist_by_name(name):
    escaped = re.sub(r'([+\-!(){}\[\]^"~*?:\\/])', r'\\\1', name)
    data = cached_get('https://musicbrainz.org/ws/2/artist/',
                      {'fmt': 'json', 'query': 'artist:"' + escaped + '"', 'limit': 25})
    wanted = normalized(name)
    matches = {}
    for item in data.get('artists', []):
        names = [item.get('name', '')] + [a.get('name', '') for a in item.get('aliases', [])]
        if any(normalized(n) == wanted for n in names):
            matches[item['id']] = item
    return list(matches.values())


def english_wikipedia(artist):
    qid = None
    for relation in artist.get('relations', []):
        resource = (relation.get('url') or {}).get('resource', '')
        parsed = urlparse(resource)
        if parsed.hostname == 'en.wikipedia.org' and parsed.path.startswith('/wiki/'):
            return unquote(parsed.path[len('/wiki/'):]).replace('_', ' ')
        if parsed.hostname in ('www.wikidata.org', 'wikidata.org'):
            match = re.fullmatch(r'/(?:wiki|entity)/(Q\d+)/?', parsed.path)
            if match:
                qid = match.group(1)
    if qid:
        data = cached_get('https://www.wikidata.org/w/api.php',
                          {'action': 'wbgetentities', 'format': 'json', 'ids': qid,
                           'props': 'sitelinks', 'sitefilter': 'enwiki'})
        return (((data.get('entities') or {}).get(qid) or {}).get('sitelinks') or {}).get('enwiki', {}).get('title')
    return None


def wikipedia_intro(title):
    data = cached_get('https://en.wikipedia.org/w/api.php',
                      {'action': 'query', 'format': 'json', 'formatversion': 2,
                       'prop': 'extracts|pageprops', 'titles': title, 'redirects': 1,
                       'exintro': 1, 'explaintext': 1, 'exsentences': 2})
    if data.get('error'):
        raise RuntimeError('Wikipedia API error: ' + data['error'].get('code', 'unknown'))
    pages = (data.get('query') or {}).get('pages') or []
    page = next((p for p in pages if not p.get('missing') and p.get('pageid', -1) > 0), None)
    if not page or 'disambiguation' in (page.get('pageprops') or {}):
        return None
    extract = re.sub(r'\s+', ' ', page.get('extract', '')).strip()
    if not extract:
        return None
    canonical = page.get('title', title)
    return {'title': canonical, 'extract': extract,
            'url': 'https://en.wikipedia.org/wiki/' + quote(canonical.replace(' ', '_'), safe=''),
            'history_url': 'https://en.wikipedia.org/w/index.php?title=' + quote(canonical, safe='') + '&action=history'}


def year_of(value):
    match = re.match(r'^(\d{4})(?:-|$)', value or '')
    return match.group(1) if match else ''


def structured_comment(artist, recording):
    name = artist.get('name', 'Artist')
    kind = artist.get('type', '')
    area = (artist.get('area') or {}).get('name', '')
    label = {'Person': 'musician', 'Group': 'group', 'Choir': 'choir', 'Orchestra': 'orchestra'}.get(kind)
    parts = [name + (', ' + label if label else '') + (' associated with ' + area if area else '')]
    life = artist.get('life-span') or {}
    begin, end = year_of(life.get('begin')), year_of(life.get('end'))
    if kind == 'Person':
        if begin:
            parts.append('born ' + begin)
        if end and life.get('ended') is True:
            parts.append('died ' + end)
    elif kind in ('Group', 'Choir', 'Orchestra'):
        if begin:
            parts.append('formed ' + begin)
        if end and life.get('ended') is True:
            parts.append('disbanded ' + end)
    release = year_of(recording.get('first-release-date'))
    if release and recording.get('title'):
        parts.append("'" + recording['title'] + "' first released " + release)
    return '; '.join(parts) + '.' if label or area or len(parts) > 1 else ''


def lookup_comment(meta, recording_result):
    """A factual comment: the Wikipedia introduction of the artist (when short enough), else a
    sentence built from MusicBrainz facts. Nothing is invented."""
    result = {'status': 'not_found', 'existing_text': meta.get('comment') or '',
              'proposed_text': '', 'sources': [], 'facts': {}, 'warnings': []}
    name = (meta.get('artist') or '').strip()
    if not name:
        result.update(status='skipped', warnings=['Artist tag is missing.'])
        return result
    artist_id = ARTIST_OVERRIDES.get(normalized(name))
    recording = {}
    candidates = recording_result.get('candidates') or []
    matched_via = 'confirmed_artist_override' if artist_id else 'artist_name'
    if artist_id:
        result['warnings'].append('Artist identity resolved using a confirmed local override.')
    if candidates and not artist_id:
        try:
            recording = mb_entity('recording', candidates[0]['id'])
            credited = {c['artist']['id'] for c in recording.get('artist-credit', [])
                        if isinstance(c, dict) and isinstance(c.get('artist'), dict) and c['artist'].get('id')}
            if len(credited) == 1:
                artist_id = next(iter(credited))
                matched_via = 'recording_credit'
            elif len(credited) > 1:
                result.update(status='needs_review')
                result['warnings'].append('Multiple credited artists; choose an artist manually before composing a biography.')
                return result
        except Exception as exc:
            result['warnings'].append('Recording artist lookup failed; trying artist name: ' + str(exc))
    if not artist_id:
        matches = find_artist_by_name(name)
        if len(matches) != 1:
            result['status'] = 'needs_review' if matches else 'not_found'
            result['facts']['artist_candidates'] = [
                {'name': a.get('name'), 'id': a.get('id'), 'disambiguation': a.get('disambiguation', '')}
                for a in matches]
            result['warnings'].append('No unique exact-name/alias artist match. Wikipedia lookup was not guessed.')
            return result
        artist_id = matches[0]['id']
        recording = {}                    # do not mix an independent artist match with an uncertain recording
    artist = mb_entity('artist', artist_id)
    if not artist.get('id'):
        raise RuntimeError('MusicBrainz artist lookup returned no artist.')
    result['sources'].append({'label': 'Artist identity — MusicBrainz', 'url': 'https://musicbrainz.org/artist/' + artist_id})
    result['facts'].update(artist=artist.get('name'), artist_type=artist.get('type'),
                           associated_area=(artist.get('area') or {}).get('name', ''),
                           life_span=artist.get('life-span') or {}, matched_via=matched_via)
    result['proposed_text'] = structured_comment(artist, recording)
    if recording.get('id'):
        result['sources'].append({'label': 'Recording — MusicBrainz', 'url': 'https://musicbrainz.org/recording/' + recording['id']})
    try:
        title = english_wikipedia(artist)
        intro = wikipedia_intro(title) if title else None
        if intro:
            result['facts']['wikipedia'] = intro
            result['sources'].extend([
                {'label': 'Artist introduction — Wikipedia (excerpt)', 'url': intro['url']},
                {'label': 'Wikipedia contributors / article history', 'url': intro['history_url']},
                {'label': 'Wikipedia text licence — CC BY-SA 4.0', 'url': 'https://creativecommons.org/licenses/by-sa/4.0/'},
            ])
            if len(intro['extract']) <= 650:
                result['proposed_text'] = intro['extract']
            else:
                result['warnings'].append('Wikipedia introduction exceeds 650 characters; kept the MusicBrainz sentence.')
            result['warnings'].append('Wikipedia text is an excerpt, not an independently verified summary. '
                                      'Keep the attribution/licence information if you reuse it.')
        else:
            result['warnings'].append('No usable linked English Wikipedia introduction; kept the MusicBrainz sentence.')
    except Exception as exc:
        result['warnings'].append('Wikipedia lookup failed; kept the MusicBrainz sentence: ' + str(exc))
    result['status'] = 'candidate_found' if result['proposed_text'] else 'not_found'
    if matched_via == 'artist_name' or (matched_via == 'recording_credit'
                                         and recording_result.get('status') != 'strong_candidate'):
        result['status'] = 'needs_review'
        result['warnings'].append('Artist identity or recording is not confirmed; inspect the linked source before accepting.')
    result['warnings'].append('No claims of fame, current membership or activity are inferred.')
    return result


# =============================================================================
# 4. DISCOGS  (needs DISCOGS_TOKEN, or DISCOGS_CONSUMER_KEY + DISCOGS_CONSUMER_SECRET)
# =============================================================================

DISCOGS = 'https://api.discogs.com'
_discogs_lock = threading.Lock()
_discogs_cache = Cache()
_discogs_next = 0.0


def discogs_credentials():
    token = os.environ.get('DISCOGS_TOKEN', '').strip()
    key = os.environ.get('DISCOGS_CONSUMER_KEY', '').strip()
    secret = os.environ.get('DISCOGS_CONSUMER_SECRET', '').strip()
    if token:
        return 'Discogs token=' + token
    if key and secret:
        return 'Discogs key=' + key + ', secret=' + secret
    return ''


def discogs_json(endpoint, params=None):
    global _discogs_next
    auth = discogs_credentials()
    if not auth:
        raise RuntimeError('Configure a Discogs personal token OR consumer key + secret.')
    ua = user_agent()
    if not ua:
        raise RuntimeError('Configure LOOKUP_USER_AGENT with application/contact details.')
    cache_key = (endpoint, tuple(sorted((params or {}).items())))
    saved = _discogs_cache.get(cache_key)
    if saved is not None:
        return saved
    with _discogs_lock:
        for attempt in range(3):
            time.sleep(max(0, _discogs_next - time.monotonic()))
            _discogs_next = time.monotonic() + 1.1
            try:
                response = requests.get(DISCOGS + endpoint, params=params, timeout=(8, 25), allow_redirects=False,
                                        headers={'Authorization': auth, 'User-Agent': ua, 'Accept': 'application/json'})
            except requests.RequestException:
                if attempt < 2:
                    _discogs_next = time.monotonic() + 2 ** (attempt + 1)
                    continue
                raise RuntimeError('Discogs network error/timeout.') from None
            if response.headers.get('X-Discogs-Ratelimit-Remaining') == '0':
                _discogs_next = max(_discogs_next, time.monotonic() + 60)
            if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                delay = retry_delay(response.headers.get('Retry-After'), 60 if response.status_code == 429 else 2 ** (attempt + 1))
                if delay is None or delay > 120:
                    raise RuntimeError('Discogs requested a wait; retry later.')
                _discogs_next = max(_discogs_next, time.monotonic() + delay)
                continue
            if response.status_code == 404:
                return {}
            if not response.ok or response.is_redirect:
                raise RuntimeError('Discogs HTTP ' + str(response.status_code) + '; check credentials/quota.')
            try:
                data = response.json()
            except ValueError:
                raise RuntimeError('Discogs returned invalid JSON.') from None
            _discogs_cache.put(cache_key, data)
            return data
    raise RuntimeError('Discogs request failed.')


def discogs_norm(value):
    value = re.sub(r'\s*\(\d+\)\s*$', '', value or '')        # "Artist (2)" -> "Artist"
    return re.sub(r'[^\w]+', ' ', value.casefold()).strip()


def override_key(artist, title):
    return re.sub(r'\W+', '', artist.casefold()) + '|' + re.sub(r'\W+', '', title_without_trailing_year(title).casefold())


def base_title(value):
    """The title without '(Radio Edit)', '- Extended Mix', '(... Remix)' and similar."""
    text = title_without_trailing_year(value)
    marker = (r'(?:radio\s+(?:edit|mix|version)|extended(?:\s+(?:mix|version|edit))?|original\s+mix|'
              r'instrumental|club\s+mix|live(?:\s+[^\]\)]*)?|[^\]\)]*remix)')
    text = re.sub(r'\s*[\(\[]\s*' + marker + r'\s*[\)\]]\s*$', '', text, flags=re.I)
    text = re.sub(r'\s+-\s+' + marker + r'\s*$', '', text, flags=re.I)
    return text.strip()


def name_match(artist, credits):
    return any(discogs_norm(artist) in (discogs_norm(c.get('name', '')), discogs_norm(c.get('anv', '')))
               for c in credits or [] if isinstance(c, dict))


def discogs_tracks(items):
    for item in items or []:
        if item.get('type_', 'track') == 'track':
            yield item
        yield from discogs_tracks(item.get('sub_tracks'))


def duration_seconds(value):
    parts = (value or '').split(':')
    if not 2 <= len(parts) <= 3 or any(not p.isdigit() for p in parts):
        return None
    result = 0
    for part in parts:
        result = result * 60 + int(part)
    return result or None


def discogs_videos(items):
    results = {}
    for item in items or []:
        parsed = urlparse(item.get('uri', ''))
        host = (parsed.hostname or '').lower()
        identifier = None
        if host in ('youtube.com', 'www.youtube.com') and parsed.path == '/watch':
            identifier = parse_qs(parsed.query).get('v', [None])[0]
        elif host == 'youtu.be':
            identifier = parsed.path.strip('/')
        if identifier and re.fullmatch(r'[A-Za-z0-9_-]{11}', identifier):
            url = 'https://www.youtube.com/watch?v=' + identifier
            results[url] = {'title': item.get('title', ''), 'url': url,
                            'note': 'Release/master link; not necessarily this track/version.'}
    return list(results.values())


def discogs_covers(data, source):
    pictures = sorted(data.get('images') or [], key=lambda x: x.get('type') != 'primary')
    result = []
    for picture in pictures[:2]:
        url = picture.get('uri') or picture.get('resource_url') or ''
        parsed = urlparse(url)
        if parsed.scheme != 'https' or parsed.hostname not in ('i.discogs.com', 'images.discogs.com'):
            continue
        result.append({'url': url, 'original_url': url, 'source': source, 'release_title': data.get('title', ''),
                       'provider': 'Discogs', 'width': picture.get('width'), 'height': picture.get('height')})
    return result


def profile_text(value):
    value = re.sub(r'\[(?:a|r|m|l)=([^\]]+)\]', r'\1', value or '')
    value = re.sub(r'\[(?:a|r|m|l)\d+\]', '', value)
    value = re.sub(r'\[/?(?:b|i|u)\]', '', value)
    return re.sub(r'\s+', ' ', value).strip()


def lookup_discogs(meta, duration):
    """Masters / releases with a matching track (title, artist, duration), their years,
    covers and videos, and the artist profile."""
    result = {'status': 'not_found', 'years': [], 'releases': [], 'artist': None, 'covers': [],
              'warnings': [], 'diagnostics': []}
    if not discogs_credentials():
        result.update(status='not_configured', warnings=['Discogs is not configured (DISCOGS_TOKEN in .env).'])
        return result
    artist = (meta.get('artist') or '').strip()
    title = title_without_trailing_year(meta.get('title') or '')
    if not artist or not title:
        result.update(status='skipped', warnings=['Artist/title missing.'])
        return result
    queue, seen, accepted = [], set(), []
    artist_id = None

    def enqueue(kind, identifier, reason):
        if type(identifier) is int and identifier > 0 and (kind, identifier) not in seen:
            seen.add((kind, identifier))
            queue.append((kind, identifier, reason))

    raw = os.environ.get('DISCOGS_MASTER_OVERRIDES', '').strip()
    if raw:
        try:
            overrides = json.loads(raw)
            if not isinstance(overrides, dict):
                raise ValueError('Expected JSON object')
            identifier = overrides.get(override_key(artist, title))
            if identifier is not None:
                enqueue('master', int(identifier), 'Explicit user-provided master')
        except (ValueError, TypeError):
            raise ValueError('Invalid DISCOGS_MASTER_OVERRIDES JSON.') from None

    def search(kind, params):
        query = {'type': kind, 'per_page': 10, 'page': 1, **params}
        data = discogs_json('/database/search', query)
        results = data.get('results') or []
        result['diagnostics'].append({'search': query, 'results_returned': len(results)})
        for item in results[:6]:
            enqueue(kind, item.get('id'), 'Search result')

    def inspect_queue(limit):
        nonlocal artist_id
        examined = 0
        while queue and examined < limit:
            kind, identifier, reason = queue.pop(0)
            examined += 1
            source = 'https://www.discogs.com/' + kind + '/' + str(identifier)
            try:
                data = discogs_json(('/masters/' if kind == 'master' else '/releases/') + str(identifier))
            except Exception as exc:
                result['diagnostics'].append({'source': source, 'rejected': str(exc)})
                continue
            if not data:
                result['diagnostics'].append({'source': source, 'rejected': 'No detail record returned'})
                continue
            if not name_match(artist, data.get('artists')):
                result['diagnostics'].append({'source': source, 'rejected': 'Artist credit differs',
                                              'artists': [a.get('name') for a in data.get('artists', [])]})
                continue
            if not re.search(r'\blive\b', title, re.I) and re.search(r'\blive\b', data.get('title', ''), re.I):
                result['diagnostics'].append({'source': source, 'rejected': 'Live release for a non-live tagged track'})
                continue
            best, track_titles = None, []
            for track in discogs_tracks(data.get('tracklist')):
                track_title = track.get('title', '')
                track_titles.append(track_title)
                if track.get('artists') and not name_match(artist, track['artists']):
                    continue
                full = discogs_norm(title) == discogs_norm(title_without_trailing_year(track_title))
                ratio = SequenceMatcher(None, discogs_norm(base_title(title)), discogs_norm(base_title(track_title))).ratio()
                if not full and ratio < .96:
                    continue
                length = duration_seconds(track.get('duration'))
                delta = abs(length - duration) if length is not None else None
                score = 100 if full else 94
                score -= min(delta / 3, 30) if delta is not None else 4
                if best is None or score > best[0]:
                    best = (score, track, delta, full)
            if best is None:
                result['diagnostics'].append({'source': source, 'rejected': 'No matching track title',
                                              'track_titles': track_titles[:12]})
                continue
            score, track, delta, full = best
            warnings = []
            if not full:
                warnings.append('Base title matches but version wording differs. Verify the edit/mix before accepting.')
            if delta is None:
                warnings.append('Track duration unavailable; audio version not confirmed.')
            elif delta > 5:
                warnings.append('Track duration differs by more than five seconds.')
            accepted.append({'title': data.get('title', ''), 'source': source, 'track': track.get('title', ''),
                             'year': str(data.get('year') or ''),
                             'duration_difference_seconds': round(delta, 2) if delta is not None else None,
                             'ranking_score': round(score, 1), 'videos': discogs_videos(data.get('videos')),
                             'warnings': warnings, 'covers': discogs_covers(data, source), 'kind': kind, 'reason': reason})
            if artist_id is None:
                artist_id = next((a.get('id') for a in data.get('artists', [])
                                  if name_match(artist, [a]) and type(a.get('id')) is int), None)
            if kind == 'master':
                enqueue('release', data.get('main_release'), 'Main edition linked from accepted master')
            else:
                enqueue('master', data.get('master_id'), 'Master linked from accepted release')

    if queue:                                   # explicit overrides before the search ranking
        inspect_queue(2)
    if not accepted:
        search('master', {'artist': artist, 'release_title': title})
        inspect_queue(6)
    if not accepted:
        search('master', {'q': artist + ' ' + title})
        inspect_queue(6)
    if not accepted:
        search('release', {'artist': artist, 'release_title': title})
        inspect_queue(6)
    if not accepted:
        search('release', {'artist': artist, 'track': title})
        inspect_queue(6)
    if accepted and queue:                      # at most two linked editions
        inspect_queue(2)
    accepted.sort(key=lambda x: (-x['ranking_score'], x['kind'] != 'master'))
    result['releases'] = accepted[:8]
    year_seen, cover_seen = set(), set()
    for entry in result['releases']:
        if re.fullmatch(r'[12]\d{3}', entry['year']) and (entry['year'], entry['source']) not in year_seen:
            year_seen.add((entry['year'], entry['source']))
            result['years'].append({'year': entry['year'],
                                    'kind': 'Master group (verify version)' if entry['kind'] == 'master' else 'Release edition',
                                    'source': entry['source'], 'release_title': entry['title'], 'track_title': entry['track'],
                                    'duration_difference_seconds': entry['duration_difference_seconds']})
        for cover in entry['covers']:
            if cover['url'] not in cover_seen:
                cover_seen.add(cover['url'])
                result['covers'].append(cover)
    if artist_id is None:
        search_data = discogs_json('/database/search', {'type': 'artist', 'q': artist, 'per_page': 20, 'page': 1})
        matches = [a for a in search_data.get('results', []) if discogs_norm(a.get('title', '')) == discogs_norm(artist)]
        ids = {a.get('id') for a in matches if type(a.get('id')) is int}
        if len(ids) == 1:
            artist_id = next(iter(ids))
        else:
            result['diagnostics'].append({'artist_search_matches': [{'id': a.get('id'), 'title': a.get('title')} for a in matches],
                                          'note': 'Artist profile not guessed; resolve release/master identity first.'})
    if artist_id is not None:
        try:
            profile = discogs_json('/artists/' + str(artist_id))
            result['artist'] = {'name': profile.get('name', ''), 'profile': profile_text(profile.get('profile', '')),
                                'source': 'https://www.discogs.com/artist/' + str(artist_id)}
        except Exception as exc:
            result['warnings'].append('Artist profile lookup: ' + str(exc))
    if result['releases'] or result['artist']:
        result['status'] = 'needs_review'
    result['warnings'].append('Master/release/track identity is heuristic. Review artist, version, duration and year before using it.')
    result['warnings'].append('Video links may refer to another track on the release.')
    return result


# =============================================================================
# 5. LRCLIB  (lyrics, no key needed)
# =============================================================================

LRCLIB = 'https://lrclib.net'
_lrclib_lock = threading.Lock()
_lrclib_last = 0.0
LRC_TIMESTAMP = re.compile(r'\[\d{1,3}:\d{2}(?:[.:]\d+)?\]')


def lrclib_json(endpoint, params):
    global _lrclib_last
    ua = user_agent()
    if not ua:
        raise RuntimeError('Set LOOKUP_USER_AGENT (application name, version and contact) in .env.')
    with _lrclib_lock:                          # one request at a time, also while waiting to retry
        for attempt in range(3):
            time.sleep(max(0.0, 0.4 - (time.monotonic() - _lrclib_last)))
            _lrclib_last = time.monotonic()
            response = requests.get(LRCLIB + endpoint, params=params, timeout=(8, 25),
                                    headers={'User-Agent': ua, 'Accept': 'application/json'})
            if response.status_code == 404:
                return None
            if response.status_code in (429, 503):
                if attempt == 2:
                    response.raise_for_status()
                wait = retry_delay(response.headers.get('Retry-After'), 2 ** (attempt + 1))
                wait = 2 ** (attempt + 1) if wait is None else wait
                if wait > 120:
                    raise RuntimeError(f'LRCLIB requested a {wait:.0f}-second wait. Retry this song later.')
                time.sleep(wait)
                continue
            if response.status_code in (500, 502, 504) and attempt < 2:
                time.sleep(2 ** (attempt + 1))
                continue
            response.raise_for_status()
            try:
                return response.json()
            except ValueError as exc:
                raise RuntimeError('LRCLIB returned an invalid JSON response.') from exc
    raise RuntimeError('LRCLIB lookup failed after retries.')


def lyrics_candidate(record, artist, title, duration):
    if not isinstance(record, dict):
        return None
    try:
        matched_duration = float(record['duration'])
    except (KeyError, TypeError, ValueError):
        return None
    duration_difference = abs(matched_duration - duration)
    artist_similarity = similarity(artist, record.get('artistName', ''))
    title_similarity = similarity(title, record.get('trackName', ''))
    if duration_difference > 2.0 or artist_similarity < 0.95 or title_similarity < 0.95:
        return None                             # conservative gates, not an identity guarantee
    synced = (record.get('syncedLyrics') or '').strip()
    plain = (record.get('plainLyrics') or '').strip()
    instrumental = bool(record.get('instrumental'))
    if instrumental:
        kind, text = 'Instrumental', ''
    elif synced and LRC_TIMESTAMP.search(synced):
        kind, text = 'LRC', synced
    elif plain:
        kind, text = 'Plain', plain
    else:
        return None
    record_id = record.get('id')
    return {'id': record_id, 'artist': record.get('artistName', ''), 'title': record.get('trackName', ''),
            'album': record.get('albumName', ''), 'duration_seconds': matched_duration,
            'duration_difference_seconds': round(duration_difference, 3),
            'artist_similarity': round(artist_similarity, 3), 'title_similarity': round(title_similarity, 3),
            'type': kind, 'text': text, 'instrumental': instrumental,
            'source': f'{LRCLIB}/api/get/{record_id}' if record_id is not None else ''}


def lookup_lyrics(meta, duration):
    existing_text = meta.get('lyrics_text') or ''
    existing_type = 'LRC' if LRC_TIMESTAMP.search(existing_text) else ('Plain' if existing_text.strip() else 'None')
    result = {'status': 'not_found', 'existing_type': existing_type, 'existing_text': existing_text,
              'proposed': None, 'candidates': [], 'warnings': []}
    artist = (meta.get('artist') or '').strip()
    title = (meta.get('title') or '').strip()
    album = (meta.get('album') or '').strip()
    if not artist or not title:
        result['status'] = 'skipped'
        result['warnings'].append('Artist or title is missing.')
        return result
    if not 1 <= duration <= 3600:
        result['status'] = 'skipped'
        result['warnings'].append('Duration is outside the supported 1–3600 second range.')
        return result
    params = {'artist_name': artist, 'track_name': title, 'duration': round(duration, 3)}
    if album:
        params['album_name'] = album
    direct = lrclib_json('/api/get', params)
    candidate = lyrics_candidate(direct, artist, title, duration) if direct else None
    candidates = [candidate] if candidate else []
    # also search when nothing matched, or when a search may find LRC instead of plain lyrics
    if candidate is None or candidate['type'] == 'Plain':
        records = lrclib_json('/api/search', {'artist_name': artist, 'track_name': title}) or []
        if not isinstance(records, list):
            raise RuntimeError('Unexpected LRCLIB search response.')
        for record in records:
            item = lyrics_candidate(record, artist, title, duration)
            if item:
                candidates.append(item)
    unique = {(c['id'], c['artist'], c['title'], c['duration_seconds']): c for c in candidates}
    candidates = list(unique.values())
    priority = {'LRC': 0, 'Plain': 1, 'Instrumental': 2}
    candidates.sort(key=lambda c: (priority[c['type']], -(c['artist_similarity'] + c['title_similarity']),
                                   c['duration_difference_seconds']))
    result['candidates'] = candidates[:5]
    if not candidates:
        result['warnings'].append('No sufficiently close artist/title/duration match found.')
        return result
    proposed = candidates[0]
    result['proposed'] = proposed
    result['status'] = 'instrumental' if proposed['instrumental'] else 'candidate_found'
    if len(candidates) > 1:
        result['warnings'].append('Multiple suitable records found; inspect the proposed version.')
    if any(c['instrumental'] for c in candidates) and any(not c['instrumental'] for c in candidates):
        result['warnings'].append('Candidates disagree about whether the track is instrumental.')
    if existing_type == 'LRC' and proposed['type'] != 'LRC':
        result['warnings'].append('The song already has LRC lyrics: do not replace them with plain lyrics.')
    result['warnings'].append('Metadata and duration matching cannot guarantee that the lyrics or timestamps match this exact audio.')
    return result


# =============================================================================
# 5b. MEGALOBIZ  (synchronised lyrics, public API, no key needed)
# =============================================================================
# https://megalobiz.com/developers : GET /search?title=&artist=&duration=&limit=  ->
# {results: [{id, title, artist, duration, duration_diff, sync, score, url, lrc_url}]}
# and GET /lrc/<id> -> {id, title, artist, duration, lrc, lines, url, ...}.
# Without a key: 30 requests a minute and 1000 a day per address. A free key (made after
# signing in on megalobiz.com, setting MEGALOBIZ_API_KEY) raises that to 300 / 20000.
# Their fair-use rules: look up only the songs you work on, send a descriptive User-Agent
# and link to the lyric's Megalobiz page (the "View source" link in the tag editor).

MEGALOBIZ = 'https://api.megalobiz.com/api/public/v1'
MEGALOBIZ_RESULTS = 3                       # sheets fetched per lookup (one request each)
_megalobiz_lock = threading.Lock()
_megalobiz_last = 0.0
_megalobiz_cache = Cache()


def megalobiz_json(endpoint, params=None):
    global _megalobiz_last
    ua = user_agent()
    if not ua:
        raise RuntimeError('Set LOOKUP_USER_AGENT (application name, version and contact) in .env.')
    headers = {'User-Agent': ua, 'Accept': 'application/json'}
    key = os.environ.get('MEGALOBIZ_API_KEY', '').strip()
    if key:
        headers['X-API-Key'] = key
    with _megalobiz_lock:                       # one request at a time, 0.5 s apart
        for attempt in range(3):
            time.sleep(max(0.0, 0.5 - (time.monotonic() - _megalobiz_last)))
            _megalobiz_last = time.monotonic()
            try:
                response = requests.get(MEGALOBIZ + endpoint, params=params, timeout=(8, 25), headers=headers)
            except requests.RequestException:
                if attempt < 2:
                    time.sleep(2 ** (attempt + 1))
                    continue
                raise RuntimeError('Megalobiz network error or timeout.') from None
            if response.status_code == 404:
                return None
            if response.status_code == 401:
                raise RuntimeError('Megalobiz rejected MEGALOBIZ_API_KEY (unknown or revoked key).')
            if response.status_code == 429:
                if attempt == 2:
                    raise RuntimeError('Megalobiz rate limit reached (30 requests a minute / 1000 a day without a key). Try again later.')
                wait = retry_delay(response.headers.get('Retry-After'), 2 ** (attempt + 1))
                wait = 2 ** (attempt + 1) if wait is None else wait
                if wait > 120:
                    raise RuntimeError(f'Megalobiz requested a {wait:.0f}-second wait. Retry this song later.')
                time.sleep(wait)
                continue
            if response.status_code in (500, 502, 503, 504) and attempt < 2:
                time.sleep(2 ** (attempt + 1))
                continue
            if not response.ok:
                raise RuntimeError(f'Megalobiz API error {response.status_code}.')
            try:
                return response.json()
            except ValueError as exc:
                raise RuntimeError('Megalobiz returned an invalid JSON response.') from exc
    raise RuntimeError('Megalobiz lookup failed after retries.')


def megalobiz_candidate(record, artist, title, duration):
    """A search result that is close enough to be shown (same style of gates as LRCLIB;
    the duration gate is a little wider because Megalobiz sheets are timed by hand)."""
    if not isinstance(record, dict) or record.get('id') is None:
        return None
    try:
        matched_duration = float(record.get('duration'))
    except (TypeError, ValueError):
        return None
    duration_difference = abs(matched_duration - duration)
    found_artist = (record.get('artist') or '').strip()
    artist_similarity = similarity(artist, found_artist) if found_artist else 0.0
    title_similarity = similarity(title, record.get('title', ''))
    notes = []
    if not found_artist:
        # Some sheets have no artist: accept only an exact title and a very close duration.
        if title_similarity < 0.98 or duration_difference > 1.5:
            return None
        notes.append('This Megalobiz sheet has no artist name; matched on title and duration only.')
    elif artist_similarity < 0.9 or title_similarity < 0.9 or duration_difference > 3.0:
        return None
    return {'id': record['id'], 'artist': found_artist, 'title': record.get('title', ''),
            'duration_seconds': matched_duration, 'duration_difference_seconds': round(duration_difference, 3),
            'artist_similarity': round(artist_similarity, 3), 'title_similarity': round(title_similarity, 3),
            'sync': record.get('sync', ''), 'score': record.get('score'),
            'url': record.get('url') or f'https://megalobiz.com/lrc/{record["id"]}', 'notes': notes}


def lookup_megalobiz(meta, duration):
    """Synchronised lyrics from Megalobiz: search, then fetch the best sheets (at most 3)."""
    existing_text = meta.get('lyrics_text') or ''
    existing_type = 'LRC' if LRC_TIMESTAMP.search(existing_text) else ('Plain' if existing_text.strip() else 'None')
    result = {'status': 'not_found', 'existing_type': existing_type, 'proposed': None, 'candidates': [], 'warnings': []}
    artist = (meta.get('artist') or '').strip()
    title = title_without_trailing_year((meta.get('title') or '').strip())
    if not artist or not title:
        result['status'] = 'skipped'
        result['warnings'].append('Artist or title is missing.')
        return result
    if not 1 <= duration <= 3600:
        result['status'] = 'skipped'
        result['warnings'].append('Duration is outside the supported 1–3600 second range.')
        return result
    cache_key = (artist.casefold(), title.casefold(), round(duration, 1))
    found = _megalobiz_cache.get(cache_key)     # {'candidates': [...], 'warnings': [...]} of an earlier search
    if found is None:
        data = megalobiz_json('/search', {'title': title, 'artist': artist, 'duration': round(duration, 2), 'limit': 10}) or {}
        records = data.get('results') if isinstance(data, dict) else None
        if not isinstance(records, list):
            raise RuntimeError('Unexpected Megalobiz search response.')
        close = [c for c in (megalobiz_candidate(r, artist, title, duration) for r in records) if c]
        close.sort(key=lambda c: (-(c['artist_similarity'] + c['title_similarity']), c['duration_difference_seconds']))
        found = {'candidates': [], 'warnings': []}
        for candidate in close[:MEGALOBIZ_RESULTS]:
            sheet = megalobiz_json(f'/lrc/{candidate["id"]}')
            text = (sheet or {}).get('lrc') if isinstance(sheet, dict) else None
            text = (text or '').strip()
            if not text or not LRC_TIMESTAMP.search(text):
                continue                        # a sheet without timestamps is of no use here
            candidate.update(type='LRC', text=text, synced_by=(sheet.get('synced_by') or ''),
                             source=sheet.get('url') or candidate['url'])
            found['warnings'].extend(candidate.pop('notes', []))
            found['candidates'].append(candidate)
        if not found['candidates']:
            found['warnings'].append('No sufficiently close artist/title/duration match found on Megalobiz.'
                                     if not close else 'The matching Megalobiz sheets contain no timestamps.')
        elif len(found['candidates']) > 1:
            found['warnings'].append('Several Megalobiz sheets match; check the timing of the one you use.')
        _megalobiz_cache.put(cache_key, found)
    else:
        result['cache_hit'] = True
    result['candidates'] = found['candidates']
    result['warnings'].extend(found['warnings'])
    if result['candidates']:
        result['status'] = 'candidate_found'
        result['proposed'] = result['candidates'][0]
        if existing_type == 'LRC':
            result['warnings'].append('The song already has LRC lyrics; they are only replaced when you choose a result and save.')
        result['warnings'].append('Megalobiz sheets are timed by their users: play the song and check that the lines keep up.')
    return result


# =============================================================================
# 6. YOUTUBE  (needs YOUTUBE_API_KEY)
# =============================================================================

YOUTUBE = 'https://www.googleapis.com/youtube/v3/'
_youtube_cache = Cache()

# ---------- the daily search counter ----------
# The YouTube Data API gives 10 000 quota units a day; one search costs 100 units and the
# video details 1 unit, so about 100 searches a day are possible (YOUTUBE_DAILY_LIMIT).
# Google resets the quota at midnight Pacific time. The counter is saved in a small JSON
# file in STATE_ROOT (default: a hidden folder inside the backup folder, which is always
# mounted) so it survives a restart of the container. A cached lookup costs nothing.

YOUTUBE_SEARCH_UNITS = 100
YOUTUBE_VIDEOS_UNITS = 1
_quota_lock = threading.Lock()
_quota_memory = None                            # last known counter (also when the file cannot be written)


def youtube_daily_limit():
    try:
        return max(1, int(os.environ.get('YOUTUBE_DAILY_LIMIT', '100').strip() or 100))
    except ValueError:
        return 100


def state_root():
    value = os.environ.get('STATE_ROOT', '').strip()
    return Path(value) if value else Path(os.environ.get('BACKUP_ROOT', '/backups')) / '.jstal-tagger'


def quota_file():
    return state_root() / 'youtube-quota.json'


def pacific_tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo('America/Los_Angeles')
    except Exception:                           # no time zone database: Pacific standard time
        return timezone(timedelta(hours=-8), 'PST')


def quota_day():
    """Today's date where Google counts (Pacific time)."""
    return datetime.now(pacific_tz()).strftime('%Y-%m-%d')


def quota_reset_time():
    """Next midnight Pacific time, as an aware datetime."""
    now = datetime.now(pacific_tz())
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def _empty_quota():
    return {'date': quota_day(), 'searches': 0, 'units': 0, 'exhausted': False}


def _load_quota():
    """The counter for today (reset when the saved one is from another day)."""
    global _quota_memory
    data = None
    try:
        with open(quota_file(), 'r', encoding='utf-8') as stream:
            data = json.load(stream)
    except (OSError, ValueError):
        data = _quota_memory
    if not isinstance(data, dict) or data.get('date') != quota_day():
        data = _empty_quota()
    data = {'date': data['date'], 'searches': int(data.get('searches', 0) or 0),
            'units': int(data.get('units', 0) or 0), 'exhausted': bool(data.get('exhausted'))}
    _quota_memory = data
    return data


def _save_quota(data):
    """Write the counter (atomically). A folder that cannot be written only costs persistence."""
    global _quota_memory
    _quota_memory = data
    try:
        path = quota_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        with open(temporary, 'w', encoding='utf-8') as stream:
            json.dump(data, stream)
        os.replace(temporary, path)
        return True
    except OSError:
        return False


def _quota_view(data):
    limit = youtube_daily_limit()
    reset = quota_reset_time()
    local_reset = reset.astimezone()            # the container's TZ (Europe/Brussels in docker-compose.yml)
    return {'configured': bool(os.environ.get('YOUTUBE_API_KEY', '').strip()),
            'searches': data['searches'], 'limit': limit, 'units': data['units'],
            'reached': data['exhausted'] or data['searches'] >= limit, 'exhausted_by_google': data['exhausted'],
            'resets_at': reset.isoformat(), 'resets_local': local_reset.strftime('%H:%M'),
            'persisted': quota_file().is_file(), 'state_file': str(quota_file())}


def youtube_quota():
    """What the page shows: searches today, the limit, whether it is reached, the reset time."""
    with _quota_lock:
        data = _load_quota()
    return _quota_view(data)


def youtube_limit_message(view):
    reason = 'Google reports that the daily quota is used up' if view['exhausted_by_google'] \
        else f'The daily limit of {view["limit"]} YouTube searches is reached'
    return f'{reason}. It resets at midnight Pacific time ({view["resets_local"]} here). Try again tomorrow.'


def youtube_count(endpoint):
    """Count a request before it is sent. Raises when today's searches are used up."""
    with _quota_lock:
        data = _load_quota()
        if endpoint == 'search':
            if data['exhausted'] or data['searches'] >= youtube_daily_limit():
                raise RuntimeError(youtube_limit_message(_quota_view(data)))
            data['searches'] += 1
            data['units'] += YOUTUBE_SEARCH_UNITS
        else:
            data['units'] += YOUTUBE_VIDEOS_UNITS
        _save_quota(data)


def youtube_mark_exhausted():
    """Google answered quotaExceeded: show the limit as reached until the next reset."""
    with _quota_lock:
        data = _load_quota()
        data['exhausted'] = True
        _save_quota(data)


def words(value):
    return set(re.findall(r'\w+', (value or '').casefold()))


def coverage(expected, actual):
    tokens = words(expected)
    return len(tokens & words(actual)) / len(tokens) if tokens else 0.0


def title_coverage(expected, actual):
    wanted = {w for w in words(expected) if not re.fullmatch(r'(?:18|19|20)\d{2}', w)}
    found = {w for w in words(actual) if not re.fullmatch(r'(?:18|19|20)\d{2}', w)}
    return len(wanted & found) / len(wanted) if wanted else coverage(expected, actual)


def preferred_channel_names():
    return {name.strip().casefold() for name in os.environ.get('YOUTUBE_PREFERRED_CHANNELS', '').split('|') if name.strip()}


def performance_upload(video_title, source_title):
    """Live / concert / TV uploads are skipped unless the song itself is a live version."""
    if re.search(r'\blive\b', source_title, re.IGNORECASE):
        return False
    patterns = (r'\blive\b', r'\bin concert\b', r'\bconcert footage\b', r'\bconcert performance\b',
                r'\bfestival performance\b', r'\bacoustic session\b', r'\blive session\b',
                r'\btop of the pops\b', r'\btotp\b', r'@\s*tmf\b', r'\btmf\s*(?:awards?|0[0-9]|19\d{2}|20\d{2})\b')
    return any(re.search(pattern, video_title, re.IGNORECASE) for pattern in patterns)


def iso_seconds(value):
    match = re.fullmatch(r'P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?', value or '')
    if not match:
        return None
    d, h, m, s = [float(x or 0) for x in match.groups()]
    return d * 86400 + h * 3600 + m * 60 + s


def youtube_json(endpoint, params, key):
    youtube_count(endpoint)                     # V2.1: counted (and refused when today's searches are used up)
    for attempt in range(3):
        try:
            response = requests.get(YOUTUBE + endpoint, params=params, timeout=(8, 25),
                                    headers={'X-Goog-Api-Key': key, 'Accept': 'application/json'})
        except requests.RequestException:
            if attempt < 2:
                time.sleep(2 ** (attempt + 1))
                continue
            raise RuntimeError('YouTube network error or timeout.') from None
        if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
            delay = retry_delay(response.headers.get('Retry-After'), 2 ** (attempt + 1))
            if delay is None or delay > 120:
                raise RuntimeError('YouTube requested a wait; retry this song later.')
            time.sleep(delay)
            continue
        try:
            data = response.json()
        except ValueError:
            raise RuntimeError('YouTube returned invalid JSON.') from None
        if not response.ok:
            error = data.get('error') or {}
            reasons = [str(item.get('reason', 'unknown')) for item in error.get('errors', [])]
            if 'quotaExceeded' in reasons or 'dailyLimitExceeded' in reasons:
                youtube_mark_exhausted()
                raise RuntimeError(youtube_limit_message(youtube_quota()))
            raise RuntimeError('YouTube API error ' + str(response.status_code) + ': '
                               + (', '.join(reasons) or 'request rejected; check API configuration'))
        return data
    raise RuntimeError('YouTube request failed.')


def lookup_youtube(meta, duration):
    """Embeddable public music videos, ranked on title, artist, duration and 'official'
    wording. V2: also searches when the song already has a TVSHOW value (you asked for it in
    the tag editor; the old value only changes when you pick a result and save)."""
    existing = (meta.get('tvshow') or '').strip()
    result = {'status': 'not_found', 'existing_tvshow': existing, 'existing_video': meta.get('video') or '',
              'proposed': None, 'candidates': [], 'warnings': []}
    key = os.environ.get('YOUTUBE_API_KEY', '').strip()
    if not key:
        result['status'] = 'not_configured'
        result['warnings'].append('YouTube is not configured (YOUTUBE_API_KEY in .env).')
        return result
    artist = (meta.get('artist') or '').strip()
    source_title = (meta.get('title') or '').strip()
    title = title_without_trailing_year(source_title)
    if not artist or not title:
        result['status'] = 'skipped'
        result['warnings'].append('Artist or title missing.')
        return result
    region = os.environ.get('YOUTUBE_REGION', 'BE').strip().upper()
    if not re.fullmatch(r'[A-Z]{2}', region):
        raise ValueError('YOUTUBE_REGION must be a two-letter country code.')
    preferences = preferred_channel_names()
    cache_key = (artist, title, round(duration, 1), region, tuple(sorted(preferences)))
    cached = _youtube_cache.get(cache_key)
    if cached is not None:
        cached.update(existing_tvshow=existing, existing_video=result['existing_video'], cache_hit=True)
        return cached
    search = youtube_json('search', {'part': 'snippet', 'type': 'video', 'q': artist + ' ' + title,
                                     'maxResults': 10, 'order': 'relevance', 'regionCode': region,
                                     'videoEmbeddable': 'true', 'videoSyndicated': 'true', 'videoDefinition': 'any'}, key)
    ids = [x.get('id', {}).get('videoId') for x in search.get('items', [])]
    ids = list(dict.fromkeys(x for x in ids if x and re.fullmatch(r'[A-Za-z0-9_-]{11}', x)))
    videos = youtube_json('videos', {'part': 'snippet,contentDetails,status', 'id': ','.join(ids)}, key).get('items', []) if ids else []
    for video in videos:
        info = video.get('snippet') or {}
        details = video.get('contentDetails') or {}
        status = video.get('status') or {}
        restriction = details.get('regionRestriction') or {}
        if status.get('embeddable') is not True or status.get('privacyStatus') != 'public':
            continue
        if info.get('liveBroadcastContent', 'none') != 'none':
            continue
        if region in restriction.get('blocked', []):
            continue
        if 'allowed' in restriction and region not in restriction['allowed']:
            continue
        if (details.get('contentRating') or {}).get('ytRating') == 'ytAgeRestricted':
            continue
        video_title = unescape(info.get('title', ''))
        channel = unescape(info.get('channelTitle', ''))
        if performance_upload(video_title, source_title):
            continue
        length = iso_seconds(details.get('duration'))
        if length is None or length <= 0:
            continue
        difference = abs(length - duration)
        title_match = title_coverage(title, video_title)
        artist_match = coverage(artist, video_title + ' ' + channel)
        if title_match < 0.6 or artist_match < 0.5 or difference > max(90, duration * 0.35):
            continue
        notes = []
        text = video_title.casefold()
        score = 55 * title_match + 25 * artist_match - min(difference / 3, 25)
        if details.get('definition') == 'hd':
            score += 2
        if channel.casefold() in preferences:
            score += 25
            notes.append('Uploader matches a configured preferred channel name; identity is not independently verified.')
        if re.search(r'\bofficial\s+music\s+video\b', text):
            score += 20
            notes.append('Title says Official Music Video; uploader identity is not verified.')
        elif re.search(r'\bofficial\s+video\b', text):
            score += 16
            notes.append('Title says Official Video; uploader identity is not verified.')
        elif re.search(r'\bofficial\b', text):
            score += 4
            notes.append('Title says Official, but may be audio or a lyric video.')
        for marker in ('cover', 'karaoke', 'remix', 'extended', 'sped up', 'slowed', 'reaction'):
            pattern = r'\b' + re.escape(marker) + r'\b'
            if re.search(pattern, text) and not re.search(pattern, title.casefold()):
                score -= 18
                notes.append('Possible different version: ' + marker)
        if re.search(r'\blyrics?\b|\bofficial\s+audio\b', text):
            score -= 10
            notes.append('Audio/lyric upload ranked below a matching music video.')
        if channel.endswith(' - Topic'):
            score -= 8
            notes.append('Topic-channel upload may use static artwork.')
        if difference > 10:
            notes.append('Duration differs by more than 10 seconds; inspect the edit or introduction.')
        if 'remaster' in text:
            notes.append('Remastering in a title does not establish quality or authenticity.')
        result['candidates'].append({'id': video['id'], 'title': video_title, 'channel': channel,
                                     'url': 'https://www.youtube.com/watch?v=' + video['id'],
                                     'duration_seconds': length, 'duration_difference_seconds': round(difference, 2),
                                     'definition': details.get('definition', 'unknown'),
                                     'ranking_score': round(score, 1), 'warnings': notes})
    result['candidates'].sort(key=lambda x: x['ranking_score'], reverse=True)
    result['candidates'] = result['candidates'][:5]
    if result['candidates']:
        result['status'] = 'needs_review'
        result['proposed'] = result['candidates'][0]
    result['region'] = region
    result['checked_at'] = datetime.now(timezone.utc).isoformat()
    result['warnings'].append('Ranking is heuristic. HD and channel names do not verify authenticity, audio quality '
                              'or the correct version. Test the video in the jukebox.')
    if existing:
        result['warnings'].append('The song already has a TVSHOW value; it is only replaced when you choose a result and save.')
    _youtube_cache.put(cache_key, result)
    return result


# =============================================================================
# 7. FIELD LOOKUP  (what the tag editor calls)
# =============================================================================

PROVIDERS = {
    'cover': [('musicbrainz', 'MusicBrainz'), ('discogs', 'Discogs'), ('musichoarders', 'MusicHoarders (opens a new tab)')],
    'year': [('musicbrainz', 'MusicBrainz'), ('discogs', 'Discogs')],
    'comment': [('musicbrainz', 'MusicBrainz / Wikipedia'), ('discogs', 'Discogs')],
    'tvshow': [('youtube', 'YouTube'), ('discogs', 'Discogs release videos')],
    'lyrics': [('lrclib', 'LRCLIB'), ('megalobiz', 'Megalobiz (LRC)')],
}
BROWSER_ONLY = {'musichoarders'}               # opened in the browser, not searched by the server


def providers_status():
    """Which sources can be used (shown in the tag editor)."""
    return {'discogs': bool(discogs_credentials()), 'youtube': bool(os.environ.get('YOUTUBE_API_KEY', '').strip()),
            'megalobiz_key': bool(os.environ.get('MEGALOBIZ_API_KEY', '').strip()),
            'user_agent': bool(user_agent())}


def field_candidates(field, provider, meta, duration):
    """At most 8 results {value, label, source} for one field from one source."""
    items, warnings = [], []

    def add(value, label='', source=''):
        if value:
            items.append({'value': str(value), 'label': str(label), 'source': str(source)})

    if provider == 'musicbrainz':
        row = lookup_musicbrainz(meta, duration, include_cover=(field == 'cover'))
        warnings.extend(row.get('warnings', []))
        if field == 'year':
            for c in row.get('candidates', []):
                if c.get('text_similarity', 0) >= .96:
                    year = c.get('first_release_date', '')[:4]
                    if re.fullmatch(r'[12]\d{3}', year):
                        add(year, c.get('title', ''), c.get('source', ''))
        elif field == 'cover' and row.get('cover'):
            c = row['cover']
            add(c.get('url'), c.get('release_title'), c.get('source'))
        elif field == 'comment':
            c = lookup_comment(meta, row)
            warnings.extend(c.get('warnings', []))
            add(c.get('proposed_text'), 'Artist/recording information; may include a Wikipedia excerpt',
                (c.get('sources') or [{}])[0].get('url', ''))
    elif provider == 'discogs':
        row = lookup_discogs(meta, duration)
        warnings.extend(row.get('warnings', []))
        if field == 'year':
            for c in row.get('years', []):
                add(c.get('year'), c.get('release_title', ''), c.get('source', ''))
        elif field == 'cover':
            for c in row.get('covers', []):
                add(c.get('url'), c.get('release_title', ''), c.get('source', ''))
        elif field == 'comment':
            c = row.get('artist') or {}
            add(c.get('profile'), c.get('name', ''), c.get('source', ''))
        elif field == 'tvshow':
            for release in row.get('releases', []):
                for c in release.get('videos', []):
                    add(c.get('url'), c.get('title', '') + ' — release video; verify the track', release.get('source', ''))
    elif provider == 'lrclib':
        row = lookup_lyrics(meta, duration)
        warnings.extend(row.get('warnings', []))
        for c in row.get('candidates', []):
            add(c.get('text'), c.get('type', '') + ' — ' + c.get('title', ''), c.get('source', ''))
    elif provider == 'megalobiz':
        row = lookup_megalobiz(meta, duration)
        warnings.extend(row.get('warnings', []))
        for c in row.get('candidates', []):
            who = c.get('artist') or 'no artist'
            add(c.get('text'), f'LRC — {c.get("title", "")} ({who}, {c.get("duration_seconds", 0):.0f} s)', c.get('source', ''))
    elif provider == 'youtube':
        row = lookup_youtube(meta, duration)
        warnings.extend(row.get('warnings', []))
        for c in row.get('candidates', []):
            add(c.get('url'), c.get('title', ''), c.get('url', ''))
    unique = {(c['value'], c['source']): c for c in items}
    return {'items': list(unique.values())[:8], 'warnings': warnings}
