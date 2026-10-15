"use strict";

/* ================================================================ helpers */

// Tiny DOM builder. Strings always become text nodes, so team and player names
// (which come from Fantrax) can never inject markup.
function h(tag, props, ...children) {
  const el = document.createElement(tag);
  for (const [key, val] of Object.entries(props || {})) {
    if (val == null || val === false) continue;
    if (key === "class") el.className = val;
    else if (key === "style") Object.assign(el.style, val);
    else if (key.startsWith("on") && typeof val === "function") el.addEventListener(key.slice(2), val);
    else if (typeof val === "boolean") el[key] = val;
    else el.setAttribute(key, val);
  }
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

const byId = (id) => document.getElementById(id);

async function request(method, path, { params, body } = {}) {
  const url = new URL(path, window.location.origin);
  for (const [k, v] of Object.entries(params || {})) {
    if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, v);
  }
  const res = await fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch (_) { /* non-JSON error body */ }
  if (!res.ok) {
    const detail = data && data.detail;
    throw new Error(typeof detail === "string" ? detail : (detail && detail.error) || `Request failed (${res.status})`);
  }
  return data;
}
const api = {
  get: (path, params) => request("GET", path, { params }),
  post: (path, body) => request("POST", path, { body }),
};

const prefs = {
  get(key, fallback) { try { return localStorage.getItem("cfl." + key) || fallback; } catch (_) { return fallback; } },
  set(key, value) { try { localStorage.setItem("cfl." + key, value); } catch (_) { /* private mode */ } },
};

/* ================================================================ formatting */

const RATIO = new Set(["GAA", "SV%"]);
const STATUS_LABEL = { MINORS: "Minors", INJURED_RESERVE: "IR", RESERVE: "Bench" };
const ICON = { up: "▲", down: "▼", flat: "–" };

function ordinal(n) {
  if (n == null) return "–";
  const s = ["th", "st", "nd", "rd"], v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}
function fmtCat(cat, v) {
  if (v == null) return "–";
  if (cat === "SV%") return v.toFixed(3).replace(/^0\./, ".");
  if (cat === "GAA") return v.toFixed(2);
  return Math.abs(v) < 10 ? v.toFixed(2) : v.toFixed(1);
}
function fmtNum(v, digits = 1) { return v == null ? "–" : Number(v).toFixed(digits); }
function fmtSigned(v, digits = 2) {
  if (v == null || Number.isNaN(v)) return "–";
  const r = Number(v.toFixed(digits));
  if (r === 0) return (0).toFixed(digits);
  return (r > 0 ? "+" : "−") + Math.abs(r).toFixed(digits);
}
function direction(v, eps = 0.005) {
  if (v == null || Math.abs(v) < eps) return "flat";
  return v > 0 ? "up" : "down";
}
function dirMark(v, eps) {
  const d = direction(v, eps);
  return h("span", { class: d, "aria-hidden": "true" }, ICON[d]);
}
function positions(p) { return (p.eligible || []).filter((e) => e !== "F").join(",") || p.position || ""; }
function bestCats(z, n = 3) {
  return Object.entries(z || {}).sort((a, b) => b[1] - a[1]).slice(0, n)
    .map(([c, v]) => `${c} ${fmtSigned(v, 1)}`).join(" · ");
}
function teamName(id) { const t = state.teams.find((x) => x.id === id); return t ? t.name : id; }

/* ================================================================ tooltip */

const tooltip = byId("tooltip");
function bindTip(el, build) {
  const show = (x, y) => {
    tooltip.replaceChildren(...build());
    tooltip.hidden = false;
    const r = tooltip.getBoundingClientRect(), pad = 14;
    let left = x + pad, top = y + pad;
    if (left + r.width > window.innerWidth - 8) left = x - r.width - pad;
    if (top + r.height > window.innerHeight - 8) top = y - r.height - pad;
    tooltip.style.left = Math.max(8, left) + "px";
    tooltip.style.top = Math.max(8, top) + "px";
  };
  el.addEventListener("pointermove", (e) => show(e.clientX, e.clientY));
  el.addEventListener("pointerleave", () => { tooltip.hidden = true; });
  el.addEventListener("focus", () => { const r = el.getBoundingClientRect(); show(r.left + r.width / 2, r.bottom); });
  el.addEventListener("blur", () => { tooltip.hidden = true; });
}
// Value leads, label follows.
function tipContent(value, label, rows = []) {
  return [
    h("div", { class: "tt-value" }, value),
    h("div", { class: "tt-label" }, label),
    ...rows.map(([k, v]) => h("div", { class: "tt-row" }, h("span", { class: "tt-label" }, k), h("span", {}, v))),
  ];
}

