// "When is it calm?" — pick a start town, slide the departure time, and every destination
// is coloured by how congested the trip usually is (travel time vs an empty road).
// Levels only, no minutes. ?demo shows made-up data to try the map out.
const $ = id => document.getElementById(id);
const DEMO = new URLSearchParams(location.search).has('demo');
const LEVEL_VAR = {calm: '--calm', moderate: '--moderate', busy: '--busy', very_busy: '--very-busy', none: '--no-data'};
const state = {data: null, stats: {}, origin: null, bucket: 0, layers: [], timer: null};

const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const place = key => state.data.places[key];
const store = {
  get(key) { try { return localStorage.getItem(key); } catch { return null; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch { /* private mode */ } },
};

function levelOf(entry) {
  if (!entry || entry[0] < state.data.min_samples) return {id: 'none', label: 'Not enough data yet'};
  return state.data.levels.find(level => level.below === null || entry[1] < level.below);
}

// --- demo data: deterministic fake rush hours to every town ---------------------------
function seeded(text) {
  let h = 2166136261;
  for (const ch of text) h = Math.imul(h ^ ch.charCodeAt(0), 16777619);
  return () => { h = Math.imul(h ^ (h >>> 15), 2246822507); h = Math.imul(h ^ (h >>> 13), 3266489909); return ((h ^= h >>> 16) >>> 0) / 4294967296; };
}
function km(a, b) {
  const r = Math.PI / 180, dLat = (b[0] - a[0]) * r, dLon = (b[1] - a[1]) * r;
  const x = Math.sin(dLat / 2) ** 2 + Math.cos(a[0] * r) * Math.cos(b[0] * r) * Math.sin(dLon / 2) ** 2;
  return 12742 * Math.asin(Math.sqrt(x));
}
function demoStats(origin) {
  const stats = {}, from = place(origin).point;
  for (const [key, target] of Object.entries(state.data.places)) {
    if (key === origin || km(from, target.point) < 12) continue;
    const random = seeded(origin + key);
    const busyTown = /^(brussels|antwerp|ghent|leuven|e313|a12)/.test(key);
    const strength = (busyTown ? 0.75 : km(from, target.point) > 60 ? 0.45 : 0.25) * (0.7 + 0.6 * random());
    stats[key] = {};
    for (const bucket of state.data.buckets) {
      const hour = +bucket.slice(0, 2) + +bucket.slice(3) / 60;
      const rush = Math.exp(-(((hour - 7.75) / 0.85) ** 2)) + 0.85 * Math.exp(-(((hour - 16.9) / 0.95) ** 2));
      stats[key][bucket] = [2 + Math.floor(random() * 11), strength * rush * (0.75 + 0.5 * random()) + 0.04 * random()];
    }
  }
  return stats;
}

// --- map -----------------------------------------------------------------------------
const map = L.map('map', {zoomSnap: 0.25, scrollWheelZoom: false}).setView([50.75, 4.5], 8);
const dark = window.matchMedia('(prefers-color-scheme: dark)');
// Standard OpenStreetMap tiles, muted to grey in map.css so the coloured trips stand out.
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 13,
  attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
}).addTo(map);
dark.addEventListener?.('change', () => draw());

// A gentle curve from start to destination, so lines to nearby towns don't overlap.
function arc(a, b) {
  const mid = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2], dx = b[1] - a[1], dy = b[0] - a[0];
  const control = [mid[0] + dx * 0.12, mid[1] - dy * 0.12];
  return Array.from({length: 25}, (_, i) => {
    const t = i / 24;
    return [(1 - t) ** 2 * a[0] + 2 * (1 - t) * t * control[0] + t * t * b[0],
            (1 - t) ** 2 * a[1] + 2 * (1 - t) * t * control[1] + t * t * b[1]];
  });
}

function destinations() {
  return DEMO ? Object.keys(state.stats) : state.data.destinations[state.origin] || [];
}

function draw() {
  state.layers.forEach(layer => layer.remove());
  state.layers = [];
  const origin = place(state.origin), bucket = state.data.buckets[state.bucket];
  const rows = [];
  for (const key of destinations()) {
    const target = place(key), entry = state.stats[key]?.[bucket], level = levelOf(entry);
    const color = css(LEVEL_VAR[level.id]);
    const measured = entry ? entry[0] : 0;
    const tip = `<strong>${origin.name} → ${target.name}</strong><br>Leaving ${bucket}: <strong>${level.label}</strong><br>` +
      `${measured} measurement${measured === 1 ? '' : 's'} at this time`;
    const path = arc(origin.point, target.point);
    const line = L.polyline(path, {color, weight: 2.5, opacity: level.id === 'none' ? 0.45 : 0.8,
                                   dashArray: level.id === 'none' ? '4 6' : null, interactive: false});
    const hit = L.polyline(path, {opacity: 0, weight: 14}).bindTooltip(tip, {sticky: true, className: 'trip'});
    const dot = L.circleMarker(target.point, {radius: 8, color: css('--ring'), weight: 2, fillColor: color, fillOpacity: 1})
      .bindTooltip(tip, {className: 'trip', direction: 'top', offset: [0, -6]});
    hit.on('mouseover', () => line.setStyle({weight: 4.5}));
    hit.on('mouseout', () => line.setStyle({weight: 2.5}));
    state.layers.push(line.addTo(map), hit.addTo(map), dot.addTo(map));
    rows.push({name: target.name, level, measured});
  }
  const you = L.circleMarker(origin.point, {radius: 10, color: css('--ring'), weight: 3, fillColor: css('--you'), fillOpacity: 1})
    .bindTooltip(`Start: ${origin.name}`, {permanent: true, direction: 'right', offset: [10, 0], className: 'trip'});
  state.layers.push(you.addTo(map));
  renderList(rows, bucket);
}

