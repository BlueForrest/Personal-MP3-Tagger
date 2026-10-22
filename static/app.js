/**
 * ==================================================================================
 * jstal-tagger - page program (app.js)                                         V2.1
 * ==================================================================================
 * Everything the page does. The server (app.py) reads and writes the MP3 files; this
 * file shows them and keeps your edits as DRAFTS in the browser until you save them.
 *
 *   1. Helpers            short functions used everywhere
 *   2. Settings           columns, fields, what this browser remembers
 *   3. State              loaded songs, selection, drafts, running job
 *   4. Header             clock, theme, write status badge, YouTube counter
 *   5. Folders            breadcrumb, sub-folders, listing the MP3 files
 *   6. Grid               columns, sorting, filtering, selecting, rows
 *   6b. Pre-listen        play a song in the browser (the ▶ buttons and the player bar)
 *   7. Drafts             edited values that are not saved yet
 *   8. Tag editor         edit one field, cover upload, online lookups
 *   9. Save and wipe      prepare -> confirm -> background job -> refresh
 *  10. Details, report, export, columns
 *  11. Start-up
 *
 * V2: replaces stage1.js and the Stage 2 / Stage 3 scripts of V1's index.html.
 * V2.1: edit mode at start, pre-listen, YouTube daily counter, Select all / Deselect all
 * in the Wipe window, prefilled wipe phrase, user manual button, Megalobiz lyrics source.
 */
'use strict';

// =============================================================================
// 1. HELPERS
// =============================================================================

const $ = id => document.getElementById(id);

/** Create an element: el('p', {className: 'x'}, 'text', child, ...) */
function el(tag, props, ...children) {
	const node = document.createElement(tag);
	if (props) {
		for (const [key, value] of Object.entries(props)) {
			if (key === 'dataset') Object.assign(node.dataset, value);
			else if (key in node) node[key] = value;
			else node.setAttribute(key, value);
		}
	}
	for (const child of children) {
		if (child === null || child === undefined || child === false) continue;
		node.append(child instanceof Node ? child : String(child));
	}
	return node;
}

async function readJson(response) {
	let data;
	try { data = await response.json(); }
	catch (e) {
		throw Error(response.status === 413 ? 'The request is too large. Save fewer covers at once.'
			: 'Invalid server response (' + response.status + ')');
	}
	if (!response.ok) {
		const error = Error(data.error || response.statusText);
		error.data = data;                       // an error answer may carry extra data (the YouTube counter)
		throw error;
	}
	return data;
}

const apiGet = url => fetch(url).then(readJson);
const apiPost = (url, data) => fetch(url, {
	method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)
}).then(readJson);

/** Several values of one tag are separated by a NUL character; show them as " / ". */
const show = value => String(value ?? '').replaceAll('\u0000', ' / ');

const shorten = (text, max = 160) => {
	text = show(text);
	return text.length > max ? text.slice(0, max - 1) + '…' : text;
};

const videoIsYes = value => !['', 'no', 'false', '0', 'off', 'none'].includes(String(value ?? '').trim().toLowerCase());

function toast(message, type = 'info', ms = 4500) {
	const node = el('div', {className: 'toast ' + type, role: type === 'error' ? 'alert' : 'status'}, message);
	$('toasts').append(node);
	setTimeout(() => node.classList.add('hide'), ms);
	setTimeout(() => node.remove(), ms + 400);
}

function download(name, data) {
	const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], {type: 'application/json'}));
	el('a', {href: url, download: name}).click();
	setTimeout(() => URL.revokeObjectURL(url), 1000);
}

const store = {
	get(key, fallback) { try { const v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); } catch (e) { return fallback; } },
	set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* private mode */ } },
};


// =============================================================================
// 2. SETTINGS
// =============================================================================

// key = song property shown, field = tag field it edits / wipes (null = not a tag)
const COLUMNS = [
	{key: 'filename', label: 'Filename', field: null},
	{key: 'listen', label: 'Listen', field: null, tip: 'Play the song in the browser (pre-listen). Nothing is changed.'},
	{key: 'artist', label: 'Artist', field: 'artist'},
	{key: 'title', label: 'Title', field: 'title'},
	{key: 'album', label: 'Album', field: 'album'},
	{key: 'year', label: 'Year', field: 'year'},
	{key: 'bpm', label: 'BPM', field: 'bpm'},
	{key: 'duration', label: 'Duration', field: 'length',
		tip: 'Measured audio duration. Editing changes only the stored TLEN value (milliseconds).'},
	{key: 'track', label: 'Track', field: 'track'},
	{key: 'genre', label: 'Genre', field: 'genre'},
	{key: 'cover', label: 'Cover', field: 'cover'},
	{key: 'lyrics', label: 'Lyrics', field: 'lyrics'},
	{key: 'tvshow', label: 'YouTube URL (TVSHOW)', field: 'tvshow'},
	{key: 'video', label: 'VIDEO', field: 'video'},
	{key: 'comment', label: 'Comment', field: 'comment'},
	{key: 'other', label: 'Other tags', field: 'other', tip: 'Number of ID3 frames without a column. Click for the list.'},
	{key: 'path', label: 'Relative path', field: null},
	{key: 'error', label: 'Read error', field: null},
];
const FIELD_LABELS = {
	artist: 'Artist', title: 'Title', album: 'Album', year: 'Year', bpm: 'BPM', length: 'Duration (stored TLEN)',
	track: 'Track', genre: 'Genre', cover: 'Cover', lyrics: 'Lyrics', tvshow: 'YouTube URL (TVSHOW)',
	video: 'VIDEO', comment: 'Comment', other: 'Other tags',
};
const EDITABLE = new Set(['artist', 'title', 'album', 'year', 'bpm', 'length', 'track', 'genre',
	'cover', 'lyrics', 'tvshow', 'video', 'comment']);
const DEFAULT_WIPE = ['year', 'bpm', 'length', 'track', 'genre', 'cover', 'lyrics', 'tvshow', 'video', 'comment', 'other'];
const LOOKUP_FIELDS = new Set(['cover', 'year', 'comment', 'tvshow', 'lyrics']);
const MAX_SOURCE_IMAGE = 25 * 1024 * 1024;   // picture chosen on this computer
const COVER_SIZE = 600;


// =============================================================================
// 3. STATE
// =============================================================================

const state = {
	config: {writes_enabled: false, edit_mode_default: true, max_batch: 100, wipe_phrase: 'WIPE SELECTED TAGS', providers: {}, sources: {}},
	path: '', parent: null, listedPath: null,
	songs: [], byPath: new Map(),
	selected: new Set(),
	drafts: new Map(),                 // path -> {field: value}
	sortKey: 'filename', ascending: true,
	hidden: new Set(store.get('jstal-hidden-columns', [])),
	wipeFields: new Set(DEFAULT_WIPE),
	busy: false,                       // listing / preparing
	writing: false,                    // a save or wipe job runs
	plan: null,                        // prepared plan waiting for confirmation
	editor: null,                      // the field open in the tag editor
	lastJob: null,
	playing: null,                     // path of the song in the player (pre-listen)
	quota: null,                       // YouTube counter {searches, limit, reached, resets_local, configured}
};


// =============================================================================
// 4. HEADER
// =============================================================================

