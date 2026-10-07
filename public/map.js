// "When is it calm?" — two views on the same data, both with a departure-time slider:
//   from: pick a start town; every destination is coloured by how busy the trip usually is
//         (travel time vs an empty road). Optionally narrow it to one destination.
//   to:   pick a destination; every start town is coloured by how long the trip usually takes.
// Lines follow OpenStreetMap road paths where available. ?demo shows made-up data.
const $ = id => document.getElementById(id);
const DEMO = new URLSearchParams(location.search).has('demo');
const LEVEL_VAR = {calm: '--calm', moderate: '--moderate', busy: '--busy', very_busy: '--very-busy', none: '--no-data'};
const TIME_BANDS = [{below: 20, label: 'Under 20 min', color: '--t1'}, {below: 40, label: '20–40 min', color: '--t2'},
                    {below: 60, label: '40–60 min', color: '--t3'}, {below: 90, label: '60–90 min', color: '--t4'},
                    {below: null, label: '90 min or more', color: '--t5'}];
const state = {data: null, mode: 'from', origin: null, target: '', arrive: null, bucket: 0, layers: [], timer: null,
               roads: {}, demo: {}};

const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const place = key => state.data.places[key];
const store = {
  get(key) { try { return localStorage.getItem(key); } catch { return null; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch { /* private mode */ } },
};
const enough = entry => entry && entry[0] >= state.data.min_samples;
function levelOf(entry) {
  if (!enough(entry)) return {id: 'none', label: 'Not enough data yet'};
  return state.data.levels.find(level => level.below === null || entry[1] < level.below);
}
function bandOf(entry) {
  if (!enough(entry)) return {label: 'Not enough data yet', color: '--no-data', none: true};
  return TIME_BANDS.find(band => band.below === null || entry[2] < band.below);
}
const aboutMinutes = minutes => `about ${Math.max(5, Math.round(minutes / 5) * 5)} min`;

// --- demo data: deterministic fake rush hours between all towns ------------------------
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
    const distance = km(from, target.point);
    if (key === origin || distance < 12) continue;
    const random = seeded(origin + key);
    const busyTown = /^(brussels|antwerp|ghent|leuven|e313|a12)/.test(key);
    const strength = (busyTown ? 0.75 : distance > 60 ? 0.45 : 0.25) * (0.7 + 0.6 * random());
    const freeFlow = distance * 1.25 / 100 * 60 + 5;  // minutes on an empty road
    stats[key] = {};
    for (const bucket of state.data.buckets) {
      const hour = +bucket.slice(0, 2) + +bucket.slice(3) / 60;
      const rush = Math.exp(-(((hour - 7.75) / 0.85) ** 2)) + 0.85 * Math.exp(-(((hour - 16.9) / 0.95) ** 2));
      const share = strength * rush * (0.75 + 0.5 * random()) + 0.04 * random();
      stats[key][bucket] = [2 + Math.floor(random() * 11), share, Math.round(freeFlow * (1 + share))];
    }
  }
  return stats;
}
function statsFrom(origin) {
  if (!DEMO) return state.data.stats[origin] || {};
  return (state.demo[origin] ||= demoStats(origin));
}
function destinationsFrom(origin) {
  return DEMO ? Object.keys(statsFrom(origin)) : state.data.destinations[origin] || [];
}
function originsTo(target) {
  return state.data.origins.filter(origin => origin !== target && destinationsFrom(origin).includes(target));
}

// --- map -----------------------------------------------------------------------------
const map = L.map('map', {zoomSnap: 0.25, scrollWheelZoom: false}).setView([50.75, 4.5], 8);
const dark = window.matchMedia('(prefers-color-scheme: dark)');
// Standard OpenStreetMap tiles, muted to grey in map.css so the coloured trips stand out.
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 13,
  attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
}).addTo(map);
dark.addEventListener?.('change', () => { renderLegend(); draw(); });