function fit() {
  const points = [place(state.origin).point, ...destinations().map(key => place(key).point)];
  map.fitBounds(L.latLngBounds(points), {padding: [36, 36], maxZoom: 10});
}

function renderList(rows, bucket) {
  const order = [...state.data.levels.map(level => level.id), 'none'];
  rows.sort((a, b) => order.indexOf(a.level.id) - order.indexOf(b.level.id) || a.name.localeCompare(b.name));
  const body = $('list'); body.replaceChildren();
  for (const row of rows) {
    const tr = document.createElement('tr');
    const name = document.createElement('td'); name.textContent = row.name;
    const level = document.createElement('td'), span = document.createElement('span'), swatch = document.createElement('i');
    span.className = 'level'; swatch.className = 'swatch'; swatch.style.background = css(LEVEL_VAR[row.level.id]);
    span.append(swatch, row.level.label); level.append(span);
    const count = document.createElement('td'); count.textContent = row.measured;
    tr.append(name, level, count); body.append(tr);
  }
  const judged = rows.filter(row => row.level.id !== 'none').length;
  $('list-meta').textContent = `From ${place(state.origin).name}, leaving ${bucket} · ${judged} of ${rows.length} with enough data`;
}

function renderLegend() {
  const legend = $('legend');
  for (const level of [...state.data.levels, {id: 'none', label: 'Not enough data yet'}]) {
    const li = document.createElement('li'), swatch = document.createElement('i');
    swatch.className = 'swatch'; swatch.style.background = css(LEVEL_VAR[level.id]);
    const text = level.id === 'none' ? level.label : `${level.label} (${level.below === null ? `≥ ${Math.round(state.data.levels.at(-2).below * 100)}%` :
      `< ${Math.round(level.below * 100)}%`} slower than an empty road)`;
    li.append(swatch, text); legend.append(li);
  }
  const you = document.createElement('li'), swatch = document.createElement('i');
  swatch.className = 'swatch'; swatch.style.background = css('--you');
  you.append(swatch, 'Your start'); legend.append(you);
}

// --- controls --------------------------------------------------------------------------
function setOrigin(key) {
  state.origin = key;
  state.stats = DEMO ? demoStats(key) : state.data.stats[key] || {};
  store.set('travelsmart-origin', key);
  history.replaceState(null, '', `${location.search}#${key}`);
  draw(); fit();
}
function setBucket(index) {
  state.bucket = index;
  $('time').value = index;
  $('time-label').textContent = state.data.buckets[index];
  draw();
}
function togglePlay() {
  if (state.timer) { clearInterval(state.timer); state.timer = null; }
  else {
    if (state.bucket === state.data.buckets.length - 1) setBucket(0);
    state.timer = setInterval(() => {
      if (state.bucket >= state.data.buckets.length - 1) { togglePlay(); return; }
      setBucket(state.bucket + 1);
    }, 900);
  }
  $('play').textContent = state.timer ? '❚❚ Pause' : '▶ Play the day';
  $('play').setAttribute('aria-pressed', String(Boolean(state.timer)));
}
function defaultBucket() {
  const asked = state.data.buckets.indexOf(new URLSearchParams(location.search).get('at'));  // e.g. ?at=08:00
  if (asked >= 0) return asked;
  const now = new Intl.DateTimeFormat('en-GB', {timeZone: 'Europe/Brussels', hour: '2-digit', minute: '2-digit', hour12: false}).format(new Date());
  const half = `${now.slice(0, 2)}:${+now.slice(3) < 30 ? '00' : '30'}`;
  const index = state.data.buckets.indexOf(half);
  return index >= 0 ? index : state.data.buckets.indexOf('07:30');
}

async function start() {
  const response = await fetch('data/commutes/map.json', {cache: 'no-store'});
  if (!response.ok) throw new Error('Map data has not been published yet.');
  state.data = await response.json();
  $('demo-banner').hidden = !DEMO;
  const select = $('origin');
  for (const key of state.data.origins) {
    const option = document.createElement('option'); option.value = key; option.textContent = place(key).name; select.append(option);
  }
  const slider = $('time');
  slider.max = state.data.buckets.length - 1;
  $('ticks').replaceChildren(...state.data.buckets.filter(b => b.endsWith(':00') && +b.slice(0, 2) % 2 === 1)
    .map(b => Object.assign(document.createElement('span'), {textContent: b})));
  renderLegend();
  const wanted = location.hash.slice(1) || store.get('travelsmart-origin') || 'diepenbeek';
  select.value = state.data.origins.includes(wanted) ? wanted : state.data.origins[0];
  state.bucket = defaultBucket();
  $('time').value = state.bucket; $('time-label').textContent = state.data.buckets[state.bucket];
  setOrigin(select.value);
  select.addEventListener('change', () => setOrigin(select.value));
  slider.addEventListener('input', () => setBucket(+slider.value));
  $('play').addEventListener('click', togglePlay);
}
start().catch(error => { $('map').textContent = error.message; });