function setTheme(theme) {
	document.documentElement.dataset.theme = theme;
	try { localStorage.setItem('jstal-theme', theme); } catch (e) { /* private mode */ }
	$('theme-toggle').textContent = theme === 'light' ? '🌙' : '☀';
	$('theme-toggle').title = theme === 'light' ? 'Dark theme' : 'Light theme';
}
$('theme-toggle').onclick = () => setTheme(document.documentElement.dataset.theme === 'light' ? 'dark' : 'light');

function tick() { $('clock').textContent = new Date().toLocaleTimeString(undefined, {hour12: false}); }
setInterval(tick, 1000);

function showWriteBadge() {
	const on = state.config.writes_enabled;
	const badge = $('write-badge');
	badge.textContent = on ? 'Writing on' : 'Read-only';
	badge.className = 'badge ' + (on ? 'on' : 'off');
	badge.title = on
		? 'Saving and wiping are possible. Every change is backed up and verified first.'
		: 'Writing is off (ENABLE_TAG_WRITES=false or a read-only music folder). You can still edit drafts and check plans.';
}

/** The YouTube counter in the header: searches today / daily limit (the Data API allows
 *  about 100 searches a day). Red and a message when the limit is reached. */
function showYoutubeQuota(quota, announce = false) {
	if (!quota) return;
	const was = state.quota;
	state.quota = quota;
	const badge = $('yt-quota');
	badge.hidden = !quota.configured;
	if (!quota.configured) return;
	const left = Math.max(0, quota.limit - quota.searches);
	badge.textContent = 'YouTube ' + quota.searches + ' / ' + quota.limit + (quota.reached ? ' — limit reached' : '');
	badge.className = 'badge quota ' + (quota.reached ? 'limit' : left <= Math.max(5, Math.round(quota.limit * 0.15)) ? 'warn' : 'on');
	badge.title = (quota.reached
		? (quota.exhausted_by_google ? 'Google reports that the daily quota is used up.' : 'All ' + quota.limit + ' YouTube searches of today are used.')
		: quota.searches + ' YouTube search' + (quota.searches === 1 ? '' : 'es') + ' today, ' + left + ' left.')
		+ ' The counter resets at midnight Pacific time (' + quota.resets_local + ' here).'
		+ (quota.persisted ? '' : ' Note: the counter file could not be written; the count restarts when the container restarts.');
	if (announce && quota.reached && !(was && was.reached)) {
		toast('YouTube: the daily limit of ' + quota.limit + ' searches is reached. It resets at ' + quota.resets_local + '.', 'error', 10000);
	}
}
$('yt-quota').onclick = async () => {
	try {
		showYoutubeQuota(await apiGet('/api/youtube/quota'));
		toast(state.quota.reached ? 'YouTube: the daily limit is reached. It resets at ' + state.quota.resets_local + '.'
			: 'YouTube counter: ' + state.quota.searches + ' of ' + state.quota.limit + ' searches used today.');
	} catch (e) { toast('Cannot read the YouTube counter: ' + e.message, 'error'); }
};


// =============================================================================
// 5. FOLDERS
// =============================================================================

function confirmLeaveDrafts() {
	if (!state.drafts.size) return true;
	if (!confirm('Discard all unsaved tag edits? Nothing has been written yet.')) return false;
	state.drafts.clear();
	return true;
}

async function browse(path) {
	if (state.busy || state.writing || !confirmLeaveDrafts()) return;
	setBusy(true);
	try {
		const data = await apiGet('/api/folders?path=' + encodeURIComponent(path));
		state.path = data.path;
		state.parent = data.parent;
		setSongs([]);
		state.listedPath = null;
		drawBreadcrumb();
		$('folders').replaceChildren(...data.folders.map(f =>
			el('button', {type: 'button', className: 'chip', title: f.path, onclick: () => browse(f.path)}, '📁 ' + f.name)));
		if (!data.folders.length) $('folders').append(el('span', {className: 'muted'}, 'No sub-folders.'));
		setStatus('Folder selected. Click List MP3 files.');
	} catch (e) {
		setStatus(e.message, 'error');
	} finally {
		setBusy(false);
	}
}

function drawBreadcrumb() {
	const nav = $('breadcrumb');
	const parts = state.path ? state.path.split('/') : [];
	nav.replaceChildren(el('button', {type: 'button', className: 'crumb', onclick: () => browse(''),
		dataset: {current: parts.length ? '0' : '1'}}, '🎵 Music'));
	parts.forEach((name, i) => {
		const target = parts.slice(0, i + 1).join('/');
		nav.append(el('span', {className: 'sep'}, '›'),
			el('button', {type: 'button', className: 'crumb', onclick: () => browse(target),
				dataset: {current: i === parts.length - 1 ? '1' : '0'}}, name));
	});
	$('up').disabled = state.parent === null;
}

async function listSongs() {
	if (state.busy || state.writing || !confirmLeaveDrafts()) return;
	setSongs([]);
	setBusy(true);
	setStatus('Reading the MP3 files…');
	try {
		const recursive = $('recursive').checked;
		const data = await apiGet('/api/scan?path=' + encodeURIComponent(state.path) + '&recursive=' + (recursive ? '1' : '0'));
		state.listedPath = state.path;
		setStatus('');
		setSongs(data.songs);
		if (!data.songs.length) setStatus('No MP3 files in this folder' + (recursive ? ' or its sub-folders.' : '. Tick Include subfolders to look deeper.'));
		if (data.warnings.length) toast('Some folders could not be read: ' + data.warnings.join('; '), 'warn', 8000);
	} catch (e) {
		setStatus(e.message, 'error');
	} finally {
		setBusy(false);
	}
}

$('up').onclick = () => { if (state.parent !== null) browse(state.parent); };
$('scan').onclick = listSongs;


// =============================================================================
// 6. GRID
// =============================================================================

function setSongs(list) {
	state.songs = list;
	state.byPath = new Map(list.map(s => [s.path, s]));
	state.selected.clear();
	render();
}

function visibleColumns() { return COLUMNS.filter(c => c.key === 'filename' || !state.hidden.has(c.key)); }

function drawHeaders() {
	const row = $('headers');
	const all = el('input', {type: 'checkbox', title: 'Select / deselect the shown songs', ariaLabel: 'Select the shown songs'});
	all.onchange = () => {
		for (const s of filteredSongs()) all.checked ? state.selected.add(s.path) : state.selected.delete(s.path);
		render();
	};
	row.replaceChildren(el('th', {className: 'col-select sticky'}, all));
	for (const column of visibleColumns()) {
		const active = state.sortKey === column.key;
		const button = el('button', {type: 'button', className: 'sort' + (active ? ' active' : ''),
			onclick: () => { state.ascending = active ? !state.ascending : true; state.sortKey = column.key; render(); }},
			column.label, el('span', {className: 'arrow'}, active ? (state.ascending ? '▲' : '▼') : ''));
		const th = el('th', {className: 'col-' + column.key + (column.key === 'filename' ? ' sticky second' : ''),
			title: column.tip || ''}, button);
		if (active) th.setAttribute('aria-sort', state.ascending ? 'ascending' : 'descending');
		row.append(th);
	}
}

