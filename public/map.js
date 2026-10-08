// "When is it calm?" — two views on the same data, both with a continuous departure-time slider:
//   from: pick a start town; every destination is coloured by how busy the trip usually is
//         (travel time vs an empty road). Optionally narrow it to one destination.
//   to:   pick a destination; every start town is coloured by how long the trip usually takes.
//   corridor: pick a Google Maps corridor and direction; each stretch, in driving order, is coloured
//         by how it compares with its own usual time (on the map and as a strip).
// Time is continuous: between two measured half hours values are blended, and colours flow
// along a gradient (mixed in OKLab, so in-between shades stay clean). Lines follow
// OpenStreetMap road paths where available. ?demo shows made-up data.
const $ = id => document.getElementById(id);
const DEMO = new URLSearchParams(location.search).has('demo');
const MINUTES_PER_BUCKET = 30;
const state = {data: null, mode: 'from', origin: null, target: '', arrive: null, corridor: null, direction: 'forward',
               t: 0, trips: [], anchor: null, coverage: null, playing: false, tween: null, roads: {}, demo: {}};

const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const place = key => state.data.places[key];
const store = {
  get(key) { try { return localStorage.getItem(key); } catch { return null; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch { /* private mode */ } },
};

// --- colour: continuous scales mixed in OKLab --------------------------------------------
const toLinear = c => c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
const toGamma = c => c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055;
function oklab(hex) {
  const n = parseInt(hex.replace('#', ''), 16);
  const [r, g, b] = [n >> 16 & 255, n >> 8 & 255, n & 255].map(v => toLinear(v / 255));
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  return [0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s, 1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
          0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s];
}
function hex([L, a, b]) {
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3, m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3,
        s = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3;
  return '#' + [4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s, -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
                -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s]
    .map(v => Math.round(Math.min(1, Math.max(0, toGamma(v))) * 255).toString(16).padStart(2, '0')).join('');
}
// A scale is a list of [value, css-variable] stops; values in between are mixed.
// Resolved colours are cached per scale and cleared when light/dark mode changes.
const resolvedStops = new Map();
function scaleColor(stops, value) {
  if (!resolvedStops.has(stops)) resolvedStops.set(stops, stops.map(([at, name]) => [at, oklab(css(name))]));
  const resolved = resolvedStops.get(stops);
  if (value <= resolved[0][0]) return hex(resolved[0][1]);
  for (let i = 1; i < resolved.length; i++) {
    const [a, ca] = resolved[i - 1], [b, cb] = resolved[i];
    if (value <= b) { const f = (value - a) / (b - a); return hex(ca.map((v, k) => v + (cb[k] - v) * f)); }
  }
  return hex(resolved.at(-1)[1]);
}
// Busy: congestion share. Stops sit in the middle of each level, so colours match the labels.
const BUSY_STOPS = [[0.05, '--calm'], [0.175, '--moderate'], [0.35, '--busy'], [0.55, '--very-busy']];
// Corridor stretches: share above (+) or below (-) the stretch's own usual time.
const USUAL_STOPS = [[-0.12, '--quieter'], [0.025, '--calm'], [0.2, '--busy'], [0.45, '--very-busy']];
// Travel time in minutes.
const TIME_STOPS = [[10, '--t1'], [30, '--t2'], [50, '--t3'], [75, '--t4'], [105, '--t5']];

// --- data at a continuous time ------------------------------------------------------------
const enough = entry => entry && entry[0] >= state.data.min_samples;
// Blend the two neighbouring half hours; if only one has enough data, use the nearer one.
function entryAt(stats, t) {
  const buckets = state.data.buckets, i = Math.min(Math.floor(t), buckets.length - 1), f = t - i;
  const a = stats?.[buckets[i]], b = stats?.[buckets[i + 1]];
  if (f < 1e-6 || !b) return enough(a) ? a : null;
  if (enough(a) && enough(b)) return [Math.round(f < 0.5 ? a[0] : b[0]), a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
  const nearer = f < 0.5 ? a : b;
  return enough(nearer) ? nearer : null;
}
function levelOf(entry) {
  if (!entry) return {id: 'none', label: 'Not enough data yet'};
  const levels = state.mode === 'corridor' ? state.data.corridors.levels : state.data.levels;
  return levels.find(level => level.below === null || entry[1] < level.below);
}
let noData = null;
const grey = () => (noData ||= css('--no-data'));
const busyColor = entry => entry ? scaleColor(BUSY_STOPS, entry[1]) : grey();
const timeColor = entry => entry ? scaleColor(TIME_STOPS, entry[2]) : grey();
const usualColor = entry => entry ? scaleColor(USUAL_STOPS, entry[1]) : grey();
function vsUsual(entry, usual) {
  const percent = Math.round(entry[1] * 100), minutes = Math.round(entry[2] - usual);
  if (Math.abs(percent) < 3) return 'as usual';
  return `${Math.abs(percent)}% ${percent > 0 ? 'slower' : 'quicker'} than usual (${minutes >= 0 ? '+' : '−'}${Math.abs(minutes)} min)`;
}
const aboutMinutes = minutes => `about ${Math.max(5, Math.round(minutes / 5) * 5)} min`;
function clock(t) {
  const minutes = Math.round(t * MINUTES_PER_BUCKET) + +state.data.buckets[0].slice(0, 2) * 60 + +state.data.buckets[0].slice(3);
  return `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`;
}

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
      stats[key][bucket] = [3 + Math.floor(random() * 10), share, Math.round(freeFlow * (1 + share))];
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
const corridors = () => state.data.corridors?.corridors || [];
const corridorPoint = key => state.data.corridors.points[key];
const currentCorridor = () => corridors().find(corridor => corridor.id === state.corridor);
const currentLegs = () => currentCorridor().directions[state.direction];

// --- map -----------------------------------------------------------------------------
const map = L.map('map', {zoomSnap: 0.25, scrollWheelZoom: false}).setView([50.75, 4.5], 8);
const dark = window.matchMedia('(prefers-color-scheme: dark)');
// Standard OpenStreetMap tiles, muted to grey in map.css so the coloured trips stand out.
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 13,
  attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
}).addTo(map);
dark.addEventListener?.('change', () => { resolvedStops.clear(); noData = null; renderLegend(); build(); });

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
const farthestFirst = (anchor, keys) => keys.slice().sort((a, b) => km(place(anchor).point, place(b).point) - km(place(anchor).point, place(a).point));