/* ================================================================ marks */

// Diverging bar around a league-average baseline (blue above, red below).
function divBar(z, maxAbs, { mini = false, label = "" } = {}) {
  const el = h("div", { class: "divbar" + (mini ? " mini" : ""), tabindex: "0", role: "img", "aria-label": label });
  if (z != null && maxAbs > 0) {
    const pct = Math.min(Math.abs(z) / maxAbs, 1) * 49;
    if (pct >= 0.5) el.append(h("div", { class: "bar " + (z >= 0 ? "pos" : "neg"), style: { width: pct + "%" } }));
  }
  return el;
}
function heatStyle(z) {
  if (z == null) return { background: "var(--wash)" };
  const t = Math.max(-1, Math.min(1, z / 2));
  const pct = Math.round(Math.abs(t) * 100);
  const pole = t >= 0 ? "var(--div-pos)" : "var(--div-neg)";
  return { background: `color-mix(in oklab, ${pole} ${pct}%, var(--div-mid))`, color: pct > 55 ? "#ffffff" : "var(--ink)" };
}

/* ================================================================ state */

const state = {
  health: null,
  league: null,
  teams: [],
  team: null,
  strategy: prefs.get("strategy", "marginal"),
  overview: null,
  profiles: new Map(),
  rosterSort: { key: "need_value", dir: -1 },
  rosterMinors: false,
  trade: {
    give: new Set(), get: new Set(), partner: "", tolerance: prefs.get("tolerance", "0.25"),
    giveMinors: false, getMinors: false, giveQuery: "", getQuery: "",
  },
};
// Latest-request counters: a slower, older response never overwrites a newer one.
const seqs = { dashboard: 0, tradeLoad: 0, suggest: 0, evaluate: 0 };

function showError(err) {
  const box = byId("error");
  box.textContent = err instanceof Error ? err.message : String(err);
  box.hidden = false;
}
function clearError() { byId("error").hidden = true; }

function getProfile(teamId) {
  const key = `${teamId}|${state.strategy}`;
  if (!state.profiles.has(key)) {
    const p = api.get(`/analysis/teams/${encodeURIComponent(teamId)}/profile`, { strategy: state.strategy });
    p.catch(() => state.profiles.delete(key));
    state.profiles.set(key, p);
  }
  return state.profiles.get(key);
}
function getOverview() {
  if (!state.overview) {
    state.overview = api.get("/analysis/overview");
    state.overview.catch(() => { state.overview = null; });
  }
  return state.overview;
}
function resetCaches() { state.profiles.clear(); state.overview = null; }

/* ================================================================ header / boot */

async function boot() {
  byId("strategy-select").value = state.strategy;
  byId("tolerance-select").value = state.trade.tolerance;
  wireControls();
  try {
    const [health, teams] = await Promise.all([api.get("/health"), api.get("/teams")]);
    state.health = health;
    state.teams = teams;
    try { state.league = await api.get("/league"); } catch (_) { state.league = null; }
  } catch (err) {
    showError(err);
    return;
  }
  if (!state.league || !state.teams.length) {
    showError("No league data yet. Click “Sync data” to pull it from Fantrax.");
  }
  state.team = state.health.my_team_id || (state.teams[0] && state.teams[0].id);
  renderHeader();
  route();
}

function renderHeader() {
  byId("league-name").textContent = state.league ? `${state.league.name} · ${state.league.season_year}–${String(state.league.season_year + 1).slice(2)}` : "League not synced";
  const sel = byId("team-select");
  sel.replaceChildren(...state.teams.map((t) =>
    h("option", { value: t.id, selected: t.id === state.team }, t.name + (t.id === state.health.my_team_id ? " (you)" : ""))));
  const last = state.health.last_sync;
  byId("sync-status").textContent = last && last.finished_at
    ? `Last sync ${new Date(last.finished_at).toLocaleString()}` : "";
}