function filteredSongs() {
	const words = $('filter').value.toLowerCase().split(/\s+/).filter(Boolean);
	let list = state.songs;
	if (words.length) {
		list = list.filter(s => {
			const text = COLUMNS.map(c => show(s[c.key] ?? '')).join('\n').toLowerCase();
			return words.every(w => text.includes(w));
		});
	}
	const key = state.sortKey;
	const value = s => key === 'other' ? Number(s.other || 0) : key === 'cover' ? (s.cover ? 1 : 0) : show(s[key] ?? '');
	return [...list].sort((a, b) => {
		const x = value(a), y = value(b);
		const order = typeof x === 'number' ? x - y : x.localeCompare(y, undefined, {numeric: true, sensitivity: 'base'});
		return order * (state.ascending ? 1 : -1);
	});
}

function cellContent(song, column, edits) {
	const field = column.field;
	const dirty = field && Object.hasOwn(edits, field);
	switch (column.key) {
		case 'filename':
			return el('button', {type: 'button', className: 'filename', title: 'All tags of ' + song.path}, song.filename);
		case 'listen':
			if (song.error) return '—';
			return el('button', {type: 'button', className: 'play-btn', title: 'Listen to ' + song.filename,
				ariaLabel: 'Listen to ' + song.filename}, '▶');
		case 'cover': {
			const source = dirty ? edits.cover : (song.cover ? '/api/thumbnail?path=' + encodeURIComponent(song.path)
				+ '&v=' + encodeURIComponent((song._source?.sha256 || '').slice(0, 12)) : '');
			if (!source) return dirty ? el('span', {className: 'draft-note'}, '(removed)') : '—';
			return el('img', {className: 'thumb', src: source, loading: 'lazy', alt: 'Cover', title: 'Show the cover',
				onerror: function () { this.replaceWith('?'); }});
		}
		case 'video': {
			const box = el('input', {type: 'checkbox', className: 'video-check', checked: videoIsYes(currentValue(song, 'video')),
				ariaLabel: 'VIDEO = Yes for ' + song.filename, title: $('edit-toggle').checked ? 'VIDEO = Yes' : 'Turn on Edit tags to change'});
			box.disabled = !$('edit-toggle').checked || state.busy || state.writing;
			return box;
		}
		case 'other':
			return song.other ? el('button', {type: 'button', className: 'link other-count', title: 'Show the other tags'}, String(song.other)) : '0';
		case 'lyrics':
			if (dirty) return edits.lyrics ? (/\[\d+:\d+/.test(edits.lyrics) ? 'LRC (draft)' : 'Plain (draft)') : '(cleared)';
			return song.lyrics || '—';
		case 'duration':
			return dirty ? (edits.length ? song.duration + ' · TLEN ' + edits.length : song.duration + ' · TLEN cleared') : (song.duration || '');
		case 'error':
			return song.error || '';
		default:
			if (dirty) return edits[field] === '' ? '(cleared)' : shorten(edits[field], 300);
			return shorten(song[column.key], 300);
	}
}

function render() {
	drawHeaders();
	const columns = visibleColumns();
	const editing = $('edit-toggle').checked;
	const fragment = document.createDocumentFragment();
	const list = filteredSongs();
	for (const song of list) {
		const edits = state.drafts.get(song.path) || {};
		const selected = state.selected.has(song.path);
		const tr = el('tr', {className: (selected ? 'selected' : '') + (song.error ? ' has-error' : ''), dataset: {path: song.path}});
		const box = el('input', {type: 'checkbox', className: 'row-select', checked: selected, ariaLabel: 'Select ' + song.filename});
		tr.append(el('td', {className: 'col-select sticky'}, box));
		for (const column of columns) {
			const field = column.field;
			const td = el('td', {className: 'col-' + column.key + (column.key === 'filename' ? ' sticky second' : '')});
			const content = cellContent(song, column, edits);
			td.append(content instanceof Node ? content : String(content));
			if (typeof content === 'string' && content.length > 20) td.title = show(column.key === 'lyrics' ? '' : (edits[field] ?? song[column.key] ?? ''));
			if (field && Object.hasOwn(edits, field)) td.classList.add('dirty');
			if (field && EDITABLE.has(field) && column.key !== 'video') {
				td.dataset.field = field;
				if (editing && !song.error) { td.tabIndex = 0; td.classList.add('editable'); }
			}
			tr.append(td);
		}
		fragment.append(tr);
	}
	$('rows').replaceChildren(fragment);
	$('grid').classList.toggle('editing', editing);
	$('empty').hidden = state.songs.length > 0 && list.length > 0;
	if (state.songs.length && !list.length) $('empty').textContent = 'No loaded song matches the filter.';
	else if (!state.songs.length) $('empty').textContent = state.listedPath === null
		? 'Choose a folder and click List MP3 files.' : 'No MP3 files here.';
	const shown = new Set(list.map(s => s.path));
	const headerBox = $('headers').querySelector('input');
	const shownSelected = list.filter(s => state.selected.has(s.path)).length;
	headerBox.checked = list.length > 0 && shownSelected === list.length;
	headerBox.indeterminate = shownSelected > 0 && shownSelected < list.length;
	refreshPlayButtons();
	updateControls(shown.size);
}

function setStatus(message, type = '') {
	state.message = message ? {text: message, type} : null;
	updateControls();
}

function updateControls(shownCount) {
	const songs = state.songs.length;
	const selected = state.selected.size;
	const errors = state.songs.filter(s => s.error).length;
	const saveable = [...state.selected].filter(p => state.drafts.has(p)).length;
	const blocked = state.busy || state.writing;
	const bar = $('status');
	bar.replaceChildren();
	if (songs) {
		const shown = shownCount ?? $('rows').children.length;
		bar.append(el('span', {className: 'pill'}, songs + ' MP3 file' + (songs === 1 ? '' : 's') + (shown !== songs ? ' · ' + shown + ' shown' : '')),
			el('span', {className: 'pill' + (selected ? ' accent' : '')}, selected + ' selected'),
			el('span', {className: 'pill' + (state.drafts.size ? ' warn' : '')}, state.drafts.size + ' with drafts'));
		if (errors) bar.append(el('span', {className: 'pill error'}, errors + ' read error' + (errors === 1 ? '' : 's')));
	}
	if (state.message) bar.append(el('span', {className: 'message ' + state.message.type}, state.message.text));
	$('save').textContent = saveable ? 'Save tags (' + saveable + ')' : 'Save tags';
	$('save').disabled = blocked || !saveable;
	$('discard').disabled = blocked || !state.drafts.size;
	$('wipe').disabled = blocked || !selected;
	$('export').disabled = blocked || !selected;
	$('select-all').disabled = $('select-none').disabled = blocked || !songs;
	for (const id of ['scan', 'recursive']) $(id).disabled = blocked;
	$('up').disabled = blocked || state.parent === null;
	document.querySelectorAll('#folders button, #breadcrumb button').forEach(b => { b.disabled = blocked || b.dataset.current === '1'; });
	document.querySelectorAll('#rows .video-check').forEach(b => { b.disabled = blocked || !$('edit-toggle').checked; });
}

function setBusy(value) {
	state.busy = value;
	document.body.classList.toggle('is-busy', value);
	updateControls();
}

$('filter').oninput = render;
$('select-all').onclick = () => { state.songs.forEach(s => state.selected.add(s.path)); render(); };
$('select-none').onclick = () => { state.selected.clear(); render(); };
$('edit-toggle').onchange = () => {
	render();
	if ($('edit-toggle').checked) toast('Edit mode: double-click a cell (or select it and press Enter) to edit it.');
};

// One set of event handlers for all rows (the rows are drawn again often)
$('rows').addEventListener('change', event => {
	const tr = event.target.closest('tr');
	const song = tr && state.byPath.get(tr.dataset.path);
	if (!song) return;
	if (event.target.classList.contains('row-select')) {
		event.target.checked ? state.selected.add(song.path) : state.selected.delete(song.path);
		tr.classList.toggle('selected', event.target.checked);
		render();
	} else if (event.target.classList.contains('video-check')) {
		if (!$('edit-toggle').checked || state.busy || state.writing) { render(); return; }
		setDraft(song, 'video', event.target.checked ? 'Yes' : '');
	}
});
$('rows').addEventListener('click', event => {
	const tr = event.target.closest('tr');
	const song = tr && state.byPath.get(tr.dataset.path);
	if (!song) return;
	if (event.target.closest('.filename')) showDetails(song);
	else if (event.target.closest('.play-btn')) playSong(song);
	else if (event.target.closest('.other-count')) showDetails(song, 'other');
	else if (event.target.classList.contains('thumb')) openEditor(song, 'cover', !$('edit-toggle').checked);
});
$('rows').addEventListener('dblclick', event => {
	const td = event.target.closest('td[data-field]');
	if (!td || !$('edit-toggle').checked || event.target.closest('input,button')) return;
	const song = state.byPath.get(td.parentElement.dataset.path);
	if (song && !song.error) openEditor(song, td.dataset.field, false);
});
$('rows').addEventListener('keydown', event => {
	const td = event.target.closest('td[data-field]');
	if (event.key !== 'Enter' || !td || !$('edit-toggle').checked) return;
	const song = state.byPath.get(td.parentElement.dataset.path);
	if (song && !song.error) { event.preventDefault(); openEditor(song, td.dataset.field, false); }
});


// =============================================================================
// 6b. PRE-LISTEN  (the ▶ buttons, the player bar and the Listen button of the editor)
// =============================================================================
// The server streams the file read-only (/api/audio); the browser's own audio element
// plays it, so seeking and the volume work as usual. Nothing in the file changes.

const audio = $('audio');

function isPlaying(path) { return state.playing === path && !audio.paused && !audio.ended; }

/** Play a song; the same button pauses / resumes it. */
function playSong(song) {
	if (!song || song.error) return;
	if (state.playing === song.path) {
		if (audio.paused) audio.play().catch(e => toast('Cannot play this file: ' + e.message, 'error'));
		else audio.pause();
		return;
	}
	state.playing = song.path;
	audio.src = '/api/audio?path=' + encodeURIComponent(song.path) + '&v=' + encodeURIComponent((song._source?.sha256 || '').slice(0, 12));
	$('player-title').textContent = [show(song.artist), show(song.title)].filter(Boolean).join(' — ') || song.filename;
	$('player-title').title = song.path;
	$('player').hidden = false;
	audio.play().catch(e => toast('Cannot play this file: ' + e.message, 'error'));
	refreshPlayButtons();
}

function stopPlayer() {
	audio.pause();
	audio.removeAttribute('src');
	audio.load();
	state.playing = null;
	$('player').hidden = true;
	refreshPlayButtons();
}
$('player-stop').onclick = stopPlayer;

/** Show ⏸ on the row (and the editor button) of the song that is playing. */
function refreshPlayButtons() {
	for (const button of document.querySelectorAll('#rows .play-btn')) {
		const playing = isPlaying(button.closest('tr')?.dataset.path);
		button.textContent = playing ? '⏸' : '▶';
		button.classList.toggle('playing', playing);
		button.title = (playing ? 'Pause ' : 'Listen to ') + (button.closest('tr') && state.byPath.get(button.closest('tr').dataset.path)?.filename || '');
	}
	const editorButton = $('editor-listen');
	if (state.editor) {
		const playing = isPlaying(state.editor.song.path);
		editorButton.textContent = playing ? '⏸ Pause' : '▶ Listen';
		editorButton.classList.toggle('playing', playing);
	}
}
for (const event of ['play', 'pause', 'ended']) audio.addEventListener(event, refreshPlayButtons);
audio.addEventListener('error', () => {
	if (state.playing) toast('This file cannot be played in the browser.', 'error');
	refreshPlayButtons();
});
$('editor-listen').onclick = () => { if (state.editor) playSong(state.editor.song); };


// =============================================================================
// 7. DRAFTS
// =============================================================================

/** The value in the file (what Save would compare a draft with). */
function savedValue(song, field) {
	if (field === 'lyrics') return song.lyrics_text || '';
	if (field === 'length') return song.length_tag || '';
	return song[field] ?? '';
}

/** The draft value, or else the value in the file. */
function currentValue(song, field) {
	const edits = state.drafts.get(song.path) || {};
	return Object.hasOwn(edits, field) ? edits[field] : savedValue(song, field);
}

function sameAsFile(song, field, value) {
	if (field === 'cover') return value === '' && !song.cover;
	if (field === 'video') return videoIsYes(value) === videoIsYes(song.video);
	const saved = savedValue(song, field);
	return ['lyrics', 'comment'].includes(field) ? value === saved : value.trim() === saved.trim();
}

/** Set (or remove, when it equals the file again) a draft. A song with a draft is selected. */
function setDraft(song, field, value) {
	const edits = {...(state.drafts.get(song.path) || {})};
	if (sameAsFile(song, field, value)) delete edits[field];
	else edits[field] = value;
	if (Object.keys(edits).length) {
		state.drafts.set(song.path, edits);
		state.selected.add(song.path);
	} else {
		state.drafts.delete(song.path);
	}
	render();
}

$('discard').onclick = () => {
	if (state.drafts.size && confirm('Discard all ' + state.drafts.size + ' songs with unsaved drafts?')) {
		state.drafts.clear();
		render();
	}
};

window.addEventListener('beforeunload', event => {
	if (state.drafts.size) { event.preventDefault(); event.returnValue = ''; }
});


// =============================================================================
// 8. TAG EDITOR
// =============================================================================
// state.editor = {song, field, preview, input, initial, cover: {dataUrl | url} | null, generation}

function openEditor(song, field, preview) {
	if (state.busy || state.writing) return;
	const editor = {song, field, preview, input: null, initial: null, cover: null, generation: 0, image: null};
	state.editor = editor;
	const label = FIELD_LABELS[field] || field;
	$('editor-title').textContent = preview ? 'Cover' : 'Edit ' + label;
	$('editor-file').textContent = song.path;
	const body = $('editor-body');
	body.replaceChildren();
	const edits = state.drafts.get(song.path) || {};
	const hasDraft = Object.hasOwn(edits, field);

	if (field === 'cover') {
		const src = hasDraft ? edits.cover : (song.cover ? '/api/cover?path=' + encodeURIComponent(song.path) : '');
		editor.image = el('img', {className: 'cover-preview', alt: 'Cover'});
		const empty = el('div', {className: 'cover-empty'}, hasDraft ? 'The cover will be removed.' : 'No embedded cover.');
		if (src) editor.image.src = src; else editor.image.hidden = true;
		empty.hidden = Boolean(src);
		editor.empty = empty;
		body.append(el('div', {className: 'cover-box'}, editor.image, empty));
		if (!preview) {
			const file = el('input', {type: 'file', accept: 'image/jpeg,image/png,image/webp', ariaLabel: 'Choose a cover image'});
			file.onchange = () => chooseCoverFile(editor, file);
			body.append(el('label', {className: 'file-pick'}, 'Choose a picture on this computer: ', file),
				el('p', {className: 'muted'}, 'The picture replaces all embedded pictures with one front cover of at most 600 × 600 '
					+ '(made smaller in the browser, never enlarged). JPEG, PNG or WEBP.'));
		}
	} else if (field === 'video') {
		const box = el('input', {type: 'checkbox', checked: videoIsYes(currentValue(song, 'video'))});
		editor.input = box;
		body.append(el('label', {className: 'check big'}, box, ' Video available (VIDEO = Yes)'),
			el('p', {className: 'muted'}, 'Unticked = the VIDEO tag is removed. Saved in the file: ' + (show(song.video) || '(empty)')));
	} else {
		const multiline = ['comment', 'lyrics'].includes(field);
		const input = el(multiline ? 'textarea' : 'input', {id: 'editor-value', spellcheck: multiline});
		if (multiline) input.rows = field === 'lyrics' ? 14 : 6; else input.type = 'text';
		const value = currentValue(song, field);
		input.value = show(value);
		editor.initial = input.value;
		editor.input = input;
		body.append(input);
		if (String(value).includes('\u0000')) {
			body.append(el('p', {className: 'notice'}, 'This tag holds several values (shown with " / "). '
				+ 'OK without changes keeps them; a changed text is saved as one value.'));
		}
		if (hasDraft) body.append(el('p', {className: 'muted'}, 'Saved in the file: ', el('span', {className: 'saved-value'}, shorten(savedValue(song, field), 200) || '(empty)')));
		const notes = {
			length: 'Stored TLEN in milliseconds. This does not change the audio or the measured duration.',
			lyrics: 'Saving replaces all lyrics of the file, also synchronised (LRC/SYLT) ones. LRC lines look like [01:23.45] text.',
			year: 'Four digits, or empty.', track: 'A number, or number/total (6/12).', bpm: 'A number.',
			tvshow: 'A YouTube link (https://www.youtube.com/watch?v=…) or a video file name.',
		};
		if (notes[field]) body.append(el('p', {className: 'muted'}, notes[field]));
		if (!multiline) input.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); $('editor-ok').click(); } });
		setTimeout(() => input.focus(), 30);
	}

	$('editor-ok').hidden = $('editor-clear').hidden = preview;
	$('editor-ok').disabled = false;
	$('editor-cancel').textContent = preview ? 'Close' : 'Cancel';
	$('editor-listen').hidden = Boolean(song.error);
	refreshPlayButtons();
	setupLookup(editor);
	$('editor').showModal();
}

