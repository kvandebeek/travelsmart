const $ = id => document.getElementById(id);
const state = {index: null, observations: [], routes: new Map()};
const fmt = new Intl.DateTimeFormat('en-BE', {timeZone:'Europe/Brussels', dateStyle:'medium', timeStyle:'short'});
const num = n => Number(n || 0).toLocaleString('en-BE');
const minutes = seconds => seconds == null ? '—' : `${(seconds / 60).toFixed(1)} min`;
const median = values => { if (!values.length) return null; const a = [...values].sort((x,y)=>x-y); return (a[Math.floor((a.length-1)/2)] + a[Math.floor(a.length/2)]) / 2; };
const slot = row => row.scheduled_at.slice(11,16);
const delay = row => row.freeflow_seconds != null ? row.duration_seconds - row.freeflow_seconds : row.traffic_delay_seconds;
const wet = row => row.weather ? Math.max(row.weather.origin.precipitation || 0, row.weather.destination.precipitation || 0) >= .2 : null;
const eventState = row => { if (!row.traffic_events) return 'unknown'; return Object.values(row.traffic_events.flemish_events_near_route || {}).some(n => n > 0) ? 'near' : 'clear'; };

function option(select, value, label) { const el = document.createElement('option'); el.value = value; el.textContent = label; select.append(el); }
const town = area => state.index.areas?.[area]?.name || area;
// A commute record is a town pair. Every start place in the home town and every
// employment area in the destination town feeds the same record.
const pairOf = route => `${route.home_area}__${route.work_area}`;
const pairLabel = pair => { const [home, work] = pair.split('__'); return `${town(home)} → ${town(work)}`; };
const routeOf = row => state.routes.get(row.route_id);

function fillPairs(select) {
  const groups = new Map();
  for (const pair of new Set(state.index.routes.map(pairOf))) {
    const label = town(pair.split('__')[0]);
    if (!groups.has(label)) groups.set(label, []);
    groups.get(label).push(pair);
  }
  for (const label of [...groups.keys()].sort((a, b) => a.localeCompare(b))) {
    const group = document.createElement('optgroup'); group.label = label;
    groups.get(label).sort((a, b) => pairLabel(a).localeCompare(pairLabel(b))).forEach(pair => option(group, pair, pairLabel(pair)));
    select.append(group);
  }
}

// Optional drill-down within a town pair: one start place and/or one employment area.
function fillPlaces() {
  const routes = state.index.routes.filter(route => pairOf(route) === $('route').value);
  for (const [id, field, all] of [['home-place', 'home_place', 'All start places'], ['work-place', 'work_place', 'All employment areas']]) {
    const select = $(id); select.replaceChildren(); option(select, 'all', all);
    [...new Set(routes.map(route => route[field]))].sort((a, b) => a.localeCompare(b)).forEach(name => option(select, name, name));
  }
}
function setText(id, value) { $(id).textContent = value; }

async function loadMonth() {
  const month = $('month').value;
  if (!month) { state.observations = []; render(); return; }
  const response = await fetch(`data/commutes/${month}.json`);
  if (!response.ok) throw new Error(`Could not load ${month}`);
  state.observations = await response.json();
  const observedPairs = new Set(state.observations.map(routeOf).filter(Boolean).map(pairOf));
  if (!$('route').value || !observedPairs.has($('route').value)) {
    const preferred = 'diepenbeek__brussels';
    $('route').value = observedPairs.has(preferred) ? preferred : [...observedPairs].sort()[0] || preferred;
    fillPlaces();
  }
  render();
}