// Build the trips once per selection; sliding the time only recolours them.
function build() {
  state.trips.forEach(trip => [trip.line, trip.hit, trip.dot].forEach(layer => layer.remove()));
  state.anchor?.remove();
  state.trips = [];
  if (state.mode === 'corridor') buildCorridor(); else buildTrips();
  renderCoverage();
  recolor();
}
function buildTrips() {
  const pairs = state.mode === 'from'
    ? farthestFirst(state.origin, state.target ? [state.target] : destinationsFrom(state.origin)).map(key => [state.origin, key, key])
    : farthestFirst(state.arrive, originsTo(state.arrive)).map(key => [key, state.arrive, key]);
  for (const [from, to, other] of pairs) {   // nearer trips are drawn last, so they stay on top
    const path = road(from, to);
    const line = L.polyline(path, {weight: 2.5, opacity: 0.85, interactive: false}).addTo(map);
    const hit = L.polyline(path, {opacity: 0, weight: 14}).bindTooltip('', {sticky: true, className: 'trip'}).addTo(map);
    const dot = L.circleMarker(place(other).point, {radius: 8, color: css('--ring'), weight: 2, fillOpacity: 1})
      .bindTooltip('', {className: 'trip', direction: 'top', offset: [0, -6]}).addTo(map);
    dot.on('click', () => state.mode === 'from' ? setTarget(other) : (setMode('from'), setOrigin(other, state.arrive)));
    hit.on('mouseover', () => line.setStyle({weight: 4.5}));
    hit.on('mouseout', () => line.setStyle({weight: 2.5}));
    state.trips.push({from, to, other, line, hit, dot, stats: statsFrom(from)[to], fromName: place(from).name, toName: place(to).name});
  }
  const anchorKey = state.mode === 'from' ? state.origin : state.arrive;
  state.anchor = L.circleMarker(place(anchorKey).point, {radius: 10, color: css('--ring'), weight: 3, fillColor: css('--you'), fillOpacity: 1})
    .bindTooltip(`${state.mode === 'from' ? 'Start' : 'Destination'}: ${place(anchorKey).name}`,
                 {permanent: true, direction: 'right', offset: [10, 0], className: 'trip'}).addTo(map);
}
// A corridor: its stretches as a chain of straight segments, plus one strip segment each.
function buildCorridor() {
  const legs = currentLegs(), known = legs.filter(leg => leg.usual).map(leg => leg.usual);
  const fallback = known.length ? known.reduce((a, b) => a + b) / known.length : 1;  // width for stretches without data
  const segments = legs.map(() => document.createElement('span'));
  $('corridor-bar').replaceChildren(...segments);
  legs.forEach((leg, index) => {
    const from = corridorPoint(leg.from), to = corridorPoint(leg.to), path = [from.point, to.point];
    const line = L.polyline(path, {weight: 5, opacity: 0.9, interactive: false}).addTo(map);
    const hit = L.polyline(path, {opacity: 0, weight: 16}).bindTooltip('', {sticky: true, className: 'trip'}).addTo(map);
    const dot = L.circleMarker(to.point, {radius: 5, color: css('--ring'), weight: 2, fillColor: css('--you'), fillOpacity: 1})
      .bindTooltip(to.name, {className: 'trip', direction: 'top', offset: [0, -4]}).addTo(map);
    hit.on('mouseover', () => line.setStyle({weight: 8}));
    hit.on('mouseout', () => line.setStyle({weight: 5}));
    segments[index].style.flexGrow = leg.usual || fallback;
    state.trips.push({from: leg.from, to: leg.to, line, hit, dot, stats: leg.stats, usual: leg.usual, corridor: true,
                      fromName: from.name, toName: to.name, segment: segments[index]});
  });
  const first = corridorPoint(legs[0].from);
  $('corridor-start').textContent = first.name;
  $('corridor-end').textContent = corridorPoint(legs.at(-1).to).name;
  state.anchor = L.circleMarker(first.point, {radius: 9, color: css('--ring'), weight: 3, fillColor: css('--you'), fillOpacity: 1})
    .bindTooltip(`Start: ${first.name}`, {permanent: true, direction: 'right', offset: [10, 0], className: 'trip'}).addTo(map);
}