function wireControls() {
  window.addEventListener("hashchange", route);
  byId("team-select").addEventListener("change", (e) => {
    state.team = e.target.value;
    state.trade.give.clear();
    state.trade.get.clear();
    if (state.trade.partner === state.team) state.trade.partner = "";
    route();
  });
  byId("strategy-select").addEventListener("change", (e) => {
    state.strategy = e.target.value;
    prefs.set("strategy", state.strategy);
    loadDashboard();
  });
  byId("roster-minors").addEventListener("change", (e) => { state.rosterMinors = e.target.checked; loadDashboard(); });
  byId("sync-btn").addEventListener("click", runSync);

  byId("partner-select").addEventListener("change", (e) => setPartner(e.target.value));
  byId("tolerance-select").addEventListener("change", (e) => {
    state.trade.tolerance = e.target.value;
    prefs.set("tolerance", e.target.value);
    refreshSuggestions();
  });
  byId("trade-clear").addEventListener("click", () => {
    state.trade.give.clear();
    state.trade.get.clear();
    loadTrade();
  });
  byId("give-search").addEventListener("input", (e) => { state.trade.giveQuery = e.target.value; renderTradeLists(); });
  byId("get-search").addEventListener("input", (e) => { state.trade.getQuery = e.target.value; renderTradeLists(); });
  byId("give-minors").addEventListener("change", (e) => { state.trade.giveMinors = e.target.checked; renderTradeLists(); });
  byId("get-minors").addEventListener("change", (e) => { state.trade.getMinors = e.target.checked; renderTradeLists(); });
}

async function runSync() {
  const btn = byId("sync-btn");
  btn.disabled = true;
  btn.textContent = "Syncing…";
  byId("sync-status").textContent = "Pulling Fantrax rosters, matchups, NHL stats and schedule…";
  try {
    const res = await api.post("/sync");
    const failed = res.results.filter((r) => r.status !== "ok").map((r) => r.resource);
    resetCaches();
    state.health = await api.get("/health");
    state.teams = await api.get("/teams");
    try { state.league = await api.get("/league"); } catch (_) { /* still unsynced */ }
    if (!state.team) state.team = state.health.my_team_id || (state.teams[0] && state.teams[0].id);
    renderHeader();
    if (failed.length) showError(`Sync finished with errors in: ${failed.join(", ")}`);
    else clearError();
    route();
  } catch (err) {
    showError(err);
  } finally {
    btn.disabled = false;
    btn.textContent = "Sync data";
  }
}

function route() {
  const view = window.location.hash === "#trade" ? "trade" : "dashboard";
  for (const v of ["dashboard", "trade"]) {
    byId(`view-${v}`).hidden = v !== view;
    byId(`tab-${v}`).setAttribute("aria-selected", String(v === view));
  }
  if (!state.team) return;
  if (view === "dashboard") loadDashboard();
  else loadTrade();
}

/* ================================================================ dashboard */

const STRATEGY_HINT = {
  marginal: "Weights categories where several teams are close to you: that's where one more goal or hit flips a weekly matchup.",
  weakness: "Weights your lowest-ranked categories most.",
  even: "Every category counts the same.",
};

async function loadDashboard() {
  const view = byId("view-dashboard");
  const token = ++seqs.dashboard;
  byId("strategy-hint").textContent = STRATEGY_HINT[state.strategy];
  view.classList.add("is-loading");
  try {
    const [profile, overview] = await Promise.all([getProfile(state.team), getOverview()]);
    if (token !== seqs.dashboard) return;
    renderKpis(profile);
    renderCategoryProfile(profile);
    renderHeatmap(overview);
    renderRoster(profile);
    clearError();
  } catch (err) {
    if (token === seqs.dashboard) showError(err);
  } finally {
    if (token === seqs.dashboard) view.classList.remove("is-loading");
  }
}

function tile(label, value, unit, sub, { hero = false } = {}) {
  return h("div", { class: "tile" + (hero ? " hero-tile" : "") },
    h("div", { class: "tile-label" }, label),
    h("div", { class: "tile-value" + (hero ? " hero" : "") }, value, unit ? h("small", {}, unit) : null),
    sub ? h("div", { class: "tile-sub" }, sub) : null);
}

function renderKpis(profile) {
  const nCats = profile.categories.length;
  const standing = state.teams.find((t) => t.id === state.team) || {};
  const skaterGames = profile.roster.filter((p) => p.position !== "G")
    .reduce((s, p) => s + (p.lineup_games_per_week || 0), 0);
  const goalieStarts = profile.roster.reduce((s, p) => s + (p.expected_starts_per_week || 0), 0);
  byId("kpis").replaceChildren(
    tile("Expected category wins per week", fmtNum(profile.expected_category_wins, 2), `of ${nCats}`,
      "Against an average opponent, from projected rest-of-season lineups", { hero: true }),
    tile("Projected strength", ordinal(profile.projected_overall_rank), `of ${state.teams.length}`,
      "Total category strength across the league"),
    tile("Standings", standing.rank ? ordinal(standing.rank) : "–", standing.record || "",
      "Fantrax head-to-head standings"),
    tile("Skater games per week", fmtNum(skaterGames, 1), null, "Lineup games, including bench fill-ins"),
    tile("Goalie starts per week", fmtNum(goalieStarts, 2), null, "Expected starts across all your goalies"),
  );
}