function renderChart(rows) {
  const container = $('chart');
  container.replaceChildren();
  if (!rows.length) { const p = document.createElement('p'); p.className = 'empty'; p.textContent = 'No successful observations for this selection yet.'; container.append(p); return; }
  const slots = [...new Set(rows.map(slot))].sort();
  const durations = rows.map(row => row.duration_seconds / 60);
  const low = Math.max(0, Math.floor(Math.min(...durations) * .9 / 5) * 5);
  const high = Math.ceil(Math.max(...durations) * 1.1 / 5) * 5 || low + 5;
  const span = Math.max(5, high - low);
  const width = 900, left = 46, right = 16, top = 18, bottom = 35, height = 250;
  const x = i => left + (slots.length === 1 ? (width-left-right)/2 : i * (width-left-right)/(slots.length-1));
  const y = value => top + (high-value)/span * (height-top-bottom);
  const parts = [];
  for (let i=0;i<=4;i++) {
    const value = low + span*i/4;
    parts.push(`<line class="grid" x1="${left}" y1="${y(value)}" x2="${width-right}" y2="${y(value)}"/><text class="axis" x="3" y="${y(value)+4}">${Math.round(value)}m</text>`);
  }
  // With 5-minute extra measurements there can be dozens of slots: label the hours and half hours only.
  const labelled = s => slots.length <= 16 || s.endsWith(':00') || s.endsWith(':30');
  slots.forEach((s,i) => { if (labelled(s)) parts.push(`<text class="axis" text-anchor="middle" x="${x(i)}" y="${height-8}">${s}</text>`); });
  const line = slots.map((s,i) => `${x(i)},${y(median(rows.filter(row => slot(row) === s).map(row => row.duration_seconds/60)))}`).join(' ');
  parts.push(`<polyline class="median" points="${line}"/>`);
  rows.forEach((row,i) => {
    const bucket = wet(row);
    const category = bucket === null ? 'unknown' : bucket ? 'wet' : 'dry';
    const jitter = ((i * 17) % 9) - 4;
    parts.push(`<circle class="${category}" cx="${x(slots.indexOf(slot(row)))+jitter}" cy="${y(row.duration_seconds/60)}" r="3.7"/>`);
  });
  container.innerHTML = `<svg viewBox="0 0 ${width} ${height}" aria-hidden="true">${parts.join('')}</svg>`;
}

function rainComparison(rows) {
  const groups = new Map();
  rows.forEach(row => {
    const d = delay(row), isWet = wet(row);
    if (d == null || isWet == null) return;
    const key = `${row.provider}|${slot(row)}`;
    if (!groups.has(key)) groups.set(key, {wet: [], dry: []});
    groups.get(key)[isWet ? 'wet' : 'dry'].push(d);
  });
  let total = 0, weight = 0, wetSamples = 0, drySamples = 0, matchedSlots = 0;
  for (const group of groups.values()) {
    if (group.wet.length < 3 || group.dry.length < 3) continue;
    const w = Math.min(group.wet.length, group.dry.length);
    total += (median(group.wet) - median(group.dry)) * w;
    weight += w; wetSamples += group.wet.length; drySamples += group.dry.length; matchedSlots++;
  }
  if (!weight) return 'Not enough matched wet and dry observations yet. Each departure slot needs at least three of each.';
  const difference = total / weight;
  const direction = difference >= 0 ? 'longer' : 'shorter';
  return `Wet journeys were ${minutes(Math.abs(difference))} ${direction} in median traffic delay across ${matchedSlots} matched departure slots (${wetSamples} wet, ${drySamples} dry observations).`;
}

function renderTable(rows) {
  const body = $('recent'); body.replaceChildren();
  for (const row of [...rows].sort((a,b)=>b.observed_at.localeCompare(a.observed_at)).slice(0,30)) {
    const tr = document.createElement('tr');
    const school = row.calendar ? `${row.calendar.flanders_school_day ? 'FL school' : 'FL break'} / ${row.calendar.fwb_school_day == null ? 'FWB ?' : row.calendar.fwb_school_day ? 'FWB school' : 'FWB break'}` : '—';
    const route = routeOf(row), journey = row.direction === 'morning'
      ? `${route?.home_place} → ${route?.work_place}` : `${route?.work_place} → ${route?.home_place}`;
    const cells = [fmt.format(new Date(row.observed_at)), journey, slot(row), minutes(row.duration_seconds), minutes(delay(row)),
      wet(row) === null ? 'Pending' : wet(row) ? 'Rain' : 'Dry', school, row.calendar?.origin_light || '—',
      eventState(row), row.provider === 'here' ? 'HERE' : 'TomTom', row.status];
    for (const value of cells) { const td = document.createElement('td'); td.textContent = value; tr.append(td); }
    body.append(tr);
  }
  if (!body.children.length) { const tr = document.createElement('tr'), td = document.createElement('td'); td.colSpan = 11; td.textContent = 'No observations yet.'; tr.append(td); body.append(tr); }
}