function recolor() {
  const time = clock(state.t), rows = [];
  $('time-label').textContent = time;
  $('time').value = state.t;
  for (const trip of state.trips) {
    const entry = entryAt(trip.stats, state.t), level = levelOf(entry);
    const color = state.mode === 'from' ? busyColor(entry) : state.mode === 'to' ? timeColor(entry) : usualColor(entry);
    const usual = !entry ? level.label : state.mode === 'from' ? level.label
      : state.mode === 'to' ? `${aboutMinutes(entry[2])} (${level.label.toLowerCase()})` : vsUsual(entry, trip.usual);
    const measured = entry ? entry[0] : 0;
    const tip = `<strong>${trip.fromName} → ${trip.toName}</strong><br>Leaving ${time}: <strong>${usual}</strong><br>` +
      (entry ? `based on ${measured}+ measurements around this time`
        : trip.corridor && !trip.usual ? 'no usual time yet: needs measurements in 4 or more half hours'
        : 'fewer than 3 measurements around this time');
    trip.line.setStyle({color, opacity: entry ? 0.85 : 0.45, dashArray: entry ? null : '4 6'});
    trip.hit.setTooltipContent(tip);
    if (trip.corridor) {
      trip.segment.style.background = color;
      trip.segment.title = `${trip.fromName} → ${trip.toName}: ${usual}`;
    } else {
      trip.dot.setStyle({fillColor: color});
      trip.dot.setTooltipContent(tip);
    }
    rows.push({trip, entry, level, color});
  }
  renderList(rows, time);
  renderStrip();
  renderCorridorNote(rows);
  renderCoverageNote();
}