// Fallback when no road path is stored: a gentle curve, so nearby lines don't overlap.
function arc(a, b) {
  const mid = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2], dx = b[1] - a[1], dy = b[0] - a[0];
  const control = [mid[0] + dx * 0.12, mid[1] - dy * 0.12];
  return Array.from({length: 25}, (_, i) => {
    const t = i / 24;
    return [(1 - t) ** 2 * a[0] + 2 * (1 - t) * t * control[0] + t * t * b[0],
            (1 - t) ** 2 * a[1] + 2 * (1 - t) * t * control[1] + t * t * b[1]];
  });
}
// Road paths from OpenStreetMap routing, stored per town pair ("a|b", sorted) as encoded polylines.
function decode(encoded) {
  const points = []; let index = 0, lat = 0, lon = 0;
  while (index < encoded.length) {
    for (const axis of [0, 1]) {
      let shift = 0, result = 0, byte;
      do { byte = encoded.charCodeAt(index++) - 63; result |= (byte & 31) << shift; shift += 5; } while (byte >= 32);
      const delta = result & 1 ? ~(result >> 1) : result >> 1;
      if (axis === 0) lat += delta; else lon += delta;
    }
    points.push([lat / 1e5, lon / 1e5]);
  }
  return points;
}
function road(from, to) {
  const [a, b] = [from, to].sort(), encoded = state.roads[`${a}|${b}`];
  if (!encoded) return arc(place(from).point, place(to).point);
  const points = decode(encoded);
  return a === from ? points : points.reverse();
}

// One trip on the map: road line, a wide invisible hover target, and a dot at the far end.
function addTrip(from, to, dotAt, color, faint, tip, onClick) {
  const path = road(from, to);
  const line = L.polyline(path, {color, weight: 2.5, opacity: faint ? 0.45 : 0.85, dashArray: faint ? '4 6' : null, interactive: false});
  const hit = L.polyline(path, {opacity: 0, weight: 14}).bindTooltip(tip, {sticky: true, className: 'trip'});
  const dot = L.circleMarker(place(dotAt).point, {radius: 8, color: css('--ring'), weight: 2, fillColor: color, fillOpacity: 1})
    .bindTooltip(tip, {className: 'trip', direction: 'top', offset: [0, -6]});
  if (onClick) dot.on('click', onClick);
  hit.on('mouseover', () => line.setStyle({weight: 4.5}));
  hit.on('mouseout', () => line.setStyle({weight: 2.5}));
  state.layers.push(line.addTo(map), hit.addTo(map), dot.addTo(map));
}
function addAnchor(key, label) {
  const marker = L.circleMarker(place(key).point, {radius: 10, color: css('--ring'), weight: 3, fillColor: css('--you'), fillOpacity: 1})
    .bindTooltip(label, {permanent: true, direction: 'right', offset: [10, 0], className: 'trip'});
  state.layers.push(marker.addTo(map));
}
const plural = n => `${n} measurement${n === 1 ? '' : 's'} at this time`;
const farthestFirst = (anchor, keys) => keys.slice().sort((a, b) => km(place(anchor).point, place(b).point) - km(place(anchor).point, place(a).point));

function draw() {
  state.layers.forEach(layer => layer.remove());
  state.layers = [];
  const bucket = state.data.buckets[state.bucket];
  if (state.mode === 'from') {
    const stats = statsFrom(state.origin), rows = [];
    const shown = state.target ? [state.target] : destinationsFrom(state.origin);
    for (const key of farthestFirst(state.origin, shown)) {  // nearer trips end up on top
      const entry = stats[key]?.[bucket], level = levelOf(entry), measured = entry ? entry[0] : 0;
      const tip = `<strong>${place(state.origin).name} → ${place(key).name}</strong><br>Leaving ${bucket}: <strong>${level.label}</strong><br>${plural(measured)}`;
      addTrip(state.origin, key, key, css(LEVEL_VAR[level.id]), level.id === 'none', tip, () => setTarget(key));
      rows.push({cells: [place(key).name, {swatch: LEVEL_VAR[level.id], text: level.label}, measured], sort: [level.id, place(key).name]});
    }
    addAnchor(state.origin, `Start: ${place(state.origin).name}`);
    const order = [...state.data.levels.map(level => level.id), 'none'];
    rows.sort((a, b) => order.indexOf(a.sort[0]) - order.indexOf(b.sort[0]) || a.sort[1].localeCompare(b.sort[1]));
    renderList(['Destination', 'Usually', 'Measurements at this time'], rows,
               `From ${place(state.origin).name}, leaving ${bucket}`, rows.filter(r => r.sort[0] !== 'none').length);
  } else {
    const rows = [];
    for (const key of farthestFirst(state.arrive, originsTo(state.arrive))) {
      const entry = statsFrom(key)[state.arrive]?.[bucket], band = bandOf(entry), level = levelOf(entry);
      const measured = entry ? entry[0] : 0;
      const usual = band.none ? band.label : `${aboutMinutes(entry[2])} (${level.label.toLowerCase()})`;
      const tip = `<strong>${place(key).name} → ${place(state.arrive).name}</strong><br>Leaving ${bucket}: <strong>${usual}</strong><br>${plural(measured)}`;
      addTrip(key, state.arrive, key, css(band.color), band.none, tip, () => { setMode('from'); setOrigin(key, state.arrive); });
      rows.push({cells: [place(key).name, {swatch: band.color, text: band.none ? band.label : aboutMinutes(entry[2])},
                         band.none ? '—' : level.label, measured], sort: band.none ? Infinity : entry[2], name: place(key).name});
    }
    addAnchor(state.arrive, `Destination: ${place(state.arrive).name}`);
    rows.sort((a, b) => a.sort - b.sort || a.name.localeCompare(b.name));
    renderList(['Start from', 'Usually takes', 'How busy', 'Measurements at this time'], rows,
               `To ${place(state.arrive).name}, leaving ${bucket}`, rows.filter(r => r.sort !== Infinity).length);
  }
  renderStrip();
}