function renderCategoryProfile(profile) {
  const cats = profile.categories;
  const maxAbs = Math.max(1, ...cats.map((c) => Math.abs(c.projected_z || 0)));
  const n = state.teams.length;
  const body = [];
  for (const [group, label] of [["SKATER", "Skaters"], ["GOALIE", "Goalies"]]) {
    body.push(h("tr", { class: "group-row" }, h("td", { colspan: "6" }, label)));
    for (const c of cats.filter((x) => x.group === group)) {
      const bar = divBar(c.projected_z, maxAbs, {
        label: `${c.name}: ${fmtSigned(c.projected_z)} standard deviations vs league average, ${ordinal(c.projected_rank)} of ${n}`,
      });
      bindTip(bar, () => tipContent(`${ordinal(c.projected_rank)} of ${n}`, c.name, [
        ["Projected / week", fmtCat(c.category, c.projected_per_week)],
        ["vs league avg", `${fmtSigned(c.projected_z)} sd`],
        ["Beats", `${Math.round((c.projected_beats_pct || 0) * 100)}% of teams`],
        ["Actual W-L-T", c.actual_record],
      ]));
      body.push(h("tr", {},
        h("td", {}, h("span", { class: "cat-name" }, c.category), h("span", { class: "cat-full" }, c.name)),
        h("td", { class: "bar-cell" }, bar),
        h("td", { class: "num" }, fmtCat(c.category, c.projected_per_week)),
        h("td", { class: "num" }, ordinal(c.projected_rank)),
        h("td", { class: "num" }, c.actual_record),
        h("td", {}, h("span", { class: "need-pill num" + (c.need_weight >= 1.15 ? " high" : "") }, fmtNum(c.need_weight, 2))),
      ));
    }
  }
  byId("category-profile").replaceChildren(
    h("p", { class: "card-sub", style: { marginBottom: "8px" } },
      `Strongest: ${profile.strengths.join(", ")} · Weakest: ${profile.weaknesses.join(", ")}`),
    h("div", { class: "scroll-x" }, h("table", { class: "cat-table" },
      h("thead", {}, h("tr", {},
        h("th", {}, "Category"), h("th", { style: { textAlign: "center" } }, "vs league average"),
        h("th", {}, "Proj / wk"), h("th", {}, "Rank"), h("th", {}, "Actual"), h("th", {}, "Need"))),
      h("tbody", {}, body))),
  );
}

function renderHeatmap(overview) {
  const cats = (state.league && state.league.categories || []).map((c) => c.short_name);
  const names = Object.fromEntries((state.league && state.league.categories || []).map((c) => [c.short_name, c.name]));
  const head = h("tr", {}, h("th", { class: "team" }, "Team"),
    cats.map((c) => h("th", { title: names[c] || c }, c)), h("th", { title: "Expected category wins per week" }, "Exp. W"));
  const rows = overview.teams.map((t) => h("tr", { class: t.team_id === state.team ? "me" : "" },
    h("td", { class: "team", title: t.team_name }, t.team_name),
    cats.map((c) => {
      const cell = t.projected[c] || {};
      const td = h("td", { class: "cell num", tabindex: "0", style: heatStyle(cell.z),
        "aria-label": `${t.team_name}, ${names[c] || c}: ${ordinal(cell.rank)}` }, cell.rank != null ? cell.rank : "–");
      bindTip(td, () => tipContent(`${ordinal(cell.rank)} in ${names[c] || c}`, t.team_name, [
        ["Projected / week", fmtCat(c, cell.value)],
        ["vs league avg", `${fmtSigned(cell.z)} sd`],
        ["Actual W-L-T", (t.actual_records || {})[c] || "–"],
      ]));
      return td;
    }),
    h("td", { class: "ecw num" }, fmtNum(t.expected_category_wins, 2)),
  ));
  byId("league-heatmap").replaceChildren(h("table", { class: "heatmap" }, h("thead", {}, head), h("tbody", {}, rows)));
}

