const COL_FIRST = 120, COL_CELL = 68; // px: row-header column and each town column
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const $ = selector => document.querySelector(selector);
class MissingData extends Error {}
const getJSON = url => fetch(url).then(r => { if (!r.ok) throw new MissingData(`${url}: HTTP ${r.status}`); return r.json(); });
const esc = text => String(text).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const state = { locations: [], byJourney: {}, reliable: 20, from: null, to: null, scheduleSlots: [], province: "", workHours: 8, tab: "one" };

const toMinutes = slot => { const [h, m] = slot.split(":").map(Number); return h * 60 + m; };
const toSlot = minutes => `${String(Math.floor(minutes / 60) % 24).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
const slotRange = slot => `${slot}–${toSlot(toMinutes(slot) + 15)}`;
const moment = ({ day, slot, end }) => `${DAY_NAMES[day]} ${slot}–${end}`;
const km = meters => meters == null ? "" : `${Math.round(meters / 1000)} km`;
const name = id => state.locations.find(l => l.id === id)?.display_name ?? id;
const measured = item => item && item.samples >= item.sufficient_samples;
const differenceLabel = spread => spread < .05 ? "Small" : spread < .15 ? "Moderate" : "Big";

/* ---------- Matrix: best moment to leave, per pair ---------- */

// How much the choice of moment matters (1 = hardly, 5 = a lot), shown as colour.
const spreadLevel = spread => 1 + [.02, .05, .1, .2].filter(cut => spread > cut).length;

function shownLocations() {
  return state.province ? state.locations.filter(l => l.province === state.province) : state.locations;
}

function renderMatrix() {
  const shown = shownLocations();
  const head = shown.map(l => `<th scope="col" title="${esc(l.display_name)}">${esc(l.display_name)}</th>`).join("");
  const rows = shown.map(from => {
    const cells = shown.map(to => {
      if (from.id === to.id) return '<td class="self" aria-hidden="true"></td>';
      const id = `${from.id}_${to.id}`, item = state.byJourney[id];
      if (!measured(item)) return '<td class="na" title="No data collected yet">n/a</td>';
      if (!item.meaningful) return '<td class="na" title="No clear difference between moments yet">—</td>';
      const pressed = from.id === state.from && to.id === state.to;
      const label = `${from.display_name} to ${to.display_name}, best moment ${moment(item.best)}`;
      return `<td class="h${spreadLevel(item.spread)}"><button class="cell" type="button" data-id="${id}"
        aria-label="${esc(label)}" aria-pressed="${pressed}"
        title="Best ${moment(item.best)} · busiest ${moment(item.worst)}">${DAYS[item.best.day]} ${item.best.slot}</button></td>`;
    }).join("");
    return `<tr><th scope="row" title="${esc(from.display_name)}">${esc(from.display_name)}</th>${cells}</tr>`;
  }).join("");
  $("#matrix").style.minWidth = `${COL_FIRST + shown.length * COL_CELL}px`; // fixed, equal columns; wide sets scroll sideways
  $("#matrix").innerHTML = `<thead><tr><th scope="col">From ↓ / To →</th>${head}</tr></thead><tbody>${rows}</tbody>`;
}

/* ---------- Detail: when is it best to travel? ---------- */

function weekGrid(moments) {
  const slots = [...state.scheduleSlots, ...Object.values(moments.cells).flatMap(day => Object.keys(day))].map(toMinutes);
  const first = Math.min(...slots), last = Math.max(...slots);
  const columns = Array.from({ length: (last - first) / 15 + 1 }, (_, i) => toSlot(first + i * 15));
  const header = columns.map(s => `<span class="hh">${s.endsWith(":00") && Number(s.slice(0, 2)) % 3 === 0 ? s.slice(0, 2) : ""}</span>`).join("");
  const rows = DAYS.map((label, day) => {
    const cells = columns.map(slot => {
      const cell = moments.cells[day]?.[slot];
      if (!cell) return '<span class="mc none"></span>';
      const best = moments.best_by_day[day] === slot;
      const word = cell.level === 1 ? "among the best" : cell.level === 5 ? "among the busiest" : "in between";
      return `<span class="mc h${cell.level}${best ? " top" : ""}" title="${DAY_NAMES[day]} ${slotRange(slot)}: ${word}${best ? " (best of the day)" : ""}"></span>`;
    }).join("");
    return `<span class="dl">${label}</span>${cells}`;
  }).join("");
  return `<div class="mgrid" style="--cols:${columns.length}" role="img"
      aria-label="Week overview. Best moment ${moment(moments.best)}, busiest ${moment(moments.worst)}.">
    <span></span>${header}${rows}</div>
    <div class="legend"><span>best</span><i class="h1"></i><i class="h2"></i><i class="h3"></i><i class="h4"></i><i class="h5"></i><span>busiest</span><span class="ring" title="Best slot of that day"></span><span>best of the day</span></div>`;
}

async function renderDetail() {
  const panel = $("#detail"), id = `${state.from}_${state.to}`;
  if (state.from === state.to) { panel.hidden = true; return; }
  panel.hidden = false;
  const item = state.byJourney[id];
  const title = `<h2>${esc(name(state.from))} → ${esc(name(state.to))}</h2>`;
  if (!measured(item)) {
    panel.innerHTML = `${title}<p class="empty">No data collected for this route yet.</p>`;
    return;
  }
  const preview = item.samples < state.reliable
    ? '<span class="badge" title="Based on few samples; the ranking firms up as samples accumulate.">Preview</span>' : "";
  const meta = `<p class="meta">${km(item.distance_m)} · ${item.samples} sample${item.samples === 1 ? "" : "s"}${preview}</p>`;
  if (!item.meaningful) {
    const why = item.covered_cells >= 2
      ? "All the times measured so far look alike."
      : "Too few days and times measured so far to compare.";
    panel.innerHTML = `${title}${meta}<p class="verdict"><small>Best moment</small><strong>No clear difference yet</strong></p><p class="empty">${why}</p>`;
    return;
  }
  panel.innerHTML = `${title}${meta}
    <div class="tabs" id="tabs" hidden>
      <button type="button" data-tab="one" aria-pressed="true">One way</button>
      <button type="button" data-tab="trip" aria-pressed="false">Round trip</button>
    </div>
    <div id="tab-one">
      <p class="verdict"><small>Best moment to travel</small><strong>${moment(item.best)}</strong></p>
      <p class="verdict busy"><small>Busiest</small>${moment(item.worst)}</p>
      <p class="meta">Difference between them: <b>${differenceLabel(item.spread)}</b></p>
      <div id="week"></div>
    </div>
    <div id="tab-trip" hidden></div>`;
  try {
    const doc = await getJSON(`data/journeys/${id}.json`);
    if (`${state.from}_${state.to}` !== id) return; // selection changed while loading
    $("#week").innerHTML = weekGrid(doc.moments);
    if (doc.round_trips && Object.keys(doc.round_trips).length) {
      $("#tab-trip").innerHTML = tripBlock();
      renderTrip(doc);
      $("#work").addEventListener("change", e => { state.workHours = Number(e.target.value); renderTrip(doc); });
      $("#tabs").hidden = false;
      $("#tabs").addEventListener("click", e => {
        const tab = e.target.closest("button[data-tab]");
        if (!tab) return;
        state.tab = tab.dataset.tab;
        showTab();
      });
      showTab();
    }
  } catch {
    $("#week").innerHTML = '<p class="empty">Week overview unavailable.</p>';
  }
}

function showTab() {
  $("#tab-one").hidden = state.tab !== "one";
  $("#tab-trip").hidden = state.tab !== "trip";
  document.querySelectorAll("#tabs button").forEach(b => b.setAttribute("aria-pressed", b.dataset.tab === state.tab));
}

/* ---------- Round trip: leave A, stay X hours at B, leave B ---------- */

function tripBlock() {
  const hours = Array.from({ length: 12 }, (_, i) => i + 1);
  return `<label class="inline">Time at ${esc(name(state.to))}
      <select id="work" aria-label="Hours spent at the destination">${hours.map(h => `<option value="${h}"${h === state.workHours ? " selected" : ""}>${h} h</option>`).join("")}</select></label>
    <div id="trip"></div>`;
}

function renderTrip(doc) {
  const plan = doc.round_trips?.[String(state.workHours)];
  const from = esc(name(state.from)), to = esc(name(state.to));
  if (!plan?.meaningful) {
    $("#trip").innerHTML = '<p class="empty">No clear best round trip yet.</p>';
    return;
  }
  const leave = t => `Leave ${from} <b>${t.out.slot}–${t.out.end}</b> · leave ${to} <b>${t.back.slot}–${t.back.end}</b>`;
  const days = Object.entries(plan.days).map(([day, t]) =>
    `<div class="trow"><span>${DAYS[day]}</span><span>${t.out.slot} · ${t.back.slot}</span><i class="h${t.level}" title="${DAY_NAMES[day]}: ${t.level === 1 ? "among the best days" : t.level === 5 ? "among the busiest days" : "in between"}"></i></div>`).join("");
  $("#trip").innerHTML = `<p class="verdict"><small>Best round trip</small><strong>${DAY_NAMES[plan.best.day]}</strong><br>${leave(plan.best)}</p>
    <p class="verdict busy"><small>Busiest</small>${DAY_NAMES[plan.worst.day]}: ${leave(plan.worst)}</p>
    <div class="trips" aria-label="Best round trip per weekday: leave times">${days}</div>`;
}

/* ---------- Selection ---------- */

function select(from, to) {
  state.from = from; state.to = to;
  $("#from").value = from; $("#to").value = to;
  history.replaceState(null, "", from === to ? "#" : `#${from}_${to}`);
  document.querySelectorAll(".cell").forEach(c => c.setAttribute("aria-pressed", c.dataset.id === `${from}_${to}`));
  renderDetail();
}

