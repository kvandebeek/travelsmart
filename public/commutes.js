const $ = id => document.getElementById(id);
const state = {index: null, observations: []};
const fmt = new Intl.DateTimeFormat('en-BE', {timeZone:'Europe/Brussels', dateStyle:'medium', timeStyle:'short'});
const num = n => Number(n || 0).toLocaleString('en-BE');
const minutes = seconds => seconds == null ? '—' : `${(seconds / 60).toFixed(1)} min`;
const median = values => { if (!values.length) return null; const a = [...values].sort((x,y)=>x-y); return (a[Math.floor((a.length-1)/2)] + a[Math.floor(a.length/2)]) / 2; };
const slot = row => row.scheduled_at.slice(11,16);
const delay = row => row.freeflow_seconds != null ? row.duration_seconds - row.freeflow_seconds : row.traffic_delay_seconds;
const wet = row => row.weather ? Math.max(row.weather.origin.precipitation || 0, row.weather.destination.precipitation || 0) >= .2 : null;
const eventState = row => { if (!row.traffic_events) return 'unknown'; return Object.values(row.traffic_events.flemish_events_near_route || {}).some(n => n > 0) ? 'near' : 'clear'; };

function option(select, value, label) { const el = document.createElement('option'); el.value = value; el.textContent = label; select.append(el); }
function setText(id, value) { $(id).textContent = value; }

async function loadMonth() {
  const month = $('month').value;
  if (!month) { state.observations = []; render(); return; }
  const response = await fetch(`data/commutes/${month}.json`);
  if (!response.ok) throw new Error(`Could not load ${month}`);
  state.observations = await response.json();
  const observedRoutes = new Set(state.observations.map(row => row.route_id));
  if (!$('route').value || !observedRoutes.has($('route').value)) {
    $('route').value = observedRoutes.has('diepenbeek__brussels_european') ? 'diepenbeek__brussels_european' : [...observedRoutes].sort()[0] || '';
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
  slots.forEach((s,i) => parts.push(`<text class="axis" text-anchor="middle" x="${x(i)}" y="${height-8}">${s}</text>`));
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
    const cells = [fmt.format(new Date(row.observed_at)), slot(row), minutes(row.duration_seconds), minutes(delay(row)),
      wet(row) === null ? 'Pending' : wet(row) ? 'Rain' : 'Dry', school, row.calendar?.origin_light || '—',
      eventState(row), row.account || row.provider, row.status];
    for (const value of cells) { const td = document.createElement('td'); td.textContent = value; tr.append(td); }
    body.append(tr);
  }
  if (!body.children.length) { const tr = document.createElement('tr'), td = document.createElement('td'); td.colSpan = 10; td.textContent = 'No observations yet.'; tr.append(td); body.append(tr); }
}

function render() {
  const all = state.observations, routeId = $('route').value, direction = $('direction').value, provider = $('provider').value;
  const route = state.index.routes.find(item => item.id === routeId);
  const account = $('account').value, rain = $('rain').value, school = $('school').value;
  const light = $('light').value, events = $('events').value, weekday = $('weekday').value;
  const base = all.filter(row => row.route_id === routeId && row.direction === direction &&
    (provider === 'all' || row.provider === provider) && (account === 'all' || row.account === account) &&
    (weekday === 'all' || String(row.calendar?.weekday) === weekday) &&
    (light === 'all' || row.calendar?.origin_light === light) &&
    (events === 'all' || eventState(row) === events) &&
    (school === 'all' || (school === 'flanders_school' && row.calendar?.flanders_school_day === true) ||
      (school === 'flanders_break' && row.calendar?.flanders_school_day === false) ||
      (school === 'fwb_school' && row.calendar?.fwb_school_day === true) ||
      (school === 'fwb_break' && row.calendar?.fwb_school_day === false)));
  const selected = base.filter(row => rain === 'all' || (rain === 'wet' && wet(row) === true) ||
    (rain === 'dry' && wet(row) === false) || (rain === 'unknown' && wet(row) === null));
  const good = selected.filter(row => row.status === 'ok');
  setText('calls', num(all.length)); setText('success', num(all.filter(row => row.status === 'ok').length));
  setText('covered', num(new Set(all.filter(row => row.status === 'ok').map(row => row.route_id)).size));
  setText('weather-count', num(all.filter(row => row.weather).length));
  const usage = $('account-usage'); usage.replaceChildren();
  for (const [id,label] of [['tomtom_morning','TomTom morning'],['tomtom_evening','TomTom evening'],['here','HERE']]) {
    const count = all.filter(row => row.account === id).length;
    const budget = state.index.account_budgets?.[id];
    const card = document.createElement('div'), name = document.createElement('small'), value = document.createElement('strong');
    name.textContent = label; value.textContent = budget ? `${num(count)} / ${num(budget.monthly_limit)}` : num(count);
    card.append(name,value); usage.append(card);
  }
  setText('route-title', route ? `${route.home_place} ↔ ${route.work_place}` : 'Choose a route');
  setText('route-meta', route ? `${route.home_area} to ${route.work_area} · ${route.tier} · ${direction === 'morning' ? 'home → work' : 'work → home'}` : '');
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
    state.index.routes.forEach(item => option($('route'), item.id, `${item.home_place} → ${item.work_place}`));
    ['route','direction','provider','account','rain','school','light','events','weekday'].forEach(id => $(id).addEventListener('change', render));
    $('month').addEventListener('change', () => loadMonth().catch(showError));
    await loadMonth();
  } catch (error) { showError(error); }
}
function showError(error) { $('chart').textContent = error.message; }
start();