const ROSTER_COLUMNS = [
  { key: "name", label: "Player", left: true, value: (p) => p.name },
  { key: "lineup_games_per_week", label: "Games / wk", value: (p) => p.lineup_games_per_week,
    render: (p) => p.expected_starts_per_week != null
      ? `${fmtNum(p.expected_starts_per_week, 2)} starts` : fmtNum(p.lineup_games_per_week, 1) },
  { key: "value", label: "Value", value: (p) => p.value, render: (p) => fmtNum(p.value, 2) },
  { key: "need_value", label: "Need value", value: (p) => p.need_value, render: (p) => fmtNum(p.need_value, 2) },
  { key: "trade_rank", label: "Trade rank", asc: true, value: (p) => p.trade_rank,
    render: (p) => (p.trade_rank ? `#${p.trade_rank}` : "–") },
  { key: "market_value", label: "Market", value: (p) => p.market_value, render: (p) => fmtNum(p.market_value, 1) },
  { key: "gp_this_season", label: "GP (this/last)", value: (p) => p.gp_this_season,
    render: (p) => `${p.gp_this_season}/${p.gp_last_season}` },
  { key: "best", label: "Best categories", left: true, sortable: false, render: (p) => bestCats(p.z) },
];

function renderRoster(profile) {
  const { key, dir } = state.rosterSort;
  const col = ROSTER_COLUMNS.find((c) => c.key === key);
  const players = profile.roster.filter((p) => state.rosterMinors || p.roster_status !== "MINORS");
  players.sort((a, b) => {
    const va = col.value(a), vb = col.value(b);
    if (va == null && vb == null) return 0;
    if (va == null) return 1;
    if (vb == null) return -1;
    return (typeof va === "string" ? va.localeCompare(vb) : va - vb) * dir;
  });
  const head = h("tr", {}, ROSTER_COLUMNS.map((c) => {
    const sortable = c.sortable !== false;
    const th = h("th", {
      class: (c.left ? "left " : "") + (sortable ? "sortable" : ""),
      "aria-sort": c.key === key ? (dir === 1 ? "ascending" : "descending") : null,
      tabindex: sortable ? "0" : null,
    }, c.label);
    if (sortable) {
      const sort = () => {
        state.rosterSort = c.key === key ? { key, dir: -dir }
          : { key: c.key, dir: c.asc || c.key === "name" ? 1 : -1 };
        renderRoster(profile);
      };
      th.addEventListener("click", sort);
      th.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); sort(); } });
    }
    return th;
  }));
  const rows = players.map((p) => h("tr", {}, ROSTER_COLUMNS.map((c) => {
    if (c.key === "name") {
      return h("td", { class: "left" },
        h("span", { class: "player-name" }, p.name),
        STATUS_LABEL[p.roster_status] ? h("span", { class: "badge" }, STATUS_LABEL[p.roster_status]) : null,
        h("div", { class: "sub" }, [positions(p), p.nhl_team].filter(Boolean).join(" · ")));
    }
    return h("td", { class: (c.left ? "left cats-inline" : "num") }, c.render ? c.render(p) : c.value(p));
  })));
  byId("roster-table").replaceChildren(h("table", { class: "data-table" }, h("thead", {}, head), h("tbody", {}, rows)));
}

/* ================================================================ trade builder */

let myRoster = [];
let partnerRoster = [];

async function loadTrade() {
  const partnerSel = byId("partner-select");
  partnerSel.replaceChildren(
    h("option", { value: "" }, "No partner: price my players"),
    ...state.teams.filter((t) => t.id !== state.team).map((t) =>
      h("option", { value: t.id, selected: t.id === state.trade.partner }, t.name)));
  byId("give-title").textContent = `${teamName(state.team)} gives`;
  const token = ++seqs.tradeLoad;
  try {
    const [mine, theirs] = await Promise.all([
      getProfile(state.team),
      state.trade.partner ? getProfile(state.trade.partner) : Promise.resolve(null),
    ]);
    if (token !== seqs.tradeLoad) return;
    myRoster = mine.roster;
    partnerRoster = theirs ? theirs.roster : [];
    clearError();
  } catch (err) {
    if (token === seqs.tradeLoad) showError(err);
    return;
  }
  renderTradeLists();
  refreshSuggestions();
  refreshEvaluation();
}

function setPartner(teamId) {
  if (teamId === state.trade.partner) return;
  state.trade.partner = teamId;
  state.trade.get.clear();
  loadTrade();
}