// --- data coverage along the timeline -------------------------------------------------------
// Per half hour, for the trips currently on the map: how many have enough data, and how many
// measurements there are in total. Uses the measured half hours themselves, not blended values.
function coverage() {
  return state.data.buckets.map(bucket => {
    let judged = 0, measured = 0, measurements = 0;
    for (const trip of state.trips) {
      const entry = trip.stats?.[bucket];
      if (!entry) continue;
      measured += 1;
      measurements += entry[0];
      if (entry[0] >= state.data.min_samples) judged += 1;
    }
    return {bucket, judged, measured, measurements, trips: state.trips.length};
  });
}
// Paint the slider track: one stop per half hour, aligned with the thumb's travel range.
function renderCoverage() {
  const bars = coverage(), last = bars.length - 1;
  state.coverage = bars;
  const stops = bars.map((bar, index) => {
    const perTrip = bar.measured ? bar.measurements / bar.measured : 0;
    const strength = !bar.measurements ? 0 : bar.judged < bar.measured / 2 ? 0.3 : perTrip >= 10 ? 1 : 0.65;
    const alpha = (strength * (0.35 + 0.65 * (bar.trips ? bar.measured / bar.trips : 0))).toFixed(2);
    return `rgb(var(--data-rgb) / ${alpha}) calc(10px + (100% - 20px) * ${(index / last).toFixed(4)})`;
  });
  $('time').style.setProperty('--track', `linear-gradient(to right, ${stops.join(', ')}), var(--line)`);
}
function renderCoverageNote() {
  if (!state.coverage) return;
  const bar = state.coverage[Math.round(state.t)];
  const of = `${bar.measured} of ${bar.trips} ${bar.trips === 1 ? 'trip' : 'trips'} measured`;
  const time = clock(state.t);  // the nearest measured half hour describes the slider's time
  $('coverage-note').textContent = !bar.measurements ? `${time} · no data yet`
    : `${time} · ${of} · ${bar.judged ? `${bar.judged} with enough to judge` : 'none with enough yet (3+ needed)'}`;
}

// From-view with one destination chosen: the whole day for that trip as one gradient bar.
function renderStrip() {
  const strip = $('strip');
  strip.hidden = state.mode !== 'from' || !state.target;
  if (strip.hidden) return;
  const stats = statsFrom(state.origin)[state.target], last = state.data.buckets.length - 1, stops = [];
  for (let step = 0; step <= last * 6; step++) {           // every 5 minutes
    const t = step / 6, entry = entryAt(stats, t);
    stops.push(`${busyColor(entry)} ${(t / last * 100).toFixed(2)}%`);
  }
  $('strip-bar').style.background = `linear-gradient(to right, ${stops.join(', ')})`;
  $('strip-marker').style.left = `${state.t / last * 100}%`;
  // The calmest stretch of the day, in 5-minute steps.
  let best = Infinity;
  const samples = Array.from({length: last * 6 + 1}, (_, step) => entryAt(stats, step / 6));
  samples.forEach(entry => { if (entry) best = Math.min(best, entry[1]); });
  const name = `${place(state.origin).name} → ${place(state.target).name}`;
  if (best === Infinity) { $('strip-note').textContent = `${name}: not enough data yet at any time of day.`; return; }
  const calm = samples.map((entry, step) => entry && entry[1] <= best + 0.03 ? step : null).filter(step => step !== null);
  const windows = [];
  for (const step of calm) {
    if (windows.length && step === windows.at(-1)[1] + 1) windows.at(-1)[1] = step; else windows.push([step, step]);
  }
  const text = windows.slice(0, 3).map(([a, b]) => a === b ? clock(a / 6) : `${clock(a / 6)}–${clock(b / 6)}`).join(', ');
  $('strip-note').textContent = `${name}: calmest when leaving around ${text}${windows.length > 3 ? ' …' : ''} ` +
    `(${levelOf([1, best]).label.toLowerCase()}). Click the bar to jump there.`;
}

// Corridor view: name the stretch that stands out most at the chosen time.
function renderCorridorNote(rows) {
  if (state.mode !== 'corridor') return;
  const judged = rows.filter(row => row.entry);
  if (!judged.length) { $('corridor-note').textContent = 'Not enough data yet for any stretch at this time.'; return; }
  const worst = judged.reduce((a, b) => b.entry[1] > a.entry[1] ? b : a);
  $('corridor-note').textContent = worst.entry[1] < 0.10
    ? `Every measured stretch is about as usual or quicker at this time (${judged.length} of ${rows.length} measured).`
    : `Slowest compared with usual: ${worst.trip.fromName} → ${worst.trip.toName}, ${vsUsual(worst.entry, worst.trip.usual)}.`;
}