function render() {
  const all = state.observations, pair = $('route').value, direction = $('direction').value, provider = $('provider').value;
  const homePlace = $('home-place').value, workPlace = $('work-place').value;
  const pairRoutes = state.index.routes.filter(route => pairOf(route) === pair);
  const rain = $('rain').value, school = $('school').value;
  const light = $('light').value, events = $('events').value, weekday = $('weekday').value;
  const base = all.filter(row => { const route = routeOf(row);
    return route && pairOf(route) === pair && row.direction === direction &&
    (homePlace === 'all' || route.home_place === homePlace) && (workPlace === 'all' || route.work_place === workPlace) &&
    (provider === 'all' || row.provider === provider) &&
    (weekday === 'all' || String(row.calendar?.weekday) === weekday) &&
    (light === 'all' || row.calendar?.origin_light === light) &&
    (events === 'all' || eventState(row) === events) &&
    (school === 'all' || (school === 'flanders_school' && row.calendar?.flanders_school_day === true) ||
      (school === 'flanders_break' && row.calendar?.flanders_school_day === false) ||
      (school === 'fwb_school' && row.calendar?.fwb_school_day === true) ||
      (school === 'fwb_break' && row.calendar?.fwb_school_day === false)); });
  const selected = base.filter(row => rain === 'all' || (rain === 'wet' && wet(row) === true) ||
    (rain === 'dry' && wet(row) === false) || (rain === 'unknown' && wet(row) === null));
  const good = selected.filter(row => row.status === 'ok');
  setText('calls', num(all.length)); setText('success', num(all.filter(row => row.status === 'ok').length));
  setText('covered', num(new Set(all.filter(row => row.status === 'ok').map(routeOf).filter(Boolean).map(pairOf)).size));
  setText('weather-count', num(all.filter(row => row.weather).length));
  const usage = $('account-usage'); usage.replaceChildren();
  const used = state.index.months.find(item => item.month === $('month').value)?.allowance_used_percent || {};
  for (const [id,label] of [['tomtom','TomTom'],['here','HERE']]) {
    const share = used[id] ?? 0;
    const card = document.createElement('div'), name = document.createElement('small'), value = document.createElement('strong');
    const bar = document.createElement('span'); bar.className = 'usage-bar'; bar.style.setProperty('--used', `${Math.min(share, 100)}%`);
    name.textContent = label; value.textContent = `${share.toLocaleString('en-BE', {maximumFractionDigits: 1})}%`;
    card.append(name,value,bar); usage.append(card);
  }
  const starts = new Set(pairRoutes.map(route => route.home_place)), ends = new Set(pairRoutes.map(route => route.work_place));
  setText('route-title', pairRoutes.length ? pairLabel(pair) : 'Choose a commute');
  setText('route-meta', pairRoutes.length ? `${starts.size} start place${starts.size === 1 ? '' : 's'} (${[...starts].sort().join(', ')}) · ` +
    `${ends.size} employment area${ends.size === 1 ? '' : 's'} (${[...ends].sort().join(', ')}) · ` +
    `${direction === 'morning' ? 'home → work' : 'work → home'}` : '');
  setText('sample-count', `${good.length} samples`);
  setText('median-time', minutes(median(good.map(row => row.duration_seconds))));
  setText('median-delay', minutes(median(good.map(delay).filter(value => value != null))));
  setText('range', good.length ? `${minutes(Math.min(...good.map(row => row.duration_seconds)))}–${minutes(Math.max(...good.map(row => row.duration_seconds)))}` : '—');
  setText('weather-result', rainComparison(base.filter(row => row.status === 'ok')));
  renderChart(good); renderTable(selected);
}

async function start() {
  try {
    const response = await fetch('data/commutes/index.json');
    if (!response.ok) throw new Error('Dashboard data has not been exported yet.');
    state.index = await response.json();
    state.index.months.slice().reverse().forEach(item => option($('month'), item.month, item.month));
    state.routes = new Map(state.index.routes.map(route => [route.id, route]));
    fillPairs($('route')); fillPlaces();
    $('route').addEventListener('change', () => { fillPlaces(); render(); });
    ['home-place','work-place','direction','provider','rain','school','light','events','weekday'].forEach(id => $(id).addEventListener('change', render));
    $('month').addEventListener('change', () => loadMonth().catch(showError));
    await loadMonth();
  } catch (error) { showError(error); }
}
function showError(error) { $('chart').textContent = error.message; }
start();