function fromHash() {
  const [from, to] = location.hash.slice(1).split("_");
  const ids = state.locations.map(l => l.id);
  if (ids.includes(from) && ids.includes(to)) return [from, to];
  return ids.includes("diepenbeek") && ids.includes("brussels") ? ["diepenbeek", "brussels"] : [ids[0], ids[1]];
}

async function start() {
  const [index, locations] = await Promise.all([getJSON("data/index.json"), getJSON("data/locations.json")]);
  state.locations = [...locations].sort((a, b) => a.display_name.localeCompare(b.display_name));
  state.byJourney = Object.fromEntries(index.journeys.map(j => [j.journey, j]));
  state.reliable = index.reliable_samples ?? 20;
  state.scheduleSlots = index.schedule_slots ?? [];
  $("#generated").textContent = `· data generated ${new Date(index.generated).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })}`;

  const provinces = [...new Set(state.locations.map(l => l.province).filter(Boolean))].sort((a, b) => a.localeCompare(b));
  const option = l => `<option value="${esc(l.id)}">${esc(l.display_name)}</option>`;
  const options = provinces.map(p => `<optgroup label="${esc(p)}">${state.locations.filter(l => l.province === p).map(option).join("")}</optgroup>`).join("")
    + state.locations.filter(l => !l.province).map(option).join("");
  $("#from").innerHTML = options; $("#to").innerHTML = options;
  $("#province").innerHTML = '<option value="">All of Belgium</option>' + provinces.map(p => `<option>${esc(p)}</option>`).join("");
  $("#province").addEventListener("change", e => { state.province = e.target.value; renderMatrix(); });
  const [startFrom] = fromHash();
  state.province = state.locations.find(l => l.id === startFrom)?.province ?? "";
  $("#province").value = state.province;
  renderMatrix();

  $("#from").addEventListener("change", e => select(e.target.value, state.to));
  $("#to").addEventListener("change", e => select(state.from, e.target.value));
  $("#swap").addEventListener("click", () => select(state.to, state.from));
  $("#matrix").addEventListener("click", e => {
    const cell = e.target.closest(".cell");
    if (cell) { const [from, to] = cell.dataset.id.split("_"); select(from, to); }
  });
  window.addEventListener("hashchange", () => select(...fromHash()));
  select(...fromHash());
}

$("#theme").addEventListener("click", () => {
  const root = document.documentElement;
  const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  root.dataset.theme = dark ? "light" : "dark";
  try { localStorage.setItem("theme", root.dataset.theme); } catch {}
});

start().catch(error => {
  console.error(error);
  const message = error instanceof MissingData
    ? "Export data not found. Run <code>python -m travelsmart export</code>."
    : "The dashboard could not load. Details are in the browser console.";
  $("#matrix").innerHTML = `<tbody><tr><td class="na" style="padding:16px">${message}</td></tr></tbody>`;
});