function renderList(rows, time) {
  const headers = state.mode === 'from' ? ['Destination', 'Usually', 'Measurements around this time']
    : state.mode === 'to' ? ['Start from', 'Usually takes', 'How busy', 'Measurements around this time']
    : ['Stretch', 'Compared with usual', 'Usually takes', 'Measurements around this time'];
  $('list-head').replaceChildren(...headers.map(text => Object.assign(document.createElement('th'), {textContent: text})));
  if (state.mode !== 'corridor') rows.sort(state.mode === 'from'
    ? (a, b) => (a.entry ? a.entry[1] : Infinity) - (b.entry ? b.entry[1] : Infinity) || place(a.trip.other).name.localeCompare(place(b.trip.other).name)
    : (a, b) => (a.entry ? a.entry[2] : Infinity) - (b.entry ? b.entry[2] : Infinity) || place(a.trip.other).name.localeCompare(place(b.trip.other).name));
  const body = $('list'); body.replaceChildren();
  for (const row of rows) {
    const cells = state.mode === 'from'
      ? [place(row.trip.other).name, {color: row.color, text: row.level.label}, row.entry ? `${row.entry[0]}+` : '0–2']
      : state.mode === 'to'
      ? [place(row.trip.other).name, {color: row.color, text: row.entry ? aboutMinutes(row.entry[2]) : row.level.label},
         row.entry ? row.level.label : '—', row.entry ? `${row.entry[0]}+` : '0–2']
      : [`${row.trip.fromName} → ${row.trip.toName}`, {color: row.color, text: row.entry ? vsUsual(row.entry, row.trip.usual) : row.level.label},
         row.trip.usual ? aboutMinutes(row.trip.usual) : '—', row.entry ? `${row.entry[0]}+` : '0–2'];
    const tr = document.createElement('tr');
    for (const cell of cells) {
      const td = document.createElement('td');
      if (cell && typeof cell === 'object') {
        const span = document.createElement('span'), swatch = document.createElement('i');
        span.className = 'level'; swatch.className = 'swatch'; swatch.style.background = cell.color;
        span.append(swatch, cell.text); td.append(span);
      } else td.textContent = cell;
      tr.append(td);
    }
    body.append(tr);
  }
  const judged = rows.filter(row => row.entry).length;
  const anchor = state.mode === 'corridor' ? `Towards ${corridorPoint(currentLegs().at(-1).to).name}`
    : `${state.mode === 'from' ? 'From' : 'To'} ${place(state.mode === 'from' ? state.origin : state.arrive).name}`;
  $('list-meta').textContent = `${anchor}, leaving ${time} · ${judged} of ${rows.length} with enough data`;
}

function renderLegend() {
  const legend = $('legend'); legend.replaceChildren();
  const stops = state.mode === 'from' ? BUSY_STOPS : state.mode === 'to' ? TIME_STOPS : USUAL_STOPS;
  const low = stops[0][0], high = stops.at(-1)[0];
  const gradient = Array.from({length: 21}, (_, i) => {
    const value = low + (high - low) * i / 20;
    return `${scaleColor(stops, value)} ${i * 5}%`;
  });
  const scale = document.createElement('div'); scale.className = 'legend-scale';
  const bar = document.createElement('div'); bar.className = 'legend-bar'; bar.style.background = `linear-gradient(to right, ${gradient.join(', ')})`;
  const labels = document.createElement('div'); labels.className = 'legend-labels';
  // Each label sits under its own colour stop, so the words match the gradient.
  const words = state.mode === 'from' ? ['Calm', 'Moderate', 'Busy', 'Very busy']
    : state.mode === 'to' ? ['10 min', '30', '50', '75', '105+ min'] : ['Quieter', 'As usual', 'Slower', 'Much slower'];
  labels.replaceChildren(...stops.map(([at], i) => {
    const span = Object.assign(document.createElement('span'), {textContent: words[i]});
    span.style.left = `${(at - low) / (high - low) * 100}%`;
    span.className = i === 0 ? 'first' : i === stops.length - 1 ? 'last' : '';
    return span;
  }));
  scale.append(bar, labels);
  legend.append(scale);
  const item = (color, text) => {
    const span = document.createElement('span'), swatch = document.createElement('i');
    span.className = 'legend-item'; swatch.className = 'swatch'; swatch.style.background = css(color);
    span.append(swatch, text); legend.append(span);
  };
  item('--no-data', 'Not enough data yet (dashed)');
  item('--you', state.mode === 'from' ? 'Your start' : state.mode === 'to' ? 'Destination' : 'Corridor points');
  if (state.mode !== 'to') {
    const note = document.createElement('span'); note.className = 'legend-item';
    note.textContent = state.mode === 'from' ? 'Busy = how much slower than an empty road'
      : "Compared with each stretch's own usual time";
    legend.append(note);
  }
}