function chooseCoverFile(editor, input) {
	const file = input.files[0];
	if (!file) return;
	if (file.size > MAX_SOURCE_IMAGE) { toast('Choose a picture smaller than 25 MiB.', 'error'); input.value = ''; return; }
	const reader = new FileReader();
	reader.onload = async () => {
		try {
			const dataUrl = await shrinkImage(reader.result, file.size);
			if (state.editor !== editor) return;
			editor.cover = {dataUrl};
			showEditorCover(editor, dataUrl);
			markChosen(null);
		} catch (e) {
			toast('This picture cannot be used: ' + e.message, 'error');
		}
	};
	reader.readAsDataURL(file);
}

/** Make a big picture at most 600 x 600 in the browser (white behind transparent parts). */
function shrinkImage(dataUrl, bytes) {
	return new Promise((resolve, reject) => {
		const image = new Image();
		image.onload = () => {
			const scale = Math.min(1, COVER_SIZE / Math.max(image.naturalWidth, image.naturalHeight));
			if (scale === 1 && bytes <= 2 * 1024 * 1024) { resolve(dataUrl); return; }
			const canvas = el('canvas', {width: Math.round(image.naturalWidth * scale), height: Math.round(image.naturalHeight * scale)});
			const context = canvas.getContext('2d');
			context.fillStyle = '#fff';
			context.fillRect(0, 0, canvas.width, canvas.height);
			context.drawImage(image, 0, 0, canvas.width, canvas.height);
			resolve(canvas.toDataURL('image/jpeg', 0.92));
		};
		image.onerror = () => reject(Error('not a readable JPEG, PNG or WEBP image'));
		image.src = dataUrl;
	});
}