function playerRow(p, selectedSet, onToggle) {
  const selected = selectedSet.has(p.fantrax_id);
  const games = p.expected_starts_per_week != null
    ? `${fmtNum(p.expected_starts_per_week, 2)} starts/wk` : `${fmtNum(p.lineup_games_per_week, 1)} g/wk`;
  const box = h("input", { type: "checkbox", checked: selected, "aria-label": `Select ${p.name}` });
  const row = h("label", { class: "prow" + (selected ? " selected" : "") },
    box,
    h("div", { class: "prow-main" },
      h("div", { class: "prow-name" }, p.name,
        STATUS_LABEL[p.roster_status] ? h("span", { class: "badge" }, STATUS_LABEL[p.roster_status]) : null),
      h("div", { class: "prow-meta" }, [positions(p), p.nhl_team, p.z && Object.keys(p.z).length ? bestCats(p.z, 2) : "no NHL stats"]
        .filter(Boolean).join(" · "))),
    h("div", { class: "prow-stats" },
      h("div", {}, h("strong", {}, p.trade_rank ? `#${p.trade_rank}` : "–"), ` · mv ${fmtNum(p.market_value, 1)}`),
      h("div", {}, games)));
  box.addEventListener("change", () => onToggle(p.fantrax_id, box.checked));
  return row;
}

function filteredList(players, query, showMinors, selected) {
  const q = query.trim().toLowerCase();
  return players
    .filter((p) => selected.has(p.fantrax_id) || showMinors || p.roster_status !== "MINORS")
    .filter((p) => !q || p.name.toLowerCase().includes(q))
    .sort((a, b) => (a.trade_rank || 1e9) - (b.trade_rank || 1e9));
}

function renderTradeLists() {
  const t = state.trade;
  const toggle = (set) => (id, on) => {
    if (on) set.add(id); else set.delete(id);
    renderTradeLists();
    refreshSuggestions();
    refreshEvaluation();
  };
  const give = filteredList(myRoster, t.giveQuery, t.giveMinors, t.give);
  byId("give-list").replaceChildren(...(give.length
    ? give.map((p) => playerRow(p, t.give, toggle(t.give))) : [h("div", { class: "empty" }, "No players match")]));

  const hasPartner = Boolean(t.partner);
  byId("get-panel").hidden = !hasPartner;
  byId("get-title").textContent = hasPartner ? `${teamName(t.partner)} gives` : "You get";
  byId("get-sub").textContent = hasPartner
    ? "Select players to receive" : "Pick a trade partner above, or add one of the suggestions";
  if (hasPartner) {
    const get = filteredList(partnerRoster, t.getQuery, t.getMinors, t.get);
    byId("get-list").replaceChildren(...(get.length
      ? get.map((p) => playerRow(p, t.get, toggle(t.get))) : [h("div", { class: "empty" }, "No players match")]));
  }
}

let suggestTimer = null;
function refreshSuggestions() {
  clearTimeout(suggestTimer);
  suggestTimer = setTimeout(loadSuggestions, 200);
}

async function loadSuggestions() {
  const t = state.trade;
  const box = byId("suggestions");
  const token = ++seqs.suggest;
  if (!t.give.size) {
    box.replaceChildren(h("p", { class: "card-sub", style: { marginBottom: "12px" } },
      "Select players you'd give up to see comparable-value targets ranked by how much they help you."));
    return;
  }
  box.classList.add("is-loading");
  try {
    const res = await api.get(`/analysis/teams/${encodeURIComponent(state.team)}/targets`, {
      offer: [...t.give].join(","), from_team: t.partner || null, tolerance: t.tolerance, limit: 8,
    });
    if (token !== seqs.suggest) return;
    const o = res.offer;
    const items = res.targets.map((p) => {
      const add = h("button", { class: "btn small ghost", type: "button" }, t.get.has(p.fantrax_id) ? "Added" : "Add");
      add.disabled = t.get.has(p.fantrax_id);
      add.addEventListener("click", () => addFromSuggestion(p));
      return h("div", { class: "sug" },
        h("div", { style: { minWidth: "0" } },
          h("div", { class: "prow-name" }, p.name, p.win_win ? h("span", { class: "badge solid" }, "Win-win") : null),
          h("div", { class: "prow-meta" }, [positions(p), p.nhl_team, `#${p.trade_rank}`, t.partner ? null : p.fantasy_team]
            .filter(Boolean).join(" · "))),
        h("div", { class: "gain" },
          h("div", {}, "You ", dirMark(p.your_gain), " ", fmtSigned(p.your_gain)),
          h("div", {}, "Them ", dirMark(p.their_gain), " ", fmtSigned(p.their_gain))),
        add);
    });
    box.replaceChildren(h("div", { class: "suggestions" },
      h("h3", {}, `Comparable to ${o.name} (≈ #${o.trade_rank}, ranks #${o.comparable_trade_ranks[0]}–#${o.comparable_trade_ranks[1]})`),
      h("p", { class: "card-sub", style: { marginBottom: "6px" } },
        "Gains are the change in expected category wins per week if you swap your selection for that one player."),
      items.length ? items : h("div", { class: "empty" }, "No players in that value range. Try a looser comparable value.")));
  } catch (err) {
    if (token === seqs.suggest) box.replaceChildren(h("div", { class: "empty" }, err.message));
  } finally {
    if (token === seqs.suggest) box.classList.remove("is-loading");
  }
}