// --- time: continuous slider, gliding jumps and play ------------------------------------
function setTime(t) {
  state.t = Math.max(0, Math.min(state.data.buckets.length - 1, t));
  recolor();
}
function glideTo(target, duration = 450) {
  cancelAnimationFrame(state.tween);
  const start = state.t, began = performance.now();
  const step = now => {
    const f = Math.min(1, (now - began) / duration), eased = f < 0.5 ? 2 * f * f : 1 - (-2 * f + 2) ** 2 / 2;
    setTime(start + (target - start) * eased);
    if (f < 1) state.tween = requestAnimationFrame(step);
  };
  state.tween = requestAnimationFrame(step);
}
function togglePlay() {
  state.playing = !state.playing;
  $('play').textContent = state.playing ? '❚❚ Pause' : '▶ Play the day';
  $('play').setAttribute('aria-pressed', String(state.playing));
  if (!state.playing) return;
  cancelAnimationFrame(state.tween);
  const last = state.data.buckets.length - 1;
  if (state.t >= last) setTime(0);
  let previous = performance.now();
  const step = now => {
    if (!state.playing) return;
    const next = state.t + (now - previous) / 1200;   // one half hour per 1.2 seconds
    previous = now;
    if (next >= last) { setTime(last); togglePlay(); return; }
    setTime(next);
    requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}
function defaultTime() {
  const toT = hhmm => {
    const [h, m] = hhmm.split(':').map(Number), first = state.data.buckets[0].split(':').map(Number);
    return ((h * 60 + m) - (first[0] * 60 + first[1])) / MINUTES_PER_BUCKET;
  };
  const asked = new URLSearchParams(location.search).get('at');      // e.g. ?at=08:00
  if (/^\d\d:\d\d$/.test(asked || '')) return toT(asked);
  const now = new Intl.DateTimeFormat('en-GB', {timeZone: 'Europe/Brussels', hour: '2-digit', minute: '2-digit', hour12: false}).format(new Date());
  const t = toT(now);
  return t >= 0 && t <= state.data.buckets.length - 1 ? t : toT('07:30');
}

// --- controls --------------------------------------------------------------------------
const option = (value, text) => Object.assign(document.createElement('option'), {value, textContent: text});
const byName = keys => keys.slice().sort((a, b) => place(a).name.localeCompare(place(b).name));
function fit(keys) {
  map.fitBounds(L.latLngBounds(keys.map(key => place(key).point)), {padding: [36, 36], maxZoom: 10});
}
function remember() {
  store.set('travelsmart-origin', state.origin);
  const hash = state.mode === 'from' ? `${state.origin}${state.target ? `/${state.target}` : ''}`
    : state.mode === 'to' ? `to/${state.arrive}` : `corridor/${state.corridor}/${state.direction}`;
  history.replaceState(null, '', `${location.search}#${hash}`);
}
function setOrigin(key, target = '') {
  state.origin = key;
  $('origin').value = key;
  state.target = destinationsFrom(key).includes(target) ? target : '';
  $('destination').replaceChildren(option('', 'All destinations'), ...byName(destinationsFrom(key)).map(k => option(k, place(k).name)));
  $('destination').value = state.target;
  remember(); build();
  fit([key, ...(state.target ? [state.target] : destinationsFrom(key))]);
}
function setTarget(key) {
  state.target = key;
  $('destination').value = key;
  remember(); build();
  fit([state.origin, ...(key ? [key] : destinationsFrom(state.origin))]);
}
function setArrive(key) {
  state.arrive = key;
  $('arrive').value = key;
  remember(); build();
  fit([key, ...originsTo(key)]);
}
function setCorridor(id, direction = state.direction) {
  state.corridor = (corridors().find(corridor => corridor.id === id) || corridors()[0]).id;
  state.direction = direction === 'reverse' ? 'reverse' : 'forward';
  $('corridor').value = state.corridor;
  const towards = way => `Towards ${corridorPoint(currentCorridor().directions[way].at(-1).to).name}`;
  $('direction').replaceChildren(option('forward', towards('forward')), option('reverse', towards('reverse')));
  $('direction').value = state.direction;
  remember(); build();
  map.fitBounds(L.latLngBounds(currentLegs().flatMap(leg => [corridorPoint(leg.from).point, corridorPoint(leg.to).point])),
                {padding: [36, 36], maxZoom: 10});
}
function setMode(mode) {
  state.mode = mode;
  for (const tab of ['from', 'to', 'corridor']) $(`tab-${tab}`).setAttribute('aria-selected', String(mode === tab));
  document.querySelectorAll('[data-mode]').forEach(el => { el.hidden = el.dataset.mode !== mode; });
  $('intro').textContent = {
    from: 'Pick where you start, slide the departure time, and see how busy each trip usually is compared with an empty road.',
    to: 'Pick a destination, slide the departure time, and see how long it usually takes to get there from each town.',
    corridor: 'Pick a corridor and direction, slide the departure time, and see which stretches are slower or quicker than usual.',
  }[mode];
  $('list-title').textContent = {from: 'Destinations', to: 'Start towns', corridor: 'Stretches'}[mode];
  renderLegend();
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
  // A label every three hours, under the thumb's centre at that time (same travel range as the track).
  $('ticks').replaceChildren(...state.data.buckets.flatMap((b, index) => b.endsWith(':00') && +b.slice(0, 2) % 3 === 0
    ? [Object.assign(document.createElement('span'), {textContent: b, style: `--at: ${index / slider.max}`})] : []));
  state.t = defaultTime();

  $('tab-corridor').hidden = !corridors().length;
  $('corridor').replaceChildren(...corridors().map(corridor => option(corridor.id, corridor.name)));

  // #diepenbeek, #diepenbeek/brussels, #to/brussels or #corridor/e314/reverse
  const [first, second = '', third = ''] = (location.hash.slice(1) || store.get('travelsmart-origin') || 'diepenbeek').split('/').map(decodeURIComponent);
  state.origin = state.data.origins.includes(first) ? first : state.data.origins.includes('diepenbeek') ? 'diepenbeek' : state.data.origins[0];
  state.arrive = reachable.includes(second) ? second : reachable.includes('brussels') ? 'brussels' : reachable[0];
  $('arrive').value = state.arrive;
  if (first === 'to') { setMode('to'); setArrive(state.arrive); }
  else if (first === 'corridor' && corridors().length) { setMode('corridor'); setCorridor(second, third); }
  else { setMode('from'); setOrigin(state.origin, second); }

  $('tab-from').addEventListener('click', () => { setMode('from'); setOrigin(state.origin, state.target); });
  $('tab-to').addEventListener('click', () => { setMode('to'); setArrive(state.arrive); });
  $('tab-corridor').addEventListener('click', () => { setMode('corridor'); setCorridor(state.corridor); });
  $('corridor').addEventListener('change', () => setCorridor($('corridor').value));
  $('direction').addEventListener('change', () => setCorridor(state.corridor, $('direction').value));
  $('origin').addEventListener('change', () => setOrigin($('origin').value));
  $('destination').addEventListener('change', () => setTarget($('destination').value));
  $('arrive').addEventListener('change', () => setArrive($('arrive').value));
  slider.addEventListener('input', () => { cancelAnimationFrame(state.tween); setTime(+slider.value); });
  $('play').addEventListener('click', togglePlay);
  $('strip-bar').addEventListener('click', event => {
    const box = $('strip-bar').getBoundingClientRect();
    glideTo((event.clientX - box.left) / box.width * (state.data.buckets.length - 1));
  });
}
start().catch(error => { $('map').textContent = error.message; });