function showEditorCover(editor, src) {
	editor.image.src = src;
	editor.image.hidden = false;
	editor.empty.hidden = true;
}

$('editor-ok').onclick = async () => {
	const editor = state.editor;
	if (!editor) return;
	const {song, field} = editor;
	if (field === 'cover') {
		if (!editor.cover) { $('editor').close(); return; }
		if (editor.cover.dataUrl) { $('editor').close(); setDraft(song, 'cover', editor.cover.dataUrl); return; }
		$('editor-ok').disabled = true;                     // a lookup result: download it first
		$('lookup-message').textContent = 'Downloading the cover…';
		try {
			const result = await apiPost('/api/lookup/cover', {url: editor.cover.url});
			if (state.editor === editor) { $('editor').close(); setDraft(song, 'cover', result.value); }
		} catch (e) {
			if (state.editor === editor) toast('Cannot use this cover: ' + e.message, 'error');
		} finally {
			$('editor-ok').disabled = false;
		}
		return;
	}
	if (field === 'video') { $('editor').close(); setDraft(song, 'video', editor.input.checked ? 'Yes' : ''); return; }
	const value = editor.input.value;
	$('editor').close();
	if (value === editor.initial) return;               // nothing changed
	setDraft(song, field, value);
};
$('editor-clear').onclick = () => {
	const editor = state.editor;
	if (!editor) return;
	$('editor').close();
	setDraft(editor.song, editor.field, '');
};
$('editor-cancel').onclick = () => $('editor').close();
$('editor').addEventListener('close', () => { state.editor = null; });

// ---------- online lookups inside the editor ----------

function setupLookup(editor) {
	const section = $('lookup');
	const providers = state.config.providers[editor.field];
	section.hidden = editor.preview || !LOOKUP_FIELDS.has(editor.field) || !providers;
	$('lookup-results').replaceChildren();
	$('lookup-message').textContent = '';
	$('lookup-run').disabled = false;
	if (section.hidden) return;
	$('lookup-artist').value = show(currentValue(editor.song, 'artist'));
	$('lookup-title').value = show(currentValue(editor.song, 'title'));
	const sources = state.config.sources || {};
	$('lookup-sources').replaceChildren(...providers.map(([id, name]) => {
		const missing = (id === 'discogs' && !sources.discogs) || (id === 'youtube' && !sources.youtube);
		const limited = id === 'youtube' && !missing && state.quota?.reached;
		const box = el('input', {type: 'checkbox', checked: !missing && !limited, disabled: missing, value: id});
		let suffix = '', title = '';
		if (missing) { suffix = ' (not configured)'; title = 'Not configured: add the key / token to .env'; }
		else if (limited) { suffix = ' (daily limit reached)'; title = 'All YouTube searches of today are used. The counter resets at ' + state.quota.resets_local + '.'; }
		else if (id === 'youtube' && state.quota) { suffix = ' (' + state.quota.searches + ' / ' + state.quota.limit + ' today)'; title = 'YouTube searches used today. A cached result costs nothing.'; }
		return el('label', {className: 'check' + (missing || limited ? ' muted' : ''), title}, box, ' ' + name + suffix);
	}));
}