// From-view with one destination chosen: the whole day for that trip, one cell per half hour.
function renderStrip() {
  const strip = $('strip'), cells = $('strip-cells');
  strip.hidden = state.mode !== 'from' || !state.target;
  if (strip.hidden) return;
  cells.replaceChildren();
  const stats = statsFrom(state.origin)[state.target] || {};
  const levels = state.data.buckets.map(bucket => levelOf(stats[bucket]));
  state.data.buckets.forEach((bucket, index) => {
    const cell = document.createElement('button');
    cell.type = 'button';
    cell.style.background = css(LEVEL_VAR[levels[index].id]);
    cell.title = `${bucket}: ${levels[index].label}`;
    cell.setAttribute('aria-label', `${bucket}, ${levels[index].label}`);
    if (index === state.bucket) cell.setAttribute('aria-current', 'true');
    cell.addEventListener('click', () => setBucket(index));
    cells.append(cell);
  });
  const order = state.data.levels.map(level => level.id);
  const judged = levels.map((level, index) => ({rank: order.indexOf(level.id), index})).filter(item => item.rank >= 0);
  const name = `${place(state.origin).name} → ${place(state.target).name}`;
  if (!judged.length) { $('strip-note').textContent = `${name}: not enough data yet at any time of day.`; return; }
  const best = Math.min(...judged.map(item => item.rank));
  const calmest = judged.filter(item => item.rank === best).map(item => state.data.buckets[item.index]);
  $('strip-note').textContent = `${name}: calmest when leaving at ${calmest.slice(0, 6).join(', ')}` +
    `${calmest.length > 6 ? ' …' : ''} (${state.data.levels[best].label.toLowerCase()}).`;
}

function renderList(headers, rows, meta, judged) {
  $('list-head').replaceChildren(...headers.map(text => Object.assign(document.createElement('th'), {textContent: text})));
  const body = $('list'); body.replaceChildren();
  for (const row of rows) {
    const tr = document.createElement('tr');
    for (const cell of row.cells) {
      const td = document.createElement('td');
      if (cell && typeof cell === 'object') {
        const span = document.createElement('span'), swatch = document.createElement('i');
        span.className = 'level'; swatch.className = 'swatch'; swatch.style.background = css(cell.swatch);
        span.append(swatch, cell.text); td.append(span);
      } else td.textContent = cell;
      tr.append(td);
    }
    body.append(tr);
  }
  $('list-meta').textContent = `${meta} · ${judged} of ${rows.length} with enough data`;
}

function renderLegend() {
  const legend = $('legend'); legend.replaceChildren();
  const item = (color, text) => {
    const li = document.createElement('li'), swatch = document.createElement('i');
    swatch.className = 'swatch'; swatch.style.background = css(color); li.append(swatch, text); legend.append(li);
  };
  if (state.mode === 'from') {
    const levels = state.data.levels;
    for (const level of levels) item(LEVEL_VAR[level.id], `${level.label} (${level.below === null
      ? `≥ ${Math.round(levels.at(-2).below * 100)}%` : `< ${Math.round(level.below * 100)}%`} slower than an empty road)`);
  } else {
    for (const band of TIME_BANDS) item(band.color, band.label);
  }
  item('--no-data', 'Not enough data yet');
  item('--you', state.mode === 'from' ? 'Your start' : 'Destination');
}