function addFromSuggestion(p) {
  const t = state.trade;
  if (t.partner !== p.fantasy_team_id) {
    t.partner = p.fantasy_team_id;
    t.get.clear();
  }
  t.get.add(p.fantrax_id);
  loadTrade();
}

let evalTimer = null;
function refreshEvaluation() {
  clearTimeout(evalTimer);
  evalTimer = setTimeout(loadEvaluation, 150);
}

async function loadEvaluation() {
  const t = state.trade;
  const out = byId("trade-result");
  const token = ++seqs.evaluate;
  if (!t.give.size && !t.get.size) {
    out.replaceChildren();
    return;
  }
  out.classList.add("is-loading");
  try {
    const res = await api.post("/analysis/trade", {
      team_id: state.team, give: [...t.give], other_team_id: t.partner || null, get: [...t.get],
    });
    if (token !== seqs.evaluate) return;
    renderEvaluation(res);
    clearError();
  } catch (err) {
    if (token === seqs.evaluate) out.replaceChildren(h("div", { class: "banner error" }, err.message));
  } finally {
    if (token === seqs.evaluate) out.classList.remove("is-loading");
  }
}

function verdict(res) {
  const [me, them] = res.sides;
  const dMe = me.expected_category_wins.delta;
  if (!them) {
    return {
      title: me.gives.length ? `Cost of losing ${me.gives.map((p) => p.name).join(" + ")}` : "Adding players",
      sub: `${fmtSigned(dMe)} expected category wins per week for ${me.team_name}, before you refill the roster spot.`,
    };
  }
  const dThem = them.expected_category_wins.delta;
  const up = (d) => d > 0.005, down = (d) => d < -0.005;
  let title;
  if (up(dMe) && up(dThem)) title = "Win-win: both teams get stronger";
  else if (up(dMe) && !up(dThem)) title = "Good for you, a tougher sell";
  else if (!up(dMe) && up(dThem)) title = "Helps them more than you";
  else if (down(dMe) && down(dThem)) title = "Makes both teams weaker";
  else title = "Roughly neutral";
  return {
    title,
    sub: `You ${fmtSigned(dMe)} · ${them.team_name} ${fmtSigned(dThem)} expected category wins per week`,
  };
}