function markChosen(card) {
	document.querySelectorAll('#lookup-results .result').forEach(c => c.classList.toggle('chosen', c === card));
}

function useResult(editor, item, card) {
	markChosen(card);
	if (editor.field === 'cover') {
		editor.cover = {url: item.value};
		showEditorCover(editor, item.value);
	} else {
		editor.input.value = item.value;
	}
}

$('lookup-run').onclick = async () => {
	const editor = state.editor;
	if (!editor) return;
	const providers = state.config.providers[editor.field] || [];
	const chosen = [...$('lookup-sources').querySelectorAll('input:checked')].map(b => b.value);
	if (!chosen.length) { $('lookup-message').textContent = 'Tick at least one source.'; return; }
	const metadata = {artist: $('lookup-artist').value.trim(), title: $('lookup-title').value.trim(),
		album: show(currentValue(editor.song, 'album'))};
	if (!metadata.artist || !metadata.title) { $('lookup-message').textContent = 'Artist and title are needed.'; return; }
	const generation = ++editor.generation;
	const alive = () => state.editor === editor && editor.generation === generation;
	const columns = new Map();
	$('lookup-results').replaceChildren(...providers.filter(([id]) => chosen.includes(id)).map(([id, name]) => {
		const column = el('div', {className: 'lookup-column'}, el('h4', null, name), el('p', {className: 'muted'}, 'Searching…'));
		columns.set(id, column);
		return column;
	}));
	$('lookup-run').disabled = true;
	$('editor-ok').disabled = true;
	$('lookup-message').textContent = 'Searching…';

	// MusicHoarders cannot be searched by the server: open it in a new tab (during the click)
	if (chosen.includes('musichoarders')) {
		const url = new URL('https://covers.musichoarders.xyz/');
		url.searchParams.set('artist', metadata.artist);
		url.searchParams.set('album', '');
		window.open(url.toString(), '_blank', 'noopener,noreferrer');
		columns.get('musichoarders').replaceChildren(el('h4', null, 'MusicHoarders'),
			el('a', {href: url.toString(), target: '_blank', rel: 'noopener noreferrer'}, 'Open the MusicHoarders cover search'),
			el('p', {className: 'muted'}, 'Download a cover there, then choose it above with "Choose a picture".'));
	}
	const results = [];
	await Promise.all(chosen.filter(id => !state.config.browser_only?.includes(id)).map(async id => {
		const name = providers.find(p => p[0] === id)[1];
		const column = columns.get(id);
		try {
			const data = await apiPost('/api/lookup', {path: editor.song.path, field: editor.field, provider: id, metadata});
			if (data.quota) showYoutubeQuota(data.quota, true);
			if (!alive()) return;
			column.replaceChildren(el('h4', null, name));
			if (!data.items.length) column.append(el('p', {className: 'muted'}, 'No usable results.'));
			for (const item of data.items) {
				const card = el('div', {className: 'result'});
				const use = el('button', {type: 'button', className: 'small', onclick: () => useResult(editor, item, card)}, 'Use');
				card.append(el('div', {className: 'result-head'}, use, el('span', {className: 'result-label'}, item.label || item.value)));
				if (editor.field === 'cover') {
					card.append(el('img', {className: 'result-cover', src: item.value, alt: item.label || 'Cover', loading: 'lazy', referrerPolicy: 'no-referrer',
						onerror: function () { use.disabled = true; this.replaceWith(el('p', {className: 'muted'}, 'Preview not available.')); }}));
				} else if (['lyrics', 'comment'].includes(editor.field)) {
					card.append(el('pre', {className: 'result-text'}, item.value));
				} else {
					card.append(el('code', {className: 'result-value'}, item.value));
				}
				if (/^https?:\/\//.test(item.source || '')) card.append(el('a', {href: item.source, target: '_blank', rel: 'noopener noreferrer'}, 'View source'));
				column.append(card);
				results.push({item, card, use});
			}
			if (data.warnings?.length) {
				column.append(el('details', {className: 'warnings'}, el('summary', null, 'Notes (' + data.warnings.length + ')'),
					...data.warnings.map(w => el('p', null, w))));
			}
		} catch (e) {
			if (e.data?.quota) showYoutubeQuota(e.data.quota, true);
			if (alive()) {
				column.replaceChildren(el('h4', null, name), el('p', {className: 'error-text'}, 'Lookup failed: ' + e.message));
				if (id === 'youtube' && e.data?.quota?.reached) toast(e.message, 'error', 10000);
			}
		}
	}));
	if (!alive()) return;
	// year: propose the earliest year that was found (as in V1)
	if (editor.field === 'year') {
		const years = results.filter(r => /^[12]\d{3}$/.test(r.item.value)).sort((a, b) => Number(a.item.value) - Number(b.item.value));
		if (years.length) useResult(editor, years[0].item, years[0].card);
	}
	$('lookup-run').disabled = false;
	$('editor-ok').disabled = false;
	$('lookup-message').textContent = results.length ? 'Click Use on a result, then OK. OK only makes a draft.' : 'Nothing found.';
};


// =============================================================================
// 9. SAVE AND WIPE
// =============================================================================

function selectedSongs() { return state.songs.filter(s => state.selected.has(s.path)); }

// ---------- wipe: choose the fields ----------
const PROTECTED_BY_DEFAULT = ['artist', 'title', 'album'];

function wipeChosenText() {
	const n = state.wipeFields.size;
	const unprotected = PROTECTED_BY_DEFAULT.filter(f => state.wipeFields.has(f)).map(f => FIELD_LABELS[f]);
	$('wipe-chosen').textContent = n + ' field' + (n === 1 ? '' : 's') + ' ticked'
		+ (unprotected.length ? ' — including ' + unprotected.join(', ') + '!' : '');
	$('wipe-chosen').className = unprotected.length ? 'error-text' : 'muted';
	$('wipe-prepare').disabled = !n;
}

function drawWipeFields() {
	$('wipe-fields').replaceChildren(...Object.entries(FIELD_LABELS).map(([field, label]) => {
		const box = el('input', {type: 'checkbox', checked: state.wipeFields.has(field)});
		box.onchange = () => { box.checked ? state.wipeFields.add(field) : state.wipeFields.delete(field); wipeChosenText(); };
		return el('label', {className: 'check' + (PROTECTED_BY_DEFAULT.includes(field) ? ' strong' : '')}, box, ' ' + label);
	}));
	wipeChosenText();
}
$('wipe').onclick = () => {
	if (state.drafts.size) { toast('Save or discard the drafts before wiping tags.', 'warn'); return; }
	const count = state.selected.size;
	const hiddenSelected = selectedSongs().length - $('rows').querySelectorAll('tr.selected').length;
	$('wipe-count').textContent = count + ' selected song' + (count === 1 ? '' : 's')
		+ (hiddenSelected > 0 ? ' (' + hiddenSelected + ' not shown because of the filter)' : '') + '.';
	drawWipeFields();
	$('wipe-dialog').showModal();
};
$('wipe-defaults').onclick = () => { state.wipeFields = new Set(DEFAULT_WIPE); drawWipeFields(); };
$('wipe-all').onclick = () => { state.wipeFields = new Set(Object.keys(FIELD_LABELS)); drawWipeFields(); };
$('wipe-none').onclick = () => { state.wipeFields = new Set(); drawWipeFields(); };
$('wipe-cancel').onclick = () => $('wipe-dialog').close();
$('wipe-prepare').onclick = () => {
	if (!state.wipeFields.size) { toast('Tick at least one field.', 'warn'); return; }
	$('wipe-dialog').close();
	prepare('wipe');
};
$('save').onclick = () => prepare('save');

// ---------- prepare: the server checks everything and returns an exact plan ----------
async function prepare(action) {
	if (state.busy || state.writing) return;
	const chosen = selectedSongs();
	const targets = action === 'save' ? chosen.filter(s => state.drafts.has(s.path)) : chosen;
	if (!targets.length) { toast(action === 'save' ? 'Select songs with drafts.' : 'Select songs first.', 'warn'); return; }
	if (targets.length > state.config.max_batch) { toast('At most ' + state.config.max_batch + ' songs at once.', 'warn'); return; }
	setBusy(true);
	setStatus('Checking the files and preparing the ' + action + '…');
	try {
		const fields = [...state.wipeFields];
		const items = targets.map(s => ({path: s.path, source: s._source, values: state.drafts.get(s.path) || {}}));
		const plan = await apiPost('/api/write/prepare', {action, fields, items});
		state.plan = plan;
		showPlan(plan);
		setStatus('');
	} catch (e) {
		setStatus(e.message, 'error');
		toast(e.message, 'error', 8000);
	} finally {
		setBusy(false);
	}
}

function valueView(field, value, path) {
	if (field === 'cover') {
		if (value && typeof value === 'object') {
			const draft = state.drafts.get(path)?.cover;
			return draft ? el('img', {className: 'thumb', src: draft, alt: 'New cover'}) : 'New picture (' + value.jpeg_bytes + ' bytes)';
		}
		if (Array.isArray(value)) return value.length ? el('img', {className: 'thumb', alt: 'Current cover',
			src: '/api/thumbnail?path=' + encodeURIComponent(path)}) : '—';
		return value === '' ? el('em', null, 'removed') : String(value);
	}
	if (Array.isArray(value)) value = value.join(' | ');
	if (value === '') return el('em', null, field === 'video' ? 'removed (No)' : 'removed');
	return shorten(value, 220) || '—';
}

function showPlan(plan) {
	const wipe = plan.action === 'wipe';
	$('confirm-title').textContent = (wipe ? 'Wipe tags of ' : 'Save tags of ') + plan.items.length + ' song' + (plan.items.length === 1 ? '' : 's');
	$('confirm-warning').textContent = plan.enabled
		? 'Each original is backed up and verified before it is replaced. Only the changes below are written.'
		: 'Writing is off (read-only). You can check this plan, but not write it.';
	$('confirm-warning').className = 'notice' + (plan.enabled ? '' : ' warn');
	const box = $('confirm-summary');
	box.replaceChildren();
	const warnings = new Set();
	for (const item of plan.items) {
		item.warnings.forEach(w => warnings.add(w));
		const card = el('div', {className: 'plan-item'}, el('h3', null, item.path));
		if (wipe) {
			const frames = item.changes.remove_frames;
			card.append(el('p', null, el('b', null, 'Removes: '), frames.length ? frames.join(', ') : 'nothing (these fields are empty)',
				item.changes.remove_unknown_frames ? ' + ' + item.changes.remove_unknown_frames + ' unknown frame(s)' : ''),
				el('p', {className: 'muted'}, 'Protected: ' + item.changes.protected_fields.map(f => FIELD_LABELS[f] || f).join(', ')));
		} else {
			const table = el('table', {className: 'changes'}, el('tr', null, el('th', null, 'Field'), el('th', null, 'Now in the file'), el('th', null, 'New')));
			for (const [field, change] of Object.entries(item.changes.edited_fields)) {
				table.append(el('tr', null, el('td', null, FIELD_LABELS[field] || field),
					el('td', null, valueView(field, change.old, item.path)), el('td', {className: 'new'}, valueView(field, change.new, item.path))));
			}
			card.append(table);
		}
		box.append(card);
	}
	if (warnings.size) box.append(el('div', {className: 'notice warn'}, el('b', null, 'Please note'), el('ul', null, ...[...warnings].map(w => el('li', null, w)))));
	$('confirm-json').textContent = JSON.stringify(plan.items, null, 2);
	$('confirm-details').open = false;
	$('confirm-phrase-label').hidden = !wipe;
	$('confirm-phrase').textContent = state.config.wipe_phrase;
	// V2.1: the confirmation text is filled in; the server still checks it, the "Wipe these tags"
	// button is the confirmation. Changing the text disables the button again.
	$('confirm-input').value = wipe ? state.config.wipe_phrase : '';
	$('confirm-ok').textContent = wipe ? 'Wipe these tags' : 'Save these changes';
	$('confirm-ok').className = wipe ? 'danger' : 'primary';
	$('confirm-ok').disabled = !plan.enabled;
	$('confirm').showModal();
	if (wipe) $('confirm-cancel').focus();          // so that Enter does not wipe by accident
}

$('confirm-input').oninput = () => {
	$('confirm-ok').disabled = !state.plan?.enabled || $('confirm-input').value !== state.config.wipe_phrase;
};

async function cancelPlan() {
	const plan = state.plan;
	state.plan = null;
	if ($('confirm').open) $('confirm').close();
	if (plan) { try { await apiPost('/api/write/cancel', {token: plan.token}); } catch (e) { /* expires anyway */ } }
}
$('confirm-cancel').onclick = cancelPlan;
$('confirm').addEventListener('cancel', event => { event.preventDefault(); cancelPlan(); });

$('confirm-ok').onclick = async () => {
	const plan = state.plan;
	if (!plan) return;
	$('confirm-ok').disabled = true;
	try {
		await apiPost('/api/write/apply', {token: plan.token, confirmed: true, confirmation: $('confirm-input').value});
		state.plan = null;
		$('confirm').close();
		watchJob(plan.items.length);
	} catch (e) {
		$('confirm-warning').textContent = e.message;
		$('confirm-warning').className = 'notice error';
		$('confirm-ok').disabled = plan.action === 'wipe' && $('confirm-input').value !== state.config.wipe_phrase;
	}
};

// ---------- the background job ----------
function showProgress(done, total, action) {
	const bar = $('progress');
	bar.hidden = false;
	bar.querySelector('.progress-fill').style.width = (total ? Math.round(100 * done / total) : 0) + '%';
	bar.querySelector('.progress-text').textContent = (action === 'wipe' ? 'Wiping ' : 'Saving ') + done + ' / ' + total;
}

async function watchJob(total) {
	state.writing = true;
	updateControls();
	try {
		let status;
		while (true) {
			status = await apiGet('/api/write/status');
			showProgress(status.results.length, status.total || total || status.results.length, status.action);
			if (status.state !== 'running') break;
			await new Promise(resolve => setTimeout(resolve, 800));
		}
		state.lastJob = status;
		const saved = status.results.filter(r => r.status === 'saved').map(r => r.path);
		const failed = status.results.filter(r => r.status !== 'saved');
		// saved songs: drafts are gone, and they are deselected (they were selected because of the drafts)
		if (status.action === 'save') saved.forEach(p => { state.drafts.delete(p); state.selected.delete(p); });
		if (saved.length) {
			const fresh = await apiPost('/api/songs', {paths: saved});
			for (const row of fresh.songs) {
				const index = state.songs.findIndex(s => s.path === row.path);
				if (index >= 0) state.songs[index] = row;
				state.byPath.set(row.path, row);
			}
		}
		render();
		if (status.state === 'failed') {
			toast('The ' + status.action + ' stopped: ' + status.error, 'error', 10000);
			showReport();
		} else if (failed.length) {
			toast(saved.length + ' saved, ' + failed.length + ' failed. See the report.', 'error', 10000);
			showReport();
		} else {
			toast((status.action === 'wipe' ? 'Wiped ' : 'Saved ') + saved.length + ' song' + (saved.length === 1 ? '' : 's') + '.', 'success');
		}
	} catch (e) {
		setStatus('Lost the connection to the save job: ' + e.message + '. Reload the page.', 'error');
	} finally {
		state.writing = false;
		$('progress').hidden = true;
		updateControls();
	}
}


// =============================================================================
// 10. DETAILS, REPORT, EXPORT, COLUMNS
// =============================================================================

function openInfo(title, nodes, json) {
	$('info-title').textContent = title;
	$('info-body').replaceChildren(...nodes);
	$('info-download').hidden = !json;
	$('info-download').onclick = json ? () => download(json.name, json.data) : null;
	$('info').showModal();
}
$('info-close').onclick = () => $('info').close();

function keyValueTable(rows) {
	return el('table', {className: 'kv'}, ...rows.map(([key, value]) => {
		const text = show(value);
		return el('tr', null, el('th', null, key), el('td', null, text.length > 140 || text.includes('\n') ? el('pre', null, text) : text || '—'));
	}));
}

function showDetails(song, focus) {
	const nodes = [];
	nodes.push(el('h3', null, 'File'), keyValueTable([
		['Path', song.path], ['Duration', song.duration || ''], ['Size', song._source ? (song._source.size / 1048576).toFixed(2) + ' MiB' : ''],
		['SHA-256', song._source?.sha256 || ''], ...(song.error ? [['Read error', song.error]] : [])]));
	const drafts = state.drafts.get(song.path);
	if (drafts) nodes.push(el('h3', null, 'Unsaved drafts'), keyValueTable(Object.entries(drafts).map(([f, v]) =>
		[FIELD_LABELS[f] || f, f === 'cover' ? (v ? 'new picture' : 'removed') : (v === '' ? '(cleared)' : v)])));
	const tagRows = Object.entries(song.all_tags || {});
	nodes.push(el('h3', null, 'All ID3 tags (' + tagRows.length + ')'), tagRows.length ? keyValueTable(tagRows) : el('p', {className: 'muted'}, 'No ID3v2 tags.'));
	const other = song.other_info || {keys: [], unknown_frames: 0};
	const otherSection = el('div', null, el('h3', null, 'Other tags (' + (song.other || 0) + ')'),
		el('p', {className: 'muted'}, 'ID3 frames without a column. "Wipe tags" with Other tags ticked removes these.'),
		other.keys.length ? el('ul', null, ...other.keys.map(k => el('li', null, el('code', null, k), ' ', shorten(song.all_tags?.[k] ?? '', 120)))) : '',
		other.unknown_frames ? el('p', null, other.unknown_frames + ' unknown frame(s) that mutagen cannot read.') : '');
	nodes.push(otherSection);
	openInfo(song.filename, nodes, {name: song.filename.replace(/\.mp3$/i, '') + '-tags.json', data: song});
	if (focus === 'other') otherSection.scrollIntoView({block: 'start'});
}

async function showReport() {
	let status = state.lastJob;
	try { status = await apiGet('/api/write/status'); } catch (e) { /* use the last known */ }
	if (!status || status.state === 'idle') { toast('No save or wipe yet since the app started.'); return; }
	const nodes = [keyValueTable([
		['Action', status.action], ['State', status.state + (status.error ? ' — ' + status.error : '')],
		['Started', status.started || ''], ['Finished', status.finished || ''], ['Batch', status.batch || ''],
		['Backups', status.backups_removed ? 'Removed: every file was saved and verified (KEEP_BACKUPS=false).'
			: (status.report_file ? 'Kept in ' + status.report_file.replace(/\/write-report\.json$/, '') : '—')],
		...(status.cleanup_warning ? [['Clean-up', status.cleanup_warning]] : [])])];
	const table = el('table', {className: 'changes'}, el('tr', null, el('th', null, 'File'), el('th', null, 'Result'), el('th', null, 'Details')));
	for (const r of status.results) {
		table.append(el('tr', {className: r.status === 'saved' ? '' : 'failed'}, el('td', null, r.path),
			el('td', null, r.status === 'saved' ? '✔ saved' : '✖ ' + r.status),
			el('td', null, r.error ? el('pre', null, r.error) : (r.warning || (r.normalized_empty_frames?.length ? 'Empty frames dropped: ' + r.normalized_empty_frames.join(', ') : '')))));
	}
	nodes.push(el('h3', null, 'Files'), status.results.length ? table : el('p', {className: 'muted'}, 'No files processed.'));
	openInfo('Last ' + (status.action || 'job') + ' report', nodes, {name: 'jstal-tagger-report.json', data: status});
}
$('report-btn').onclick = showReport;

$('export').onclick = () => download('jstal-tagger-selection.json', {
	app: 'jstal-tagger', version: state.config.version, exported: new Date().toISOString(),
	folder: state.listedPath, songs: selectedSongs(),
});

function drawColumnChoices() {
	$('columns-list').replaceChildren(...COLUMNS.filter(c => c.key !== 'filename').map(column => {
		const box = el('input', {type: 'checkbox', checked: !state.hidden.has(column.key)});
		box.onchange = () => {
			box.checked ? state.hidden.delete(column.key) : state.hidden.add(column.key);
			store.set('jstal-hidden-columns', [...state.hidden]);
			render();
		};
		return el('label', {className: 'check'}, box, ' ' + column.label);
	}));
}
$('columns-btn').onclick = () => { drawColumnChoices(); $('columns-dialog').showModal(); };
$('columns-all').onclick = () => { state.hidden.clear(); store.set('jstal-hidden-columns', []); drawColumnChoices(); render(); };
$('columns-close').onclick = () => $('columns-dialog').close();


// =============================================================================
// 11. START-UP
// =============================================================================

(async function start() {
	setTheme(document.documentElement.dataset.theme === 'light' ? 'light' : 'dark');
	tick();
	render();
	try {
		state.config = await apiGet('/api/config');
	} catch (e) {
		toast('Cannot read the settings of the server: ' + e.message, 'error');
	}
	showWriteBadge();
	showYoutubeQuota(state.config.youtube_quota);
	// V2.1: start in edit mode (EDIT_MODE_DEFAULT in .env). Editing only makes drafts.
	$('edit-toggle').checked = state.config.edit_mode_default !== false;
	await browse('');
	try {
		const status = await apiGet('/api/write/status');
		if (status.state === 'running') watchJob(status.total);
		else if (status.state !== 'idle') state.lastJob = status;
	} catch (e) {
		setStatus(e.message, 'error');
	}
})();