// --- controls --------------------------------------------------------------------------
const option = (value, text) => Object.assign(document.createElement('option'), {value, textContent: text});
const byName = keys => keys.slice().sort((a, b) => place(a).name.localeCompare(place(b).name));

function fit(keys) {
  map.fitBounds(L.latLngBounds(keys.map(key => place(key).point)), {padding: [36, 36], maxZoom: 10});
}
function remember() {
  store.set('travelsmart-origin', state.origin);
  const hash = state.mode === 'from' ? `${state.origin}${state.target ? `/${state.target}` : ''}` : `to/${state.arrive}`;
  history.replaceState(null, '', `${location.search}#${hash}`);
}
function setOrigin(key, target = '') {
  state.origin = key;
  $('origin').value = key;
  state.target = destinationsFrom(key).includes(target) ? target : '';
  const select = $('destination');
  select.replaceChildren(option('', 'All destinations'), ...byName(destinationsFrom(key)).map(k => option(k, place(k).name)));
  select.value = state.target;
  remember(); draw();
  fit([key, ...(state.target ? [state.target] : destinationsFrom(key))]);
}
function setTarget(key) {
  state.target = key;
  $('destination').value = key;
  remember(); draw();
  fit([state.origin, ...(key ? [key] : destinationsFrom(state.origin))]);
}
function setArrive(key) {
  state.arrive = key;
  $('arrive').value = key;
  remember(); draw();
  fit([key, ...originsTo(key)]);
}
function setMode(mode) {
  state.mode = mode;
  $('tab-from').setAttribute('aria-selected', String(mode === 'from'));
  $('tab-to').setAttribute('aria-selected', String(mode === 'to'));
  document.querySelectorAll('[data-mode]').forEach(el => { el.hidden = el.dataset.mode !== mode; });
  $('intro').textContent = mode === 'from'
    ? 'Pick where you start, slide the departure time, and see how busy each trip usually is compared with an empty road.'
    : 'Pick a destination, slide the departure time, and see how long it usually takes to get there from each town.';
  $('list-title').textContent = mode === 'from' ? 'Destinations' : 'Start towns';
  renderLegend();
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
  const roads = await fetch(`data/commutes/geometry.json?v=${encodeURIComponent(state.data.generated_at)}`).catch(() => null);
  state.roads = roads?.ok ? await roads.json() : {};
  $('demo-banner').hidden = !DEMO;
  $('origin').replaceChildren(...state.data.origins.map(key => option(key, place(key).name)));
  const reachable = byName([...new Set(state.data.origins.flatMap(destinationsFrom))]);
  $('arrive').replaceChildren(...reachable.map(key => option(key, place(key).name)));
  const slider = $('time');
  slider.max = state.data.buckets.length - 1;
  $('ticks').replaceChildren(...state.data.buckets.filter(b => b.endsWith(':00') && +b.slice(0, 2) % 2 === 1)
    .map(b => Object.assign(document.createElement('span'), {textContent: b})));
  state.bucket = defaultBucket();
  $('time').value = state.bucket; $('time-label').textContent = state.data.buckets[state.bucket];

  // #diepenbeek, #diepenbeek/brussels or #to/brussels
  const [first, second = ''] = (location.hash.slice(1) || store.get('travelsmart-origin') || 'diepenbeek').split('/').map(decodeURIComponent);
  const startOrigin = state.data.origins.includes(first) ? first : state.data.origins.includes('diepenbeek') ? 'diepenbeek' : state.data.origins[0];
  state.origin = startOrigin;
  state.arrive = reachable.includes(second) ? second : reachable.includes('brussels') ? 'brussels' : reachable[0];
  $('arrive').value = state.arrive;
  if (first === 'to') { setMode('to'); setArrive(state.arrive); }
  else { setMode('from'); setOrigin(startOrigin, second); }

  $('tab-from').addEventListener('click', () => { setMode('from'); setOrigin(state.origin, state.target); });
  $('tab-to').addEventListener('click', () => { setMode('to'); setArrive(state.arrive); });
  $('origin').addEventListener('change', () => setOrigin($('origin').value));
  $('destination').addEventListener('change', () => setTarget($('destination').value));
  $('arrive').addEventListener('change', () => setArrive($('arrive').value));
  slider.addEventListener('input', () => setBucket(+slider.value));
  $('play').addEventListener('click', togglePlay);
}
start().catch(error => { $('map').textContent = error.message; });