function marketCard(res) {
  const m = res.market;
  const give = m.you_give || 0, get = m.you_get || 0;
  const max = Math.max(give, get, 0.01);
  const row = (label, value, rank, cls) => h("div", { class: "market-row" },
    h("span", {}, label),
    h("div", { class: "market-track" }, h("div", { class: "market-fill " + cls, style: { width: (value / max) * 100 + "%" } })),
    h("span", { class: "num" }, `${fmtNum(value, 1)}${rank ? ` · ≈ #${rank}` : ""}`));
  const diff = get - give;
  let note;
  if (!res.sides[1]) note = "Market value is value over a replacement-level starter. Pick a partner to compare both sides.";
  else if (Math.abs(diff) <= Math.max(0.5, 0.15 * max)) note = "Even on market value: a fair ask.";
  else if (diff > 0) note = `You get ${fmtNum(diff, 1)} more market value; expect pushback.`;
  else note = `You pay a ${fmtNum(-diff, 1)} premium in market value.`;
  return h("div", { class: "card market" },
    h("h3", {}, "Market value"),
    row("You give", give, m.you_give_equiv_rank, "give"),
    res.sides[1] ? row("You get", get, m.you_get_equiv_rank, "get") : null,
    h("p", { class: "market-note" }, note));
}

function sideCard(side, maxDz) {
  const ecw = side.expected_category_wins;
  const d = direction(ecw.delta);
  const r = side.roster;
  const facts = [
    ["Roster (cap)", `${r.before} → ${r.after}${r.cap ? ` / ${r.cap}` : ""}`],
    ["Skater games / wk", `${fmtNum(side.volume.before.skater_games, 1)} → ${fmtNum(side.volume.after.skater_games, 1)}`],
    ["Goalie starts / wk", `${fmtNum(side.volume.before.goalie_starts, 2)} → ${fmtNum(side.volume.after.goalie_starts, 2)}`],
    ["Market value", fmtSigned(side.market_value.net, 1)],
  ];
  const rows = side.categories.map((c) => {
    const rankDelta = c.before.rank != null && c.after.rank != null ? c.before.rank - c.after.rank : 0;
    const bar = divBar(c.delta_z, maxDz, { mini: true, label: `${c.name} change ${fmtSigned(c.delta_z)} sd` });
    bindTip(bar, () => tipContent(`${fmtSigned(c.delta_z)} sd`, c.name, [
      ["Before", `${fmtCat(c.category, c.before.value)} (${ordinal(c.before.rank)})`],
      ["After", `${fmtCat(c.category, c.after.value)} (${ordinal(c.after.rank)})`],
    ]));
    return h("tr", {},
      h("td", { class: "left" }, h("span", { class: "cat-name" }, c.category)),
      h("td", { class: "num" }, fmtCat(c.category, c.before.value)),
      h("td", { class: "num" }, fmtCat(c.category, c.after.value)),
      h("td", { class: "num delta" }, `${ordinal(c.before.rank)} → ${ordinal(c.after.rank)} `,
        rankDelta ? h("span", { class: rankDelta > 0 ? "up" : "down" }, `${rankDelta > 0 ? "▲" : "▼"}${Math.abs(rankDelta)}`) : null),
      h("td", {}, bar));
  });
  const notes = [];
  if (r.cap && r.after < r.cap) notes.push(`${r.cap - r.after} open roster spot(s): you'd fill from waivers (not counted here).`);
  if (side.auto_dropped.length) notes.push("Over the roster limit, so the weakest player is dropped in the projection.");
  return h("article", { class: "card side" },
    h("div", { class: "side-team" }, side.role === "you" ? "You" : "Trade partner", " · ", side.team_name),
    h("div", { class: "side-hero" },
      h("span", { class: "icon " + d, "aria-hidden": "true" }, ICON[d]),
      h("span", { class: "big" }, fmtSigned(ecw.delta)),
    ),
    h("div", { class: "side-caption" },
      `Expected category wins per week: ${fmtNum(ecw.before, 2)} → ${fmtNum(ecw.after, 2)}`),
    h("div", { class: "moves" },
      side.gives.map((p) => h("span", { class: "chip out" }, p.name)),
      side.gets.map((p) => h("span", { class: "chip in" }, p.name)),
      side.auto_dropped.map((p) => h("span", { class: "chip drop", title: "Dropped to stay under the roster limit" }, p.name))),
    h("div", { class: "facts" }, facts.map(([k, v]) => h("div", {},
      h("div", { class: "fact-label" }, k), h("div", { class: "fact-value num" }, v)))),
    h("div", { class: "scroll-x" }, h("table", { class: "data-table compact" },
      h("thead", {}, h("tr", {}, h("th", { class: "left" }, "Cat"), h("th", {}, "Before / wk"), h("th", {}, "After / wk"),
        h("th", {}, "Rank"), h("th", { style: { textAlign: "center" } }, "Change"))),
      h("tbody", {}, rows))),
    notes.map((n) => h("p", { class: "market-note" }, n)));
}

function renderEvaluation(res) {
  const v = verdict(res);
  const allDz = res.sides.flatMap((s) => s.categories.map((c) => Math.abs(c.delta_z || 0)));
  const maxDz = Math.max(0.25, ...allDz);
  byId("trade-result").replaceChildren(
    h("div", { class: "result-head" },
      h("div", { class: "card verdict" },
        h("div", { class: "verdict-title" }, v.title),
        h("p", { class: "verdict-sub" }, v.sub),
        h("p", { class: "market-note" },
          "Simulated with daily lineups over the rest of the season. Each side is scored against the unchanged rest of the league.")),
      marketCard(res)),
    h("div", { class: "sides" }, res.sides.map((s) => sideCard(s, maxDz))));
}

boot();
