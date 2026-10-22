"use strict";

import { dataChanged, onDataChanged, onSignedOut } from "../bridge";
import { fmtCat, fmtMoney, fmtNum, fmtSigned, ordinal } from "../format";

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
    if (Array.isArray(v)) v.forEach((item) => url.searchParams.append(k, item));
    else if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, v);
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
    const err = new Error(typeof detail === "string" ? detail : (detail && detail.error) || `Request failed (${res.status})`);
    err.status = res.status;
    if (res.status === 401 && !path.startsWith("/auth/")) showSignIn();   // the session ran out
    throw err;
  }
  return data;
}
const api = {
  get: (path, params) => request("GET", path, { params }),
  post: (path, body) => request("POST", path, { body }),
  put: (path, body) => request("PUT", path, { body }),
  del: (path) => request("DELETE", path),
};

const prefs = {
  get(key, fallback) { try { return localStorage.getItem("cfl." + key) || fallback; } catch (_) { return fallback; } },
  set(key, value) { try { localStorage.setItem("cfl." + key, value); } catch (_) { /* private mode */ } },
};

/* ================================================================ formatting */

const RATIO = new Set(["GAA", "SV%"]);
const STATUS_LABEL = { MINORS: "Prospect", INJURED_RESERVE: "IR", RESERVE: "Bench" };
const ICON = { up: "▲", down: "▼", flat: "–" };

function fmtContract(p) {
  if (p.salary == null) return null;
  const end = p.contract_end && p.contract_end.status ? ` · ${p.contract_end.status} '${String(p.contract_end.year).slice(2)}` : "";
  return `${fmtMoney(p.salary)}${p.contract_years ? ` × ${p.contract_years} yr${p.contract_years === 1 ? "" : "s"}` : ""}${end}`;
}
// RFA / UFA when the contract ends, e.g. "RFA '29". Sort key: year, then RFA before UFA.
function endPill(p) {
  const e = p.contract_end;
  if (!e) return h("span", { class: "sub" }, "–");
  const label = `${e.status || "?"} '${String(e.year).slice(2)}`;
  return h("span", { class: "pill end " + (e.status ? e.status.toLowerCase() : "unknown"),
    title: `After ${e.after_season}: ${e.reason || ""}` }, label);
}
const endSortKey = (p) => (p.contract_end ? p.contract_end.year * 10 + (p.contract_end.status === "UFA" ? 2 : 1) : null);
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
function possessive(name) { return /s$/i.test(name) ? `${name}’` : `${name}’s`; }

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

/* ================================================================ state */

const state = {
  me: null,             // who's signed in: { email, name, team_id, is_admin, status, auth }
  health: null,
  league: null,
  teams: [],
  team: null,
  strategy: prefs.get("strategy", "marginal"),   // the Analysis tab's need weighting
  overviews: new Map(),   // the Dashboard's /teams/{id}/overview, by team
  profiles: new Map(),    // /analysis/teams/{id}/profile (Analysis tab, Trade builder), by team and weighting
  leagueAnalysis: new Map(),   // /analysis/overview, the Analysis tab's heatmap
  rosterSort: { key: "salary", dir: -1 },
  rosterView: "roster",
  analysisSort: { key: "need_value", dir: -1 },
  analysisView: "roster",
  playersSort: { key: "season0", dir: -1 },
  playersView: "all",
  changesTeam: "",
  changesKind: "all",
  offseason: null,      // the last /offseason response, so team and status filters redraw without a request
  offseasonYear: null,  // null: the next offseason, whatever season the league is in
  offseasonTeam: "",
  offseasonView: "all",
  offseasonSort: { key: "final_salary", dir: -1 },
  trade: {
    give: new Set(), get: new Set(), partner: "", tolerance: prefs.get("tolerance", "0.25"),
    giveMinors: false, getMinors: false, giveQuery: "", getQuery: "",
    pickups: new Map(),   // free agents selected on the waiver wire, by id, so they stay listed
  },
};
// Partner-select value for picking up free agents instead of trading.
const WAIVERS = "__waivers__";
const onWaivers = () => state.trade.partner === WAIVERS;
// Latest-request counters: a slower, older response never overwrites a newer one.
const seqs = { dashboard: 0, analysis: 0, tradeLoad: 0, suggest: 0, evaluate: 0, pool: 0 };

function showError(err) {
  const box = byId("error");
  box.textContent = err instanceof Error ? err.message : String(err);
  box.hidden = false;
}
function clearError() { byId("error").hidden = true; }

// One request per key until something changes (a sync or an edit clears them).
function cached(map, key, path) {
  if (!map.has(key)) {
    const p = api.get(path);
    p.catch(() => map.delete(key));
    map.set(key, p);
  }
  return map.get(key);
}
const getOverview = (id) => cached(state.overviews, id, `/teams/${encodeURIComponent(id)}/overview`);
const getProfile = (id) => cached(state.profiles, `${id}|${state.strategy}`,
  `/analysis/teams/${encodeURIComponent(id)}/profile?strategy=${state.strategy}`);
const getLeagueAnalysis = () => cached(state.leagueAnalysis, "league", "/analysis/overview");
function resetCaches() { state.profiles.clear(); state.overviews.clear(); state.leagueAnalysis.clear(); }
onDataChanged(resetCaches);
onSignedOut(showSignIn);

/* ================================================================ header / boot */

async function boot() {
  byId("strategy-select").value = state.strategy;
  byId("tolerance-select").value = state.trade.tolerance;
  wireControls();
  try {
    state.me = await api.get("/auth/me");
  } catch (err) {
    if (err.status === 401) showSignIn();
    else showError(err);
    return;
  }
  if (state.me.status !== "active") { showWaiting(); return; }
  applyRole();
  if (state.me.auth && !state.me.team_id) {
    showError(state.me.is_admin
      ? "Your account isn't linked to a team yet. Pick yours in the Admin tab, on your own row."
      : "Your account isn't linked to a team yet. Ask the admin to link it.");
  }
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
  renderRefreshButton(state.health.refresh_wait_seconds);
}

const REFRESH_LABEL = byId("refresh-btn").textContent;
let refreshTimer = null;

function renderRefreshButton(wait) {
  const btn = byId("refresh-btn");
  clearTimeout(refreshTimer);
  btn.disabled = wait > 0;
  btn.textContent = wait > 0
    ? `Refresh available ${new Date(Date.now() + wait * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`
    : REFRESH_LABEL;
  if (wait > 0) refreshTimer = setTimeout(() => renderRefreshButton(0), wait * 1000);
}

function wireControls() {
  window.addEventListener("hashchange", route);
  let searchTimer = null;
  byId("players-search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(loadPlayers, 250); });
  byId("players-team").addEventListener("change", (e) => { state.playersTeam = e.target.value; loadPlayers(); });
  byId("players-missing").addEventListener("change", loadPlayers);
  byId("changes-team").addEventListener("change", (e) => { state.changesTeam = e.target.value; loadChanges(); });
  byId("offseason-year").addEventListener("change", (e) => { state.offseasonYear = Number(e.target.value); loadOffseason(); });
  byId("offseason-team").addEventListener("change", (e) => { state.offseasonTeam = e.target.value; renderOffseason(); });
  byId("team-select").addEventListener("change", (e) => {
    state.team = e.target.value;
    state.trade.give.clear();
    state.trade.get.clear();
    state.trade.pickups.clear();
    if (state.trade.partner === state.team) state.trade.partner = "";
    route();
  });
  byId("strategy-select").addEventListener("change", (e) => {
    state.strategy = e.target.value;
    prefs.set("strategy", state.strategy);
    loadAnalysis();
  });
  byId("refresh-btn").addEventListener("click", () => runSync("quick"));
  byId("sync-btn").addEventListener("click", confirmFullSync);

  byId("partner-select").addEventListener("change", (e) => setPartner(e.target.value));
  byId("tolerance-select").addEventListener("change", (e) => {
    state.trade.tolerance = e.target.value;
    prefs.set("tolerance", e.target.value);
    refreshSuggestions();
  });
  byId("trade-clear").addEventListener("click", () => {
    state.trade.give.clear();
    state.trade.get.clear();
    state.trade.pickups.clear();
    loadTrade();
  });
  byId("give-search").addEventListener("input", (e) => { state.trade.giveQuery = e.target.value; renderTradeLists(); });
  let poolTimer = null;
  byId("get-search").addEventListener("input", (e) => {
    state.trade.getQuery = e.target.value;
    if (!onWaivers()) { renderTradeLists(); return; }
    clearTimeout(poolTimer);
    poolTimer = setTimeout(loadWaiverPool, 250);
  });
  byId("give-minors").addEventListener("change", (e) => { state.trade.giveMinors = e.target.checked; renderTradeLists(); });
  byId("get-minors").addEventListener("change", (e) => { state.trade.getMinors = e.target.checked; renderTradeLists(); });
}

// "quick" only refreshes what changes day to day; "full" re-reads every source.
const SYNC_MODES = {
  quick: { resources: ["rosters", "standings", "matchups", "stats", "match", "bios"], busy: "Refreshing…",
    status: "Pulling today's Fantrax rosters, standings and matchup scores, and NHL.com stats…",
    done: "Rosters and stats refreshed" },
  full: { resources: null, busy: "Syncing…",
    status: "Full sync: Fantrax league, rosters and matchups, NHL.com stats and schedule, salary sheets…",
    done: "Full sync finished" },
};

function confirmDialog({ title, body, confirmLabel, danger = false }) {
  const dialog = byId("confirm-dialog");
  return new Promise((resolve) => {
    const finish = (ok) => { dialog.close(); resolve(ok); };
    byId("confirm-dialog-body").replaceChildren(
      h("h2", { id: "cd-title" }, title),
      ...body,
      h("div", { class: "dialog-actions" },
        h("span", { class: "spacer" }),
        h("button", { class: "btn ghost", type: "button", onclick: () => finish(false) }, "Cancel"),
        h("button", { class: "btn" + (danger ? " danger" : ""), type: "button", onclick: () => finish(true) }, confirmLabel)));
    dialog.addEventListener("cancel", () => resolve(false), { once: true });
    dialog.showModal();
  });
}

async function confirmFullSync() {
  const ok = await confirmDialog({
    title: "Run a full sync?",
    danger: true,
    confirmLabel: "Run full sync",
    body: [
      h("p", { class: "dialog-text" }, "This re-reads every source and replaces what the app has stored from them:"),
      h("ul", { class: "dialog-list" },
        h("li", {}, "Fantrax: league settings, teams, player list, rosters, standings and matchup scores."),
        h("li", {}, "NHL.com: stats for this season and last, and the NHL schedule."),
        h("li", {}, h("strong", {}, "Salary sheets: "),
          "every team's sheet contracts are replaced with what the sheets say right now. Anything changed on a sheet since the last sync shows up here.")),
      h("p", { class: "dialog-text" }, "Your contract edits made in this app are kept. Nothing is written to Fantrax or Google."),
    ],
  });
  if (ok) runSync("full");
}

async function runSync(mode) {
  const cfg = SYNC_MODES[mode];
  const buttons = [byId("refresh-btn"), byId("sync-btn")];
  const btn = mode === "full" ? byId("sync-btn") : byId("refresh-btn");
  const label = btn.textContent;
  buttons.forEach((b) => { b.disabled = true; });
  btn.textContent = cfg.busy;
  byId("sync-status").textContent = cfg.status;
  try {
    const res = await request("POST", "/sync", { params: cfg.resources ? { resources: cfg.resources } : {} });
    const failed = res.results.filter((r) => r.status !== "ok").map((r) => r.resource);
    dataChanged();
    state.health = await api.get("/health");
    state.teams = await api.get("/teams");
    try { state.league = await api.get("/league"); } catch (_) { /* still unsynced */ }
    if (!state.team) state.team = state.health.my_team_id || (state.teams[0] && state.teams[0].id);
    renderHeader();
    if (failed.length) showError(`Sync finished with errors in: ${failed.join(", ")}`);
    else clearError();
    byId("sync-status").textContent = `${cfg.done} ${new Date().toLocaleTimeString()}.`;
    route();
  } catch (err) {
    showError(err);
    if (err.status === 429) state.health = await api.get("/health");   // someone refreshed after this page loaded
    renderHeader();
  } finally {
    buttons.forEach((b) => { b.disabled = false; });
    btn.textContent = label;
    renderRefreshButton(state.health.refresh_wait_seconds);
  }
}

const VIEWS = ["dashboard", "analysis", "trade", "players", "changes", "rules", "offseason", "admin", "account"];
const ADMIN_VIEWS = new Set(["trade", "offseason", "admin"]);
const isAdmin = () => Boolean(state.me && state.me.is_admin);

function currentView() {
  const v = window.location.hash.slice(1);
  return VIEWS.includes(v) && (isAdmin() || !ADMIN_VIEWS.has(v)) ? v : "dashboard";
}

function route() {
  if (!state.me || state.me.status !== "active") return;
  const view = currentView();
  byId("view-signin").hidden = true;
  byId("team-field").hidden = ["players", "changes", "rules", "offseason", "admin", "account"].includes(view);   // they pick teams their own way
  for (const v of VIEWS) {
    byId(`view-${v}`).hidden = v !== view;
    if (v !== "account") byId(`tab-${v}`).setAttribute("aria-selected", String(v === view));
  }
  byId("account-link").setAttribute("aria-current", view === "account" ? "page" : "false");   // in the header, not a tab
  if (view === "account") { renderAccount(); return; }
  if (view === "rules") { showView("rules-card", () => import("../views/rules/RulesPage").then((m) => m.RulesPage)); return; }
  if (view === "admin") { loadAdmin(); return; }
  if (view === "offseason") { loadOffseason(); return; }
  if (!state.team) return;
  if (view === "dashboard") loadDashboard();
  else if (view === "analysis") loadAnalysis();
  else if (view === "trade") loadTrade();
  else if (view === "changes") loadChanges();
  else loadPlayers();
}

// Mounts a React view in its element the first time it shows. React and Effect load only then.
function showView(id, load) {
  Promise.all([import("../views/mount"), load()])
    .then(([{ mountView }, View]) => mountView(byId(id), View))
    .catch(showError);
}

/* ================================================================ dashboard */

async function loadDashboard() {
  const view = byId("view-dashboard");
  const token = ++seqs.dashboard;
  view.classList.add("is-loading");
  try {
    const ov = await getOverview(state.team);
    if (token !== seqs.dashboard) return;
    renderKpis(ov);
    renderWeek(ov);
    renderStandings(ov);
    renderRoster(ov);
    clearError();
  } catch (err) {
    if (token === seqs.dashboard) showError(err);
  } finally {
    if (token === seqs.dashboard) view.classList.remove("is-loading");
  }
}

// `error`: over a league limit (cap, contract-year bank, roster). The sub says by how much, with overBy().
// `hero`: the one number a row of tiles leads with, twice as wide.
// `help`: a GLOSSARY key, shown as a hint on the label.
function tile(label, value, unit, sub, { error = false, hero = false, help = null } = {}) {
  const labelEl = h("div", { class: "tile-label" }, label);
  if (help) withHelp(labelEl, help);
  return h("div", { class: "tile" + (error ? " error" : "") + (hero ? " hero-tile" : "") },
    labelEl,
    h("div", { class: "tile-value" + (hero ? " hero" : "") }, value, unit ? h("small", {}, unit) : null),
    sub ? h("div", { class: "tile-sub" }, sub) : null);
}
const overBy = (text) => [h("span", { class: "down", "aria-hidden": "true" }, "▲ "), text];

// "Oct 4" from a Fantrax period date such as 2026-10-04T13:00:00.0-0400.
const periodDay = (iso) => new Date(`${iso.slice(0, 10)}T12:00`).toLocaleDateString([], { month: "short", day: "numeric" });
const weekDates = (week) => `${periodDay(week.start_date)} – ${periodDay(week.end_date)}`;
// A category total as Fantrax shows it: ratios to 2 or 3 decimals, counts whole.
function fmtScore(cat, v) {
  if (v == null) return "–";
  if (RATIO.has(cat)) return fmtCat(cat, v);
  return cat === "+/-" ? fmtSigned(v, 0) : String(Math.round(v));
}
// This week's category score against one opponent, from the team's side. Null before it starts.
function weekScore(m) {
  if (!m.record) return null;
  const [w, l, t] = m.record;
  const dir = w > l ? "up" : w < l ? "down" : "flat";
  return { text: `${w}–${l}–${t}`, dir, says: { up: "Winning vs", down: "Losing to", flat: "Tied with" }[dir] };
}
const RESULT = { WIN: ["W", "up", "won"], LOSS: ["L", "down", "lost"], TIE: ["T", "flat", "tied"] };

function renderKpis(ov) {
  const standing = state.teams.find((t) => t.id === state.team) || {};
  byId("kpis").replaceChildren(...[
    weekTile(ov.week),
    tile("Standings", standing.rank ? ordinal(standing.rank) : "–", `of ${state.teams.length}`,
      [standing.record, standing.games_back ? `${fmtNum(standing.games_back, 1)} games back` : null]
        .filter(Boolean).join(" · ") || "Fantrax standings"),
    spotsTile(ov),
  ].filter(Boolean));
}

// The week's score, one line per opponent (some weeks have two), or the bye.
function weekTile(week) {
  if (!week) return null;
  if (!week.matchups.length) {
    return tile(`Week ${week.period}`, week.bye ? "Bye" : "–", null, week.bye ? "No matchup this week" : "No matchup found");
  }
  return h("div", { class: "tile" },
    h("div", { class: "tile-label" }, `Week ${week.period} · ${weekDates(week)}`),
    week.matchups.map((m) => {
      const s = weekScore(m);
      return [
        h("div", { class: "tile-value" }, s ? s.text : "0–0–0"),
        h("div", { class: "tile-sub" }, s ? [h("span", { class: s.dir, "aria-hidden": "true" }, ICON[s.dir]), ` ${s.says} `] : "vs ",
          m.team_name),
      ];
    }));
}

// Active and bench players count against the roster limit; IR and prospects don't.
function spotsTile(ov) {
  const { used, limit } = ov.roster_spots;
  const count = (status) => ov.roster.filter((p) => p.roster_status === status).length;
  const ir = count("INJURED_RESERVE"), minors = count("MINORS");
  const others = [ir ? `${ir} on IR` : null, minors ? `${minors} prospect${minors === 1 ? "" : "s"}` : null];
  if (!limit) return tile("Roster", String(used), "active and bench", others.filter(Boolean).join(" · "));
  const over = used - limit;
  return tile("Roster spots", String(used), `of ${limit}`,
    [over > 0 ? overBy(`${over} over the limit`) : over === 0 ? "Full" : `${-over} open`,
      others.filter(Boolean).map((x) => ` · ${x}`)], { error: over > 0 });
}

// This week's head-to-head in each category, next to the season's record and league rank.
function renderWeek(ov) {
  const week = ov.week;
  const games = week ? week.matchups : [];
  const mine = state.team === state.health.my_team_id;
  byId("week-title").textContent = !week ? "Categories"
    : games.length ? `Week ${week.period} vs ${games.map((m) => m.team_name).join(" and ")}`
      : `Week ${week.period}${week.bye ? ": bye" : ""}`;
  const asOf = week && week.scores_as_of ? new Date(week.scores_as_of).toLocaleString([],
    { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : null;
  byId("week-sub").textContent = [
    week ? weekDates(week) : null,
    games.length ? (asOf ? `scores as of ${asOf}` : "no scores yet") : null,
    "Season: head-to-head record and league rank in each category",
  ].filter(Boolean).join(" · ");

  const name = (text) => h("span", { class: "team-name", title: text }, text);
  const head = [
    h("tr", { class: "band" }, h("th", {}),
      games.length ? h("th", { colspan: String(1 + 2 * games.length) }, "This week") : null,
      h("th", { colspan: "2" }, "Season")),
    h("tr", {}, h("th", {}, "Category"),
      games.length ? h("th", {}, name(mine ? "You" : ov.team_name)) : null,
      games.map((m) => h("th", { colspan: "2" }, name(m.team_name))),
      h("th", {}, "W-L-T"), h("th", { title: "League rank by season totals" }, "Rank")),
  ];
  const body = [];
  for (const [group, label] of [["SKATER", "Skaters"], ["GOALIE", "Goalies"]]) {
    const cats = ov.categories.filter((c) => c.group === group);
    if (!cats.length) continue;
    body.push(h("tr", { class: "group-row" }, h("td", { colspan: String(3 + 2 * games.length) }, label)));
    for (const c of cats) {
      const mineWon = games.length === 1 && (games[0].categories[c.category] || {}).outcome === "WIN";
      body.push(h("tr", {},
        h("td", {}, h("span", { class: "cat-name" }, c.category), h("span", { class: "cat-full" }, c.name)),
        games.length ? h("td", { class: "num" + (mineWon ? " won" : "") },
          fmtScore(c.category, (games.map((m) => m.categories[c.category]).find(Boolean) || {}).value)) : null,
        games.map((m) => {
          const s = m.categories[c.category];
          const [letter, cls, verb] = RESULT[s && s.outcome] || ["", "", ""];
          return [
            h("td", { class: "num" + (s && s.outcome === "LOSS" ? " won" : "") }, fmtScore(c.category, s && s.opponent_value)),
            h("td", { class: "result-cell" }, letter
              ? h("span", { class: "result " + cls, title: `${verb} ${c.name} vs ${m.team_name}` }, letter) : null),
          ];
        }),
        h("td", { class: "num" }, c.record),
        h("td", { class: "num" }, c.rank ? ordinal(c.rank) : "–"),
      ));
    }
  }
  if (games.some((m) => m.record)) {
    body.push(h("tr", { class: "total-row" }, h("td", {}, "Score"), h("td", {}),
      games.map((m) => { const s = weekScore(m); return h("td", { class: "num", colspan: "2" }, s ? s.text : "–"); }),
      h("td", {}), h("td", {})));
  }
  byId("week-table").replaceChildren(h("table", { class: "cat-table week-table" }, h("thead", {}, head), h("tbody", {}, body)));
}

// Fantrax standings. Click a team to see its dashboard.
function renderStandings(ov) {
  const opponents = new Set(((ov.week && ov.week.matchups) || []).map((m) => m.team_id));
  const show = (id) => {
    const sel = byId("team-select");
    sel.value = id;
    sel.dispatchEvent(new Event("change"));
  };
  const rows = state.teams.map((t) => h("tr", { class: t.id === state.team ? "me" : "" },
    h("td", { class: "num" }, t.rank != null ? t.rank : "–"),
    h("td", { class: "left" },
      t.id === state.team ? h("span", { class: "player-name" }, t.name)
        : h("button", { type: "button", class: "player-link", onclick: () => show(t.id) }, t.name),
      t.id === state.health.my_team_id ? h("span", { class: "badge" }, "you") : null,
      opponents.has(t.id) ? h("span", { class: "badge" }, "this week") : null),
    h("td", { class: "num" }, t.record || "–"),
    h("td", { class: "num" }, t.games_back ? fmtNum(t.games_back, 1) : "–")));
  byId("standings-table").replaceChildren(h("table", { class: "data-table standings" },
    h("thead", {}, h("tr", {}, h("th", {}, "#"), h("th", { class: "left" }, "Team"), h("th", {}, "W-L-T"),
      h("th", { title: "Games back" }, "GB"))),
    h("tbody", {}, rows)));
}

/* ================================================================ salary cap (Players & cap) */

function capTile(cap) {
  const over = cap.committed - cap.cap;
  return tile("Cap space", fmtMoney(cap.space), `of ${fmtMoney(cap.cap)}`, [
    over > 0 ? [overBy(`${fmtMoney(over)} over the cap`), " · "] : null,
    `${fmtMoney(cap.committed)} committed`,
    cap.unknown_salary.length ? ` · no salary for ${cap.unknown_salary.join(", ")}` : null,
  ], { error: over > 0 });
}

function yearsTile(cap) {
  const over = cap.contract_years - cap.contract_year_bank;
  return tile("Contract years", String(cap.contract_years), `of ${cap.contract_year_bank}`,
    over > 0 ? overBy(`${over} over the bank`) : `${-over} left in the bank`, { error: over > 0 });
}

// `holds`: RFA placeholders drawn as an unfilled, dotted segment after the committed fill.
function capRow(label, committed, cap, note, holds = 0) {
  const over = committed > cap;
  const pct = Math.min(committed / cap, 1) * 100;
  const holdPct = Math.min((holds / cap) * 100, 100 - pct);
  const row = h("div", { class: "cap-row", tabindex: "0" },
    h("span", { class: "cap-season" }, label),
    h("div", { class: "meter", role: "img", "aria-label": `${label}: ${fmtMoney(committed)} of ${fmtMoney(cap)}`
        + (holds ? `, plus ${fmtMoney(holds)} in RFA holds` : "") },
      h("div", { class: "meter-fill" + (over ? " over" : ""), style: { width: pct + "%" } }),
      holdPct > 0 ? h("div", { class: "meter-hold", style: { left: pct + "%", width: holdPct + "%" } }) : null),
    h("span", { class: "cap-figures num" },
      over ? h("span", { class: "down", "aria-hidden": "true" }, "▲ ") : null,
      `${fmtMoney(committed)} · `, h("strong", {}, over ? `${fmtMoney(committed - cap)} over` : `${fmtMoney(cap - committed)} space`)),
  );
  if (note) bindTip(row, () => tipContent(`${fmtMoney(cap - committed)} space`, label, note));
  return row;
}

let capTeam = null;   // the team whose cap is on screen
// Google Sheet publishing status for the team on screen (only teams whose contracts live in the app).
async function renderPublish() {
  const box = byId("publish-status");
  if (!isAdmin()) { box.replaceChildren(); return; }
  let st;
  try { st = await api.get("/publish"); } catch (_) { box.replaceChildren(); return; }
  const t = st.teams[capTeam];
  if (!t) { box.replaceChildren(); return; }
  if (!st.configured) {
    box.replaceChildren(h("span", { class: "sub" }, st.missing === "url"
      ? "Sheet publishing: deploy data/sheet_publisher.gs as a web app and set CFL_SHEET_PUBLISH_URL in .env"
      : "Sheet publishing isn't set up: run python -m cfl publish --script"));
    return;
  }
  const last = st.last_run;
  const failed = last && last.status === "error";
  const button = h("button", { class: "btn small ghost", type: "button" }, "Publish now");
  button.addEventListener("click", async () => {
    button.disabled = true;
    button.textContent = "Publishing…";
    try { await request("POST", "/publish", { params: { force: true } }); } catch (err) { showError(err); }
    renderPublish();
  });
  box.replaceChildren(
    h("a", { href: t.sheet_url, target: "_blank", rel: "noopener" }, "Google Sheet"),
    h("span", { class: failed ? "warn-text" : "sub" }, failed
      ? ` · last publish failed: ${(last.detail && last.detail.error) || "unknown error"}`
      : ` · ${t.published_at ? "published " + new Date(t.published_at).toLocaleString() : "not published yet"}`),
    button);
}

// Cap space and contract-year tiles, then this season and the next four. `cap` is null for "All
// teams", or a team with no salary sheet.
function renderCap(cap, teamId) {
  capTeam = teamId;
  byId("players-kpis").hidden = byId("cap-card").hidden = !cap;
  if (!cap) return;
  byId("players-kpis").replaceChildren(capTile(cap), yearsTile(cap));
  renderPublish();
  const parts = [`Cap ${fmtMoney(cap.cap)}. Active and bench salaries count, plus buyouts; minors and IR don't`];
  if (cap.ir_relief) parts.push(`${fmtMoney(cap.ir_relief)} IR relief right now`);
  byId("cap-sub").textContent = parts.join(" · ");
  const now = [["Committed", fmtMoney(cap.committed)], ["IR relief", fmtMoney(cap.ir_relief)],
    ["Buyouts", fmtMoney(cap.dead_cap)]];
  if (cap.unknown_salary.length) now.push(["No salary on sheet", cap.unknown_salary.join(", ")]);
  byId("cap-outlook").replaceChildren(
    capRow(`${cap.season} (now)`, cap.committed, cap.cap, now),
    ...cap.future.map((f) => capRow(f.season, f.committed, cap.cap, [
      ["Already committed", fmtMoney(f.committed)], ["Includes", "every contract still running that season"],
      ...(f.rfa_holds ? [
        ["RFA holds", `${fmtMoney(f.rfa_holds)} (dotted)`],
        ["Space after holds", fmtMoney(cap.cap - f.committed - f.rfa_holds)],
        ...f.rfa_players.map((x) => [x.name, `${fmtMoney(x.salary)} · RFA '${x.after_season.slice(-2)}`]),
      ] : []),
    ], f.rfa_holds)),
    ...(cap.future.some((f) => f.rfa_holds) ? [h("p", { class: "market-note" },
      h("span", { class: "hold-swatch", "aria-hidden": "true" }),
      "RFA holds: players whose contracts end while they're RFAs, carried into every later season at their " +
      "final salary. Not counted in committed or space.")] : []),
    h("p", { class: "market-note" },
      `${cap.contract_years} of ${cap.contract_year_bank} contract years used (seasons left on each contract; minors and IR excluded).` +
      (cap.unknown_salary.length ? ` Unknown salaries aren't included: ${cap.unknown_salary.join(", ")}.` : "")),
  );
}

// Player, age, this NHL season's games and totals in each league category, then the contract.
// Skaters and goalies have different categories, so each position group labels its own stat
// columns; short lists are padded so the contract columns line up.
function rosterColumns(ov, groupKey) {
  const of = (grp) => ov.categories.filter((c) => c.group === grp);
  const slots = Math.max(of("SKATER").length, of("GOALIE").length);
  const stats = of(groupKey === "G" ? "GOALIE" : "SKATER").map((c) => ({
    key: `stat:${c.category}`, label: c.category, title: c.name, asc: c.lower_is_better,
    value: (p) => (p.season ? p.season[c.category] : null),
    render: (p) => (p.season ? fmtScore(c.category, p.season[c.category]) : "–") }));
  while (stats.length < slots) stats.push({ key: `pad${stats.length}`, label: "", sortable: false, value: () => null, render: () => "" });
  return [
    { key: "name", label: "Player", left: true, value: (p) => p.name },
    { key: "age", label: "Age", value: (p) => p.age, render: (p) => (p.age != null ? p.age : "–") },
    { key: "gp", label: "GP", title: "Games played this NHL season", value: (p) => p.gp_this_season },
    ...stats,
    { key: "salary", label: "Salary", value: (p) => p.salary, render: (p) => fmtMoney(p.salary) },
    { key: "contract_years", label: "Yrs", value: (p) => p.contract_years, render: (p) => (p.contract_years || "–") },
    { key: "contract_end", label: "At expiry", asc: true, value: endSortKey, render: endPill },
  ];
}

function renderRoster(ov) {
  const y = state.league ? state.league.season_year : null;
  byId("roster-sub").textContent = `${y ? `${y}-${String(y + 1).slice(2)} ` : ""}NHL stats. ` +
    "Click a column to sort, or a player for his contract.";
  segmented(byId("roster-view"), [["roster", "Roster"], ["prospects", "Prospects"], ["all", "All"]],
    state.rosterView, (v) => { state.rosterView = v; renderRoster(ov); });
  byId("roster-table").replaceChildren(sectionedTable({
    players: ov.roster, groupColumns: (key) => rosterColumns(ov, key), view: state.rosterView,
    sort: state.rosterSort, onSort: (next) => { state.rosterSort = next; renderRoster(ov); },
    empty: state.rosterView === "prospects" ? "No prospects in the minors." : "No players.",
  }));
}

/* ================================================================ analysis */

// Projections, need weights and market values: the numbers behind the trade builder.

// What each number means, in plain words: the glossary at the top of the tab, and the hint on
// each tile label and column header (hover or focus; marked with a dotted underline).
const GLOSSARY = {
  ecw: ["Expected category wins per week", "How many categories the team would win in a typical week, " +
    "averaged over every opponent, from its projected lineups. Half of them (6 of 12) is an average team."],
  strength: ["Projected strength", "The team's rank when its projected strength in every category is added up (1 = strongest)."],
  skater_games: ["Skater games per week", "Skater games the team is projected to fill each week by setting its best " +
    "available players every day. Bench players count on nights the regulars sit. More games, more counting stats."],
  goalie_starts: ["Goalie starts per week", "Starts the team's goalies are projected to make each week, from how " +
    "often each one starts for his NHL team."],
  sd: ["vs league average", "How far the team's projected weekly total is above (blue) or below (red) the league " +
    "average, in standard deviations (sd). About 1 sd is a clear strength or weakness; 2 is extreme."],
  proj: ["Proj / wk", "The team's projected total in the category per week. GAA and SV% are averages."],
  rank: ["Rank", "Projected rank in the category among all teams (1 = best). In League at a glance, the color " +
    "shows the same distance from average as the bars."],
  actual: ["Actual", "The team's real head-to-head record in the category this season: wins, losses and ties, one per matchup."],
  need: ["Need", "How much one more unit of the category is worth to the team, from the Need weighting setting. " +
    "1.00 is average; dark pills (1.15 and up) are the categories worth targeting."],
  games: ["Games / wk", "Games per week the player is projected to spend in the team's lineup, with the best " +
    "lineup set every day. For goalies, projected starts."],
  value: ["Value", "The player's projected per-game production in every category, counted equally and compared " +
    "with the players good enough to start in this league. 0 is an average starter; higher is better."],
  need_value: ["Need value", "Value with each category weighted by the team's Need. Higher than Value means the " +
    "player is strong where the team needs help."],
  trade_rank: ["Trade rank", "The player's rank league-wide by Value (#1 = most valuable). Fewer goalies start, so " +
    "goalies are spaced out on the same scale: the best goalie ranks about #9."],
  market: ["Market", "Trade value: how far the player is above the last starter in the league. Bench-level " +
    "players are worth about 0, so a package's values add up without two depth players matching a star."],
  gp: ["GP (this/last)", "NHL games played this season and last. With few games, the projection leans on last " +
    "season and the league average."],
  best: ["Best categories", "The player's three strongest categories, in standard deviations above an average starter."],
  marginal: ["Swing categories", "Weighs the categories where several teams are close to this one: one more goal " +
    "or hit there flips a weekly matchup. Categories it dominates, or trails badly in, count less."],
  weakness: ["Weakest categories", "Weighs the team's lowest-ranked categories most."],
  even: ["Even", "Every category counts the same."],
};
const GLOSSARY_GROUPS = [
  ["Team projections", ["ecw", "strength", "skater_games", "goalie_starts", "sd", "proj", "rank", "actual", "need"]],
  ["Player values", ["games", "value", "need_value", "trade_rank", "market", "gp", "best"]],
  ["Need weighting", ["marginal", "weakness", "even"]],
];

function withHelp(el, key) {
  const [term, text] = GLOSSARY[key];
  el.classList.add("has-help");
  if (!el.hasAttribute("tabindex")) el.setAttribute("tabindex", "0");
  bindTip(el, () => tipContent(term, text));
  return el;
}

function renderGlossary() {
  const box = byId("glossary");
  if (box.dataset.ready) return;
  box.dataset.ready = "1";
  box.open = prefs.get("glossary", "") === "open";
  box.addEventListener("toggle", () => prefs.set("glossary", box.open ? "open" : "closed"));
  byId("glossary-body").replaceChildren(
    h("p", { class: "glossary-intro" }, "Projections blend each player's per-game stats from this season and last " +
      "(this season counts for more as games pile up), then set the team's best lineup every day for the rest of " +
      "the NHL schedule. Hover over any label with a dotted underline to see its meaning there."),
    h("div", { class: "glossary-groups" }, GLOSSARY_GROUPS.map(([title, keys]) => h("section", {},
      h("h3", {}, title),
      h("dl", {}, keys.map((k) => [h("dt", {}, GLOSSARY[k][0]), h("dd", {}, GLOSSARY[k][1])]))))));
}

async function loadAnalysis() {
  const view = byId("view-analysis");
  const token = ++seqs.analysis;
  renderGlossary();
  byId("strategy-hint").textContent = GLOSSARY[state.strategy][1];
  view.classList.add("is-loading");
  try {
    const [profile, league] = await Promise.all([getProfile(state.team), getLeagueAnalysis()]);
    if (token !== seqs.analysis) return;
    renderAnalysisKpis(profile);
    renderCategoryProfile(profile);
    renderHeatmap(league);
    renderPlayerValues(profile);
    clearError();
  } catch (err) {
    if (token === seqs.analysis) showError(err);
  } finally {
    if (token === seqs.analysis) view.classList.remove("is-loading");
  }
}

function renderAnalysisKpis(profile) {
  const skaterGames = profile.roster.filter((p) => p.position !== "G")
    .reduce((s, p) => s + (p.lineup_games_per_week || 0), 0);
  const goalieStarts = profile.roster.reduce((s, p) => s + (p.expected_starts_per_week || 0), 0);
  byId("analysis-kpis").replaceChildren(
    tile("Expected category wins per week", fmtNum(profile.expected_category_wins, 2), `of ${profile.categories.length}`,
      "Against an average opponent, from projected rest-of-season lineups", { hero: true, help: "ecw" }),
    tile("Projected strength", ordinal(profile.projected_overall_rank), `of ${state.teams.length}`,
      "Total category strength across the league", { help: "strength" }),
    tile("Skater games per week", fmtNum(skaterGames, 1), null, "Lineup games, including bench fill-ins",
      { help: "skater_games" }),
    tile("Goalie starts per week", fmtNum(goalieStarts, 2), null, "Expected starts across all the team's goalies",
      { help: "goalie_starts" }));
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
        h("th", {}, "Category"), withHelp(h("th", { style: { textAlign: "center" } }, "vs league average"), "sd"),
        withHelp(h("th", {}, "Proj / wk"), "proj"), withHelp(h("th", {}, "Rank"), "rank"),
        withHelp(h("th", {}, "Actual"), "actual"), withHelp(h("th", {}, "Need"), "need"))),
      h("tbody", {}, body))),
  );
}

function heatStyle(z) {
  if (z == null) return { background: "var(--wash)" };
  const t = Math.max(-1, Math.min(1, z / 2));
  const pct = Math.round(Math.abs(t) * 100);
  const pole = t >= 0 ? "var(--div-pos)" : "var(--div-neg)";
  return { background: `color-mix(in oklab, ${pole} ${pct}%, var(--div-mid))`, color: pct > 55 ? "#ffffff" : "var(--ink)" };
}

function renderHeatmap(overview) {
  const cats = (state.league && state.league.categories || []).map((c) => c.short_name);
  const names = Object.fromEntries((state.league && state.league.categories || []).map((c) => [c.short_name, c.name]));
  const head = h("tr", {}, h("th", { class: "team" }, "Team"),
    cats.map((c) => h("th", { title: names[c] || c }, c)), withHelp(h("th", {}, "Exp. W"), "ecw"));
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

const VALUE_COLUMNS = [
  { key: "name", label: "Player", left: true, value: (p) => p.name },
  { key: "lineup_games_per_week", label: "Games / wk", help: "games", value: (p) => p.lineup_games_per_week,
    render: (p) => p.expected_starts_per_week != null
      ? `${fmtNum(p.expected_starts_per_week, 2)} starts` : fmtNum(p.lineup_games_per_week, 1) },
  { key: "value", label: "Value", help: "value", value: (p) => p.value, render: (p) => fmtNum(p.value, 2) },
  { key: "need_value", label: "Need value", help: "need_value", value: (p) => p.need_value,
    render: (p) => fmtNum(p.need_value, 2) },
  { key: "trade_rank", label: "Trade rank", help: "trade_rank", asc: true, value: (p) => p.trade_rank,
    render: (p) => (p.trade_rank ? `#${p.trade_rank}` : "–") },
  { key: "market_value", label: "Market", help: "market", value: (p) => p.market_value,
    render: (p) => fmtNum(p.market_value, 1) },
  { key: "salary", label: "Salary", value: (p) => p.salary, render: (p) => fmtMoney(p.salary) },
  { key: "gp_this_season", label: "GP (this/last)", help: "gp", value: (p) => p.gp_this_season,
    render: (p) => `${p.gp_this_season}/${p.gp_last_season}` },
  { key: "best", label: "Best categories", help: "best", left: true, cls: "cats-inline", sortable: false,
    render: (p) => bestCats(p.z) },
];

function renderPlayerValues(profile) {
  segmented(byId("analysis-view"), [["roster", "Roster"], ["prospects", "Prospects"], ["all", "All"]],
    state.analysisView, (v) => { state.analysisView = v; renderPlayerValues(profile); });
  byId("analysis-table").replaceChildren(sectionedTable({
    players: profile.roster, columns: VALUE_COLUMNS, view: state.analysisView,
    sort: state.analysisSort, onSort: (next) => { state.analysisSort = next; renderPlayerValues(profile); },
    empty: state.analysisView === "prospects" ? "No prospects in the minors." : "No players.",
  }));
}

/* ================================================================ sectioned player tables */

const POSITION_GROUPS = [
  { key: "F", label: "Forwards", sub: "C / LW / RW", slots: ["C", "LW", "RW", "F"] },
  { key: "D", label: "Defense", slots: ["D"] },
  { key: "G", label: "Goalies", slots: ["G"] },
];
// Roster players count against the 25-man roster (active, bench, IR); prospects sit in the minors.
const SEGMENTS = [
  { key: "roster", label: "Roster", sub: "active, bench and IR", test: (p) => p.fantasy_team_id && p.roster_status !== "MINORS" },
  { key: "prospects", label: "Prospects", sub: "in the minors", test: (p) => p.roster_status === "MINORS" },
  { key: "fa", label: "Free agents", test: (p) => !p.fantasy_team_id },
];
const STATUS_PILL = {
  ACTIVE: ["Active", "active"], RESERVE: ["Bench", "bench"], INJURED_RESERVE: ["IR", "ir"], MINORS: ["Prospect", "prospect"],
};

function posGroup(p) {
  if (p.position === "G") return "G";
  return p.position === "D" ? "D" : "F";
}

function statusPill(p) {
  if (!p.fantasy_team_id) return h("span", { class: "pill fa" }, "Free agent");
  const [label, cls] = STATUS_PILL[p.roster_status] || [p.roster_status || "–", ""];
  return h("span", { class: "pill " + cls }, label);
}

function lineupSlots(group) {
  const constraints = (state.league && state.league.roster_info && state.league.roster_info.positionConstraints) || {};
  return group.slots.reduce((n, pos) => n + ((constraints[pos] && constraints[pos].maxActive) || 0), 0);
}

function segmented(el, options, value, onChange) {
  el.replaceChildren(...options.map(([v, label]) => h("button", {
    type: "button", "aria-pressed": String(v === value), onclick: () => { if (v !== value) onChange(v); },
  }, label)));
}

function playerCell(p) {
  return h("td", { class: "left" }, playerLink(p), statusPill(p),
    h("div", { class: "sub" }, [positions(p), p.nhl_team].filter(Boolean).join(" · ")));
}

// One table, with a section per roster segment (or the given `segments`) and position group so
// columns line up across sections. Sorting applies within each section. `salary` is the amount
// each section totals. With `groupColumns(groupKey)`, each position group has its own columns
// (all the same length), labelled under its group heading instead of once at the top; a group
// without the sorted column keeps the order it was given.
function sectionedTable({ players, columns, view, sort, onSort, empty, oneTeam = true, segments = SEGMENTS,
  salary = (p) => p.salary, groupColumns = null }) {
  const colsFor = (group) => (groupColumns ? groupColumns(group.key) : columns);
  const width = colsFor(POSITION_GROUPS[0]).length;
  const comparator = (cols) => {
    const col = cols.find((c) => c.key === sort.key) || (groupColumns ? null : cols[0]);
    return col && ((a, b) => {
      const va = col.value(a), vb = col.value(b);
      if (va == null && vb == null) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;
      return (typeof va === "string" ? va.localeCompare(vb) : va - vb) * sort.dir;
    });
  };
  const labels = (cols) => h("tr", { class: "labels" }, cols.map((c) => {
    const sortable = c.sortable !== false;
    const th = h("th", {
      class: (c.left ? "left " : "") + (sortable ? "sortable" : ""),
      "aria-sort": c.key === sort.key ? (sort.dir === 1 ? "ascending" : "descending") : null,
      tabindex: sortable ? "0" : null, title: c.title,
    }, c.label);
    if (c.help) withHelp(th, c.help);
    if (sortable) {
      const go = () => onSort(c.key === sort.key ? { key: sort.key, dir: -sort.dir }
        : { key: c.key, dir: c.asc || c.key === "name" ? 1 : -1 });
      th.addEventListener("click", go);
      th.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
    }
    return th;
  }));
  const money = (list) => list.reduce((sum, p) => sum + (salary(p) || 0), 0);
  const bodies = [];
  for (const seg of segments.filter((x) => view === "all" || x.key === view)) {
    const inSeg = players.filter(seg.test);
    if (!inSeg.length) continue;
    bodies.push(h("tbody", { class: "seg" }, h("tr", { class: "seg-row" }, h("th", { colspan: String(width) },
      seg.label, seg.sub ? h("span", { class: "seg-sub" }, ` · ${seg.sub}`) : null,
      h("span", { class: "seg-meta" }, `${inSeg.length} player${inSeg.length === 1 ? "" : "s"}` +
        (money(inSeg) ? ` · ${fmtMoney(money(inSeg))} salary` : ""))))));
    for (const group of POSITION_GROUPS) {
      const cols = colsFor(group);
      const cmp = comparator(cols);
      const list = inSeg.filter((p) => posGroup(p) === group.key);
      if (!list.length) continue;
      if (cmp) list.sort(cmp);
      const meta = [`${list.length}`];
      if (oneTeam && seg.key === "roster" && lineupSlots(group)) meta.push(`${lineupSlots(group)} lineup slots`);
      if (money(list)) meta.push(`${fmtMoney(money(list))}`);
      bodies.push(h("tbody", { class: "group" },
        h("tr", { class: "group-row" }, h("th", { colspan: String(width) },
          group.label, group.sub ? h("span", { class: "seg-sub" }, ` ${group.sub}`) : null,
          h("span", { class: "seg-meta" }, meta.join(" · ")))),
        groupColumns ? labels(cols) : null,
        list.map((p) => h("tr", {}, cols.map((c) => (c.key === "name" ? playerCell(p)
          : h("td", { class: c.left ? "left" + (c.cls ? " " + c.cls : "") : "num" }, c.render ? c.render(p) : c.value(p))))))));
    }
  }
  if (!bodies.length) return h("div", { class: "empty" }, empty || "No players.");
  return h("table", { class: "data-table sectioned" }, groupColumns ? null : h("thead", {}, labels(columns)), ...bodies);
}

/* ================================================================ trade builder */

let myRoster = [];
let partnerRoster = [];

async function loadTrade() {
  const partnerSel = byId("partner-select");
  partnerSel.replaceChildren(
    h("option", { value: "" }, "No partner: price my players"),
    h("option", { value: WAIVERS, selected: onWaivers() }, "Waiver wire: free agents"),
    ...state.teams.filter((t) => t.id !== state.team).map((t) =>
      h("option", { value: t.id, selected: t.id === state.trade.partner }, t.name)));
  const waivers = onWaivers();
  byId("give-title").textContent = `${teamName(state.team)} ${waivers ? "drops" : "gives"}`;
  byId("give-sub").textContent = waivers ? "Select players to release" : "Select players to send";
  byId("tolerance-field").hidden = waivers;
  const token = ++seqs.tradeLoad;
  try {
    const [mine, theirs] = await Promise.all([
      getProfile(state.team),
      state.trade.partner && !waivers ? getProfile(state.trade.partner) : Promise.resolve(null),
      waivers ? loadWaiverPool({ render: false }) : Promise.resolve(),
    ]);
    if (token !== seqs.tradeLoad) return;
    myRoster = mine.roster;
    if (!waivers) partnerRoster = theirs ? theirs.roster : [];
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
  state.trade.pickups.clear();
  loadTrade();
}

// Selected pickups first, then the rest; each by trade rank.
function orderPool(players) {
  const picked = state.trade.pickups;
  const byRank = (a, b) => (a.trade_rank || 1e9) - (b.trade_rank || 1e9);
  return [...[...picked.values()].sort(byRank), ...players.filter((p) => !picked.has(p.fantrax_id)).sort(byRank)];
}

// The free agents listed on the waiver wire: the best by value, or matches for the search.
// Selected pickups stay listed whatever the search.
async function loadWaiverPool({ render = true } = {}) {
  const token = ++seqs.pool;
  const q = state.trade.getQuery.trim();
  try {
    const res = await api.get("/players", { status: "fa", q: q || null, min_games: q ? 0 : 5, limit: 40 });
    if (token !== seqs.pool || !onWaivers()) return;
    partnerRoster = orderPool(res.players);
  } catch (err) {
    if (token === seqs.pool) showError(err);
    return;
  }
  if (render) renderTradeLists();
}

function playerRow(p, selectedSet, onToggle) {
  const selected = selectedSet.has(p.fantrax_id);
  const games = p.expected_starts_per_week != null ? `${fmtNum(p.expected_starts_per_week, 2)} starts/wk`
    : p.lineup_games_per_week != null ? `${fmtNum(p.lineup_games_per_week, 1)} g/wk` : null;
  const box = h("input", { type: "checkbox", checked: selected, "aria-label": `Select ${p.name}` });
  const row = h("label", { class: "prow" + (selected ? " selected" : "") },
    box,
    h("div", { class: "prow-main" },
      h("div", { class: "prow-name" }, p.name,
        statusPill(p)),
      h("div", { class: "prow-meta" }, [positions(p), p.nhl_team, p.z && Object.keys(p.z).length ? bestCats(p.z, 2) : "no NHL stats"]
        .filter(Boolean).join(" · "))),
    h("div", { class: "prow-stats" },
      h("div", {}, h("strong", {}, p.trade_rank ? `#${p.trade_rank}` : "–"), ` · mv ${fmtNum(p.market_value, 1)}`),
      h("div", {}, [games, fmtContract(p)].filter(Boolean).join(" · "))));
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
  const waivers = onWaivers();
  byId("get-panel").hidden = !hasPartner;
  byId("get-minors-label").hidden = waivers;
  byId("get-search").placeholder = waivers ? "Search free agents" : "Filter players";
  byId("get-title").textContent = waivers ? "You pick up" : hasPartner ? `${teamName(t.partner)} gives` : "You get";
  byId("get-sub").textContent = waivers ? "Free agents: select players to add, or use the suggestions"
    : hasPartner ? "Select players to receive" : "Pick a trade partner above, or add one of the suggestions";
  if (hasPartner) {
    const pick = toggle(t.get);
    const onToggle = waivers ? (id, on) => {
      if (on) t.pickups.set(id, partnerRoster.find((p) => p.fantrax_id === id));
      else t.pickups.delete(id);
      pick(id, on);
    } : pick;
    // The server already searched the free agents; only a trade partner's roster is filtered here.
    const get = waivers ? partnerRoster : filteredList(partnerRoster, t.getQuery, t.getMinors, t.get);
    byId("get-list").replaceChildren(...(get.length
      ? get.map((p) => playerRow(p, t.get, onToggle)) : [h("div", { class: "empty" }, "No players match")]));
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
  if (onWaivers()) {
    loadPickupSuggestions(box, token);
    return;
  }
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
          h("div", { class: "prow-meta" }, [positions(p), p.nhl_team, `#${p.trade_rank}`, fmtContract(p),
            t.partner ? null : p.fantasy_team].filter(Boolean).join(" · "))),
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

// Free agents ranked by the simulated gain of picking them up. With no drop selected, the best
// drop is found for each (unless there's an open roster spot).
async function loadPickupSuggestions(box, token) {
  const t = state.trade;
  box.classList.add("is-loading");
  try {
    const res = await api.get(`/analysis/teams/${encodeURIComponent(state.team)}/targets`, {
      source: "fa", drop: [...t.give].join(",") || null, limit: 8,
    });
    if (token !== seqs.suggest) return;
    const items = res.targets.map((p) => {
      const added = t.get.has(p.fantrax_id);
      const add = h("button", { class: "btn small ghost", type: "button" }, added ? "Added" : "Add");
      add.disabled = added;
      add.addEventListener("click", () => addPickup(p));
      const drops = p.drops.map((d) => d.name + (d.release_cost ? ` (${fmtMoney(d.release_cost)} buyout)` : ""));
      return h("div", { class: "sug" },
        h("div", { style: { minWidth: "0" } },
          h("div", { class: "prow-name" }, p.name),
          h("div", { class: "prow-meta" }, [positions(p), p.nhl_team, p.trade_rank ? `#${p.trade_rank}` : null,
            `mv ${fmtNum(p.market_value, 1)}`].filter(Boolean).join(" · ")),
          t.give.size ? null : h("div", { class: "prow-meta" },
            drops.length ? `Drop ${drops.join(", ")}` : "Fills an open roster spot")),
        h("div", { class: "gain" },
          h("div", {}, "You ", dirMark(p.your_gain), " ", fmtSigned(p.your_gain)),
          h("div", {}, "mv ", fmtSigned(p.market_value_net, 1))),
        add);
    });
    const r = res.roster;
    const heading = res.drop ? `Best pickups if you drop ${res.drop.name}`
      : r.cap && r.count < r.cap ? `Best pickups (${r.cap - r.count} open roster spot${r.cap - r.count === 1 ? "" : "s"})`
        : "Best pickups, each with its best drop";
    box.replaceChildren(h("div", { class: "suggestions" },
      h("h3", {}, heading),
      h("p", { class: "card-sub", style: { marginBottom: "6px" } },
        "Gains are the change in expected category wins per week, simulated with daily lineups. mv is market value gained (pickup minus drop)."),
      items.length ? items : h("div", { class: "empty" }, "No free agents with enough NHL games.")));
  } catch (err) {
    if (token === seqs.suggest) box.replaceChildren(h("div", { class: "empty" }, err.message));
  } finally {
    if (token === seqs.suggest) box.classList.remove("is-loading");
  }
}

// Adds a suggested pickup. With no drop chosen yet, the suggestion's drop comes along, so the
// evaluation below matches the suggestion.
function addPickup(p) {
  const t = state.trade;
  t.get.add(p.fantrax_id);
  t.pickups.set(p.fantrax_id, p);
  if (!t.give.size) p.drops.forEach((d) => t.give.add(d.fantrax_id));
  partnerRoster = orderPool(partnerRoster);
  renderTradeLists();
  refreshSuggestions();
  refreshEvaluation();
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
      team_id: state.team, give: [...t.give], other_team_id: t.partner && !onWaivers() ? t.partner : null,
      get: [...t.get],
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
  const names = (players) => players.map((p) => p.name).join(" + ");
  if (!them && me.gets.length) {
    const move = `Pick up ${names(me.gets)}${me.gives.length ? `, drop ${names(me.gives)}` : ""}`;
    return {
      title: dMe > 0.005 ? "Worth the pickup" : dMe < -0.005 ? "Makes you weaker" : "Roughly neutral",
      sub: `${move}: ${fmtSigned(dMe)} expected category wins per week for ${me.team_name}.`,
    };
  }
  if (!them) {
    return {
      title: `Cost of losing ${names(me.gives)}`,
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
  const pickup = !res.sides[1] && res.sides[0].gets.length > 0;
  const row = (label, value, rank, cls) => h("div", { class: "market-row" },
    h("span", {}, label),
    h("div", { class: "market-track" }, h("div", { class: "market-fill " + cls, style: { width: (value / max) * 100 + "%" } })),
    h("span", { class: "num" }, `${fmtNum(value, 1)}${rank ? ` · ≈ #${rank}` : ""}`));
  const diff = get - give;
  let note;
  if (pickup && Math.abs(diff) <= 0.5) note = "Even on market value.";
  else if (pickup && diff > 0) note = `You add ${fmtNum(diff, 1)} more market value than you drop.`;
  else if (pickup) note = `You drop ${fmtNum(-diff, 1)} more market value than you add: that may fetch more in a trade.`;
  else if (!res.sides[1]) note = "Market value is value over a replacement-level starter. Pick a partner to compare both sides.";
  else if (Math.abs(diff) <= Math.max(0.5, 0.15 * max)) note = "Even on market value: a fair ask.";
  else if (diff > 0) note = `You get ${fmtNum(diff, 1)} more market value; expect pushback.`;
  else note = `You pay a ${fmtNum(-diff, 1)} premium in market value.`;
  return h("div", { class: "card market" },
    h("h3", {}, "Market value"),
    row(pickup ? "You drop" : "You give", give, m.you_give_equiv_rank, "give"),
    res.sides[1] || pickup ? row(pickup ? "You add" : "You get", get, m.you_get_equiv_rank, "get") : null,
    h("p", { class: "market-note" }, note));
}

function sideCard(side, maxDz, waiverMove) {
  const move = waiverMove ? "move" : "trade";
  const ecw = side.expected_category_wins;
  const d = direction(ecw.delta);
  const r = side.roster;
  const facts = [
    ["Roster (cap)", `${r.before} → ${r.after}${r.cap ? ` / ${r.cap}` : ""}`],
    ["Skater games / wk", `${fmtNum(side.volume.before.skater_games, 1)} → ${fmtNum(side.volume.after.skater_games, 1)}`],
    ["Goalie starts / wk", `${fmtNum(side.volume.before.goalie_starts, 2)} → ${fmtNum(side.volume.after.goalie_starts, 2)}`],
    ["Market value", fmtSigned(side.market_value.net, 1)],
  ];
  const cap = side.cap;
  if (cap) {
    facts.push(["Cap space", `${fmtMoney(cap.space_before)} → ${fmtMoney(cap.space_after)}${cap.unknown_incoming.length ? "*" : ""}`]);
    facts.push(["Salary out / in", `${fmtMoney(cap.salary_out)} / ${fmtMoney(cap.salary_in)}${cap.unknown_incoming.length ? "*" : ""}`]);
    facts.push(["Contract years", `${cap.contract_years_before} → ${cap.contract_years_after} / ${cap.contract_year_bank}`]);
  } else {
    facts.push(["Cap space", "no salary sheet"]);
  }
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
  if (cap && cap.release_buyouts) notes.push(`Releasing $4+ contracts costs ${fmtMoney(cap.release_buyouts)} in buyouts (half salary, for the rest of each contract).`);
  if (cap && cap.drop_buyouts) notes.push(`Cutting ${side.auto_dropped.map((p) => p.name).join(", ")} costs a ${fmtMoney(cap.drop_buyouts)} buyout (half salary, $4+ contracts).`);
  if (cap && cap.unknown_incoming.length) {
    notes.push(waiverMove
      ? `* No salary set for ${cap.unknown_incoming.join(", ")}, so cap space after leaves it out. Set the pickup's contract in the Players & cap tab to include it.`
      : `* Salary unknown for ${cap.unknown_incoming.join(", ")}: their team's sheet isn't loaded, so cap space after is overstated.`);
  }
  if (cap && cap.over_cap) notes.push(`▲ Over the cap by ${fmtMoney(-cap.space_after)} after this ${move}.`);
  if (cap && cap.contract_years_after > cap.contract_year_bank) {
    notes.push(`▲ ${cap.contract_years_after - cap.contract_year_bank} contract years over the ${cap.contract_year_bank}-year bank after this ${move}.`);
  }
  return h("article", { class: "card side" },
    h("div", { class: "side-team" }, side.role === "you" ? "You" : "Trade partner", " · ", side.team_name),
    h("div", { class: "side-hero" },
      h("span", { class: "icon " + d, "aria-hidden": "true" }, ICON[d]),
      h("span", { class: "big" }, fmtSigned(ecw.delta)),
    ),
    h("div", { class: "side-caption" },
      `Expected category wins per week: ${fmtNum(ecw.before, 2)} → ${fmtNum(ecw.after, 2)}`),
    h("div", { class: "moves" },
      side.gives.map((p) => h("span", { class: "chip out" }, p.name, p.salary != null ? ` ${fmtMoney(p.salary)}` : "")),
      side.gets.map((p) => h("span", { class: "chip in" }, p.name, p.salary != null ? ` ${fmtMoney(p.salary)}` : "")),
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
    h("div", { class: "sides" }, res.sides.map((s) => sideCard(s, maxDz, !res.sides[1] && s.gets.length > 0))));
}

/* ================================================================ players + contract editor */

const TYPE_SHORT = { STANDARD: null, ELC: "ELC", IN_SEASON_UFA: "In-season UFA" };

function playerLink(p) {
  return h("button", { type: "button", class: "player-link", onclick: () => openPlayer(p.fantrax_id),
    title: "Edit contract" }, p.name);
}

const PLAYER_COLUMNS = [
  { key: "name", label: "Player", left: true, value: (p) => p.name },
  { key: "fantasy_team", label: "Team", left: true, value: (p) => p.fantasy_team || "", render: (p) => p.fantasy_team || "–",
    leagueOnly: true },
  { key: "age", label: "Age", value: (p) => p.age, render: (p) => (p.age != null ? p.age : "–") },
  { key: "contract_type", label: "Type", left: true, value: (p) => TYPE_SHORT[p.contract_type] || "",
    render: (p) => TYPE_SHORT[p.contract_type] || "" },
];
const CONTRACT_SEASONS = 5;   // season columns, sync.SHEET_SEASONS
// A salary for each season under contract, then the RFA/UFA pill in the first season after it ends.
const seasonColumns = (count) => Array.from({ length: count }, (_, i) => {
  const y = state.league.season_year + i;
  return { key: `season${i}`, label: `${y}-${String(y + 1).slice(2)}`, value: (p) => p.contract_salaries[i],
    render: (p) => (i < p.contract_salaries.length ? fmtMoney(p.contract_salaries[i])
      : i === p.contract_salaries.length ? endPill(p) : null) };
});

let playersSeq = 0;
async function loadPlayers() {
  // Its own team choice, separate from the Dashboard's: it starts on your team and stays where you leave it.
  if (state.playersTeam === undefined) state.playersTeam = state.health.my_team_id || state.team;
  const teamSel = byId("players-team");
  teamSel.replaceChildren(
    h("option", { value: "" }, "All teams"),
    ...state.teams.map((t) => h("option", { value: t.id, selected: t.id === state.playersTeam }, t.name)));
  const q = byId("players-search").value.trim();
  const missingOnly = byId("players-missing").checked;
  const team = state.playersTeam;
  const out = byId("players-table");
  const views = [["all", "All"], ["roster", "Roster"], ["prospects", "Prospects"]];
  if (!team) views.push(["fa", "Free agents"]);
  if (team && state.playersView === "fa") state.playersView = "all";
  segmented(byId("players-view"), views, state.playersView, (v) => { state.playersView = v; loadPlayers(); });
  loadPicks();
  if (!team) renderCap(null, null);
  if (!team && q.length < 2) {
    byId("players-title").textContent = "Players";
    out.replaceChildren(h("div", { class: "empty" }, "Type at least two letters of a name, or pick a team."));
    return;
  }
  const token = ++playersSeq;
  out.classList.add("is-loading");
  try {
    const res = await api.get("/players", { q: q || null, team_id: team || null, limit: team ? 120 : 60 });
    if (token !== playersSeq) return;
    let players = res.players;
    if (missingOnly) players = players.filter((p) => p.salary == null && p.roster_status !== "MINORS");
    byId("players-title").textContent = team ? teamName(team) : `${players.length} matches`;
    if (team) renderCap(res.cap, team);
    out.replaceChildren(sectionedTable({
      players, columns: [...PLAYER_COLUMNS.filter((c) => !(team && c.leagueOnly)), ...seasonColumns(CONTRACT_SEASONS + 1)],
      view: state.playersView, oneTeam: Boolean(team),
      sort: state.playersSort, onSort: (next) => { state.playersSort = next; loadPlayers(); },
      empty: missingOnly ? "Everyone here has a salary." : "No players found.",
    }));
  } catch (err) {
    if (token === playersSeq) out.replaceChildren(h("div", { class: "empty" }, err.message));
  } finally {
    if (token === playersSeq) out.classList.remove("is-loading");
  }
}

function describeContract(c) {
  if (!c || !c.salaries.length) return "no contract";
  const s = c.salaries;
  const same = s.every((x) => x === s[0]);
  return same ? `${fmtMoney(s[0])} × ${s.length} season${s.length === 1 ? "" : "s"}` : s.map(fmtMoney).join(" / ");
}

async function openPlayer(id) {
  const dialog = byId("player-dialog");
  const body = byId("player-dialog-body");
  body.replaceChildren(h("div", { class: "empty" }, "Loading…"));
  if (!dialog.open) dialog.showModal();
  try {
    renderPlayerDialog(await api.get(`/players/${encodeURIComponent(id)}/contract`));
  } catch (err) {
    body.replaceChildren(h("div", { class: "banner error" }, err.message),
      h("div", { class: "dialog-actions" }, h("button", { class: "btn ghost", type: "button", onclick: () => dialog.close() }, "Close")));
  }
}

function renderPlayerDialog(d) {
  const p = d.player;
  const dialog = byId("player-dialog");
  const current = d.override || d.effective || null;
  const salaries = current ? current.salaries : [];
  const knownSalary = salaries.length ? salaries[0] : null;
  const editable = d.can_edit !== false;   // a GM edits only their own team's players

  // The salary comes from the sheet; this editor sets how many years are left on the contract.
  // Only a player with no salary anywhere gets a salary box.
  const salaryInput = knownSalary == null ? h("input", { type: "number", min: "0", step: "1", inputmode: "numeric",
    placeholder: "$", "aria-label": "Salary per season", disabled: !editable }) : null;
  const salaryText = h("div", { class: "fact-value num" });
  const years = h("input", { type: "number", min: "1", step: "1", inputmode: "numeric", class: "stepper-value",
    value: String(salaries.length || 1), "aria-label": "Years left on the contract", disabled: !editable });
  const minus = h("button", { type: "button", class: "stepper-btn", "aria-label": "One year fewer" }, "−");
  const plus = h("button", { type: "button", class: "stepper-btn", "aria-label": "One year more" }, "+");
  const range = h("span", { class: "card-sub" });
  const typeSel = h("select", { "aria-label": "Contract type", disabled: !editable },
    Object.entries(d.contract_types).map(([k, label]) =>
      h("option", { value: k, selected: k === ((current && current.contract_type) || "STANDARD") }, label)));
  const note = h("input", { type: "text", placeholder: "Optional note (e.g. year 2 of ELC)",
    value: (d.override && d.override.note) || "", "aria-label": "Note", disabled: !editable });
  const error = h("div", { class: "banner error", hidden: true });

  const maxYears = () => (typeSel.value === "IN_SEASON_UFA" ? 1
    : typeSel.value === "ELC" ? d.rules.elc_max_years : d.rules.max_years);
  function refresh(n) {
    const v = Math.max(1, Math.min(maxYears(), Math.round(Number(n) || 1)));
    years.value = String(v);
    years.max = String(maxYears());
    minus.disabled = !editable || v <= 1;
    plus.disabled = !editable || v >= maxYears();
    range.textContent = v === 1 ? `${d.seasons[0]} only` : `${d.seasons[0]} – ${d.seasons[v - 1]}`;
    const elc = typeSel.value === "ELC";
    if (salaryInput && elc) salaryInput.value = String(d.rules.elc_salary);
    salaryText.textContent = elc ? `${fmtMoney(d.rules.elc_salary)} a season (ELC)` : `${fmtMoney(knownSalary)} a season`;
  }
  minus.addEventListener("click", () => refresh(Number(years.value) - 1));
  plus.addEventListener("click", () => refresh(Number(years.value) + 1));
  years.addEventListener("change", () => refresh(years.value));
  typeSel.addEventListener("change", () => refresh(years.value));
  refresh(years.value);

  const sourceLine = d.app_owned
    ? (d.override ? `Managed in CFL Manager · updated ${new Date(d.override.updated_at).toLocaleDateString()}`
      : "No contract yet. Saving adds one in CFL Manager.")
    : d.override
    ? `Edited here ${new Date(d.override.updated_at).toLocaleDateString()}` +
      (d.sheet ? ` · ${possessive(d.sheet.team_name)} sheet says ${describeContract(d.sheet)}` : " · not on any salary sheet")
    : d.sheet ? `From ${possessive(d.sheet.team_name)} salary sheet` + (d.sheet.sheet_name !== p.name ? ` (listed as “${d.sheet.sheet_name}”)` : "")
      : "Not on any salary sheet yet";

  function showError(message) {
    error.textContent = message;
    error.hidden = false;
  }
  async function save() {
    const body = { years: Number(years.value), contract_type: typeSel.value, note: note.value };
    if (salaryInput) {
      if (salaryInput.value.trim() === "") return showError("Enter a salary: this player isn't on any sheet yet.");
      body.salary = Number(salaryInput.value);
    }
    try {
      await api.put(`/players/${encodeURIComponent(p.fantrax_id)}/contract`, body);
      dialog.close();
      afterContractChange(`Saved ${p.name}'s contract`);
    } catch (err) {
      showError(err.message);
    }
  }
  async function revert() {
    try {
      renderPlayerDialog(await api.del(`/players/${encodeURIComponent(p.fantrax_id)}/contract`));
      afterContractChange("Reverted to the sheet");
    } catch (err) {
      showError(err.message);
    }
  }

  byId("player-dialog-body").replaceChildren(...[
    h("header", { class: "dialog-head" },
      h("div", {},
        h("h2", { id: "pd-title" }, p.name),
        h("p", { class: "card-sub" }, [positions(p), p.nhl_team, p.fantasy_team || "Free agent",
          STATUS_LABEL[p.roster_status]].filter(Boolean).join(" · "))),
      h("button", { class: "btn ghost small", type: "button", "aria-label": "Close", onclick: () => dialog.close() }, "✕")),
    h("div", { class: "facts" }, [
      ["Value", fmtNum(p.value, 2)], ["Trade rank", p.trade_rank ? `#${p.trade_rank}` : "–"],
      ["Market value", fmtNum(p.market_value, 1)], ["GP (this / last)", `${p.gp_this_season} / ${p.gp_last_season}`],
    ].map(([k, v]) => h("div", {}, h("div", { class: "fact-label" }, k), h("div", { class: "fact-value num" }, v)))),
    birthDateRow(p, d),
    h("h3", {}, "Contract"),
    h("p", { class: "card-sub", style: { margin: "2px 0 10px" } }, sourceLine),
    p.contract_end ? h("p", { class: "expiry-line" }, endPill(p),
      ` after ${p.contract_end.after_season}: `, p.contract_end.reason || "") : null,
    h("div", { class: "dialog-row" },
      h("div", { class: "field" },
        h("span", { class: "field-label" }, "Years left (this season included)"),
        h("div", { class: "stepper-row" }, h("div", { class: "stepper" }, minus, years, plus), range)),
      salaryInput
        ? h("label", { class: "field" }, h("span", { class: "field-label" }, "Salary per season ($)"), salaryInput)
        : h("div", { class: "field" }, h("span", { class: "field-label" }, "Salary"), salaryText),
      h("label", { class: "field" }, h("span", { class: "field-label" }, "Type"), typeSel)),
    h("p", { class: "market-note" },
      `Up to ${d.rules.max_years} years. An ELC is $${d.rules.elc_salary} for up to ${d.rules.elc_max_years}; ` +
      "an in-season UFA gets 1 year and doesn't use contract-bank years."),
    h("div", { class: "dialog-row" },
      h("label", { class: "field grow" }, h("span", { class: "field-label" }, "Note"), note)),
    error,
    editable ? null : h("p", { class: "market-note" },
      `Only ${p.fantasy_team ? possessive(p.fantasy_team) + " GM or the admin" : "the admin"} can change this contract.`),
    h("div", { class: "dialog-actions" },
      editable && d.override && d.sheet ? h("button", { class: "btn ghost", type: "button", onclick: revert }, "Revert to sheet") : null,
      editable && d.override && !d.sheet ? h("button", { class: "btn ghost", type: "button", onclick: revert }, "Remove contract") : null,
      h("span", { class: "spacer" }),
      h("button", { class: "btn ghost", type: "button", onclick: () => dialog.close() }, "Close"),
      editable ? h("button", { class: "btn", type: "button", onclick: save }, "Save") : null),
  ].filter(Boolean));
}

// Birth date decides RFA vs UFA; it comes from NHL.com but can be set by hand (syncs keep it).
function birthDateRow(p, d) {
  const editable = d.can_edit !== false;
  const input = h("input", { type: "date", value: p.birth_date || "", "aria-label": "Birth date", class: "date-input",
    disabled: !editable });
  const save = h("button", { class: "btn small ghost", type: "button", disabled: true, hidden: !editable }, "Save");
  const msg = h("span", { class: "sub" });
  input.addEventListener("input", () => { save.disabled = input.value === (p.birth_date || ""); });
  save.addEventListener("click", async () => {
    try {
      renderPlayerDialog(await api.put(`/players/${encodeURIComponent(p.fantrax_id)}/birth-date`,
        { birth_date: input.value || null }));
      afterContractChange(`Saved ${p.name}'s birth date`);
    } catch (err) {
      msg.textContent = err.message;
    }
  });
  const source = !p.birth_date ? "unknown: enter it to work out RFA/UFA"
    : d.birth_date_source === "manual" ? `age ${p.age} · entered by hand` : `age ${p.age} · from NHL.com`;
  return h("div", { class: "birth-row" },
    h("span", { class: "field-label" }, "Born"), input, save, h("span", { class: "sub" }, source), msg);
}

function afterContractChange(message) {
  dataChanged();
  byId("sync-status").textContent = `${message}. Cap and trade numbers updated.`;
  setTimeout(renderPublish, 3000);   // the server republishes the sheet in the background
  const view = currentView();
  if (view === "players") loadPlayers();
  else if (view === "trade") loadTrade();
  else if (view === "changes") loadChanges();
  else if (view === "offseason") loadOffseason();   // a birth date or contract length can move a player
  else if (view === "analysis") loadAnalysis();
  else loadDashboard();
}

/* ================================================================ draft picks */

const MOVE_LABEL = { trade: "Trade", rfa: "RFA compensation" };
const shortDate = (iso) => new Date(iso).toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" });
// "2027 R1", plus whose pick it was unless it's the holder's own. withYear: false where a row already says the year.
function pickLabel(p, holder, withYear = true) {
  return (withYear ? `${p.draft_year} ` : "") + `R${p.round}` + (p.original_team_id === holder ? "" : ` (${p.original_team_name})`);
}

let picksSeq = 0;
let picksShown = null;   // the team whose picks the card shows
async function loadPicks({ force = false } = {}) {
  const team = state.playersTeam;
  byId("picks-card").hidden = !team;
  if (!team || (team === picksShown && !force)) return;
  const body = byId("picks-body");
  const token = ++picksSeq;
  body.classList.add("is-loading");
  try {
    const res = await api.get(`/teams/${encodeURIComponent(team)}/picks`);
    if (token !== picksSeq) return;
    picksShown = team;
    renderPicks(res);
  } catch (err) {
    if (token === picksSeq) body.replaceChildren(h("div", { class: "empty" }, err.message));
  } finally {
    if (token === picksSeq) body.classList.remove("is-loading");
  }
}

// Own picks it still holds, picks from other teams, and (dashed) own picks it sent away.
function pickChip(p, teamId) {
  const own = p.original_team_id === teamId, gone = p.owner_team_id !== teamId, m = p.last_move;
  const chip = h("span", { class: "pick-chip " + (gone ? "gone" : own ? "own" : "got"), tabindex: "0" },
    gone ? `→ ${p.owner_team_name}` : own ? "Own" : p.original_team_name);
  bindTip(chip, () => tipContent(`${p.draft_year} round ${p.round}`,
    own ? (gone ? "Own pick, sent away" : "Own pick") : `${possessive(p.original_team_name)} pick`,
    m ? [
      ...(gone ? [["Now with", p.owner_team_name]] : []),
      [MOVE_LABEL[m.kind], `${m.from_team_name} → ${p.owner_team_name}`],
      ["When", shortDate(m.moved_at)],
      ...(m.note ? [["Note", m.note]] : []),
      ["Recorded by", m.source],
    ] : []));
  return chip;
}

// "One → Two: 2027 R1; Two → One: 2028 R2 (Skullets)"
function describeDeal(d) {
  const legs = new Map();
  for (const m of d.moves) {
    const leg = `${m.from_team_id}>${m.to_team_id}`;
    if (!legs.has(leg)) legs.set(leg, { from: m.from_team_name, to: m.to_team_name, picks: [] });
    legs.get(leg).picks.push(pickLabel(m, m.from_team_id));
  }
  return [...legs.values()].map((l) => `${l.from} → ${l.to}: ${l.picks.join(", ")}`).join("; ");
}

function renderPicks(res) {
  const count = res.owned.length;
  const counts = [...new Set(Object.values(res.rounds))];   // each draft's rounds
  byId("picks-sub").textContent = `${count} pick${count === 1 ? "" : "s"} in the next ${res.draft_years.length} drafts. ` +
    `Every team gets ${counts.length === 1 ? `${counts[0]} a year` : "one each round"}; they move in trades and as RFA compensation.`;
  byId("picks-actions").replaceChildren(...(res.can_edit
    ? [h("button", { class: "btn small", type: "button", onclick: () => openPickDeal(res) }, "Trade or send picks…")] : []));
  const rounds = Array.from({ length: Math.max(...counts) }, (_, i) => i + 1);
  const at = (list, y, r) => list.filter((p) => p.draft_year === y && p.round === r);
  const ownFirst = (a, b) => (b.original_team_id === res.team_id) - (a.original_team_id === res.team_id);
  const grid = h("div", { class: "scroll-x" }, h("table", { class: "data-table picks-table" },
    h("thead", {}, h("tr", {}, h("th", { class: "left narrow" }, "Draft"),
      rounds.map((r) => h("th", { class: "left" }, `Round ${r}`)), h("th", { class: "narrow" }, "Picks"))),
    h("tbody", {}, res.draft_years.map((y) => h("tr", {},
      h("td", { class: "left pick-year" }, String(y)),
      rounds.map((r) => {
        if (r > res.rounds[y]) return h("td", {});   // this draft has fewer rounds
        const chips = [...at(res.owned, y, r).sort(ownFirst), ...at(res.sent, y, r)].map((p) => pickChip(p, res.team_id));
        return h("td", { class: "left" }, chips.length ? chips : h("span", { class: "sub" }, "–"));
      }),
      h("td", { class: "num" }, String(res.owned.filter((p) => p.draft_year === y).length)))))));
  const legend = h("div", { class: "picks-legend" },
    h("span", { class: "pick-chip own" }, "Own"), "its own pick",
    h("span", { class: "pick-chip got" }, "Team"), "from that team",
    h("span", { class: "pick-chip gone" }, "→ Team"), "sent away, not counted");
  const deals = !res.deals.length ? null : h("div", { class: "pick-deals" },
    h("h3", {}, "Pick deals"),
    h("div", { class: "scroll-x" }, h("table", { class: "data-table" },
      h("thead", {}, h("tr", {}, ["When", "Deal", "Note", "By"].map((c) => h("th", { class: "left" }, c)), h("th", {}, ""))),
      h("tbody", {}, res.deals.map((d) => h("tr", {},
        h("td", { class: "left" }, shortDate(d.moved_at)),
        h("td", { class: "left wrap" }, h("strong", {}, MOVE_LABEL[d.kind]), ` · ${describeDeal(d)}`),
        h("td", { class: "left wrap" }, d.note || ""),
        h("td", { class: "left" }, d.source),
        h("td", {}, d.can_undo ? h("button", { class: "btn small ghost", type: "button", onclick: () => undoDeal(d) }, "Undo") : "")))))));
  byId("picks-body").replaceChildren(grid, legend, ...(deals ? [deals] : []));
}

async function undoDeal(d) {
  const ok = await confirmDialog({
    title: "Undo this pick deal?", danger: true, confirmLabel: "Undo deal",
    body: [h("p", { class: "dialog-text" }, `The picks go back to the teams that sent them. ${describeDeal(d)}.`)],
  });
  if (!ok) return;
  try {
    await api.del(`/picks/deals/${encodeURIComponent(d.deal_id)}`);
    byId("sync-status").textContent = "Undid the pick deal.";
    loadPicks({ force: true });
  } catch (err) {
    showError(err);
  }
}

// Record a trade (picks both ways; the other team's side needs its GM or the admin) or RFA
// compensation (this team signed another team's RFA and sends it picks).
function openPickDeal(res) {
  const dialog = byId("picks-dialog");
  let kind = "trade";
  let partner = null;   // the other team's picks
  const sendSel = new Set(), getSel = new Set();
  const key = (p) => `${p.draft_year}|${p.round}|${p.original_team_id}`;
  const ref = (k) => { const [y, r, t] = k.split("|"); return { draft_year: Number(y), round: Number(r), original_team_id: t }; };

  const kindEl = h("div", { class: "segmented", role: "group", "aria-label": "Kind of deal" });
  const teamLabel = h("span", { class: "field-label" });
  const teamSel = h("select", { "aria-label": "Other team" }, h("option", { value: "" }, "Pick a team"),
    state.teams.filter((t) => t.id !== res.team_id).map((t) => h("option", { value: t.id }, t.name)));
  const sendBox = h("div", { class: "pick-picker" });
  const getTitle = h("h3", {});
  const getBox = h("div", { class: "pick-picker" });
  const getSection = h("div", {}, getTitle, getBox);
  const help = h("p", { class: "card-sub" });
  const note = h("input", { type: "text", "aria-label": "Note" });
  const error = h("div", { class: "banner error", hidden: true });
  const save = h("button", { class: "btn", type: "button", disabled: true }, "Record deal");
  const showErr = (message) => { error.textContent = message; error.hidden = false; };
  const refreshSave = () => { save.disabled = !teamSel.value || !(sendSel.size || (kind === "trade" && getSel.size)); };

  function picker(box, picks, holder, selected) {
    box.replaceChildren(...res.draft_years.map((y) => {
      const list = picks.filter((p) => p.draft_year === y);
      return h("div", { class: "pick-year-row" }, h("span", { class: "pick-year" }, String(y)),
        list.length ? list.map((p) => {
          const k = key(p);
          const input = h("input", { type: "checkbox", checked: selected.has(k) });
          input.addEventListener("change", () => { if (input.checked) selected.add(k); else selected.delete(k); refreshSave(); });
          return h("label", { class: "check" }, input, pickLabel(p, holder, false));
        }) : h("span", { class: "sub" }, "None left"));
    }));
  }
  function renderSides() {
    const other = partner ? partner.team_name : null;
    segmented(kindEl, Object.entries(res.kinds), kind, (v) => { kind = v; renderSides(); });
    teamLabel.textContent = kind === "rfa" ? "The RFA's old team (gets the picks)" : "Trade with";
    help.textContent = kind === "rfa"
      ? `${res.team_name} signed another team's RFA and sends that team these picks as compensation.`
      : "Picks move as soon as you record the deal. You can undo it from the Pick deals list.";
    note.placeholder = kind === "rfa" ? "Which RFA? e.g. Lucas Raymond" : "Optional, e.g. the players in the deal";
    getSection.hidden = kind !== "trade";
    getTitle.textContent = `${other || "The other team"} sends`;
    if (!teamSel.value) getBox.replaceChildren(h("p", { class: "sub" }, "Pick the other team first."));
    else if (!partner) getBox.replaceChildren(h("p", { class: "sub" }, "Loading…"));
    else if (!partner.can_edit) getBox.replaceChildren(h("p", { class: "sub" },
      `${possessive(other)} GM records the picks they send, from their own team. Or ask the admin.`));
    else picker(getBox, partner.owned, partner.team_id, getSel);
    refreshSave();
  }
  teamSel.addEventListener("change", async () => {
    const id = teamSel.value;
    partner = null;
    getSel.clear();
    renderSides();
    if (!id) return;
    try {
      const p = await api.get(`/teams/${encodeURIComponent(id)}/picks`);
      if (teamSel.value === id) { partner = p; renderSides(); }
    } catch (err) {
      showErr(err.message);
    }
  });
  save.addEventListener("click", async () => {
    save.disabled = true;
    try {
      const out = await api.post("/picks/deals", { kind, team_id: res.team_id, other_team_id: teamSel.value,
        send: [...sendSel].map(ref), receive: kind === "trade" ? [...getSel].map(ref) : [], note: note.value });
      dialog.close();
      picksShown = out.team_id;
      renderPicks(out);
      byId("sync-status").textContent = `Recorded the ${MOVE_LABEL[kind].toLowerCase()}. It shows on the Changes tab.`;
    } catch (err) {
      showErr(err.message);
      refreshSave();
    }
  });

  picker(sendBox, res.owned, res.team_id, sendSel);
  renderSides();
  byId("picks-dialog-body").replaceChildren(
    h("header", { class: "dialog-head" },
      h("div", {}, h("h2", { id: "pk-title" }, "Trade or send picks"), help),
      h("button", { class: "btn ghost small", type: "button", "aria-label": "Close", onclick: () => dialog.close() }, "✕")),
    h("div", { class: "dialog-row" },
      h("div", { class: "field" }, h("span", { class: "field-label" }, "Kind"), kindEl),
      h("label", { class: "field grow" }, teamLabel, teamSel)),
    h("h3", { class: "picker-title" }, `${res.team_name} sends`),
    sendBox,
    getSection,
    h("div", { class: "dialog-row" }, h("label", { class: "field grow" }, h("span", { class: "field-label" }, "Note"), note)),
    error,
    h("div", { class: "dialog-actions" },
      h("span", { class: "spacer" }),
      h("button", { class: "btn ghost", type: "button", onclick: () => dialog.close() }, "Cancel"),
      save));
  dialog.showModal();
}

/* ================================================================ changes */

const CHANGE_FILTERS = {
  all: [], roster: ["added", "dropped", "traded"], salary: ["salary"], contract: ["contract"], picks: ["pick"],
};
const CHANGE_LABEL = { added: "Added", dropped: "Dropped", traded: "Traded", salary: "Salary", contract: "Contract" };

function describeChange(c) {
  if (c.kind === "pick") {
    return `${MOVE_LABEL[c.pick.move]}: ${teamName(c.old_value)} → ${teamName(c.new_value)}` + (c.pick.note ? ` · ${c.pick.note}` : "");
  }
  if (c.kind === "traded") return `Traded: ${teamName(c.old_value)} → ${teamName(c.new_value)}`;
  if (CHANGE_FILTERS.roster.includes(c.kind)) return CHANGE_LABEL[c.kind];
  const typeChanged = c.old_value.contract_type !== c.new_value.contract_type;   // then "Standard" is named too
  const contract = (v) => describeContract(v) + (v.salaries.length && (TYPE_SHORT[v.contract_type] || typeChanged)
    ? ` ${TYPE_SHORT[v.contract_type] || "Standard"}` : "");
  return `${CHANGE_LABEL[c.kind]}: ${contract(c.old_value)} → ${contract(c.new_value)}`;
}

let changesSeq = 0;
async function loadChanges() {
  byId("changes-team").replaceChildren(
    h("option", { value: "" }, "All teams"),
    ...state.teams.map((t) => h("option", { value: t.id, selected: t.id === state.changesTeam }, t.name)));
  segmented(byId("changes-kind"), [["all", "All"], ["roster", "Roster moves"], ["salary", "Salary"], ["contract", "Contract"],
    ["picks", "Draft picks"]], state.changesKind, (v) => { state.changesKind = v; loadChanges(); });
  const out = byId("changes-table");
  const token = ++changesSeq;
  out.classList.add("is-loading");
  try {
    const list = await api.get("/changes", { team_id: state.changesTeam || null, kind: CHANGE_FILTERS[state.changesKind] });
    if (token !== changesSeq) return;
    out.replaceChildren(list.length ? h("table", { class: "data-table" },
      h("thead", {}, h("tr", {}, ["When", "Player or pick", "Team", "Change", "By"].map((label) => h("th", { class: "left" }, label)))),
      h("tbody", {}, list.map((c) => h("tr", {},
        h("td", { class: "left" }, new Date(c.changed_at).toLocaleString()),
        h("td", { class: "left" }, c.kind === "pick"
          ? [`${c.pick.draft_year} round ${c.pick.round}`, h("div", { class: "sub" }, `${possessive(c.pick.original_team_name)} pick`)]
          : playerLink({ fantrax_id: c.player_id, name: c.player_name || c.player_id })),
        h("td", { class: "left" }, c.team_name || "–"),
        h("td", { class: "left" }, describeChange(c)),
        h("td", { class: "left" }, c.source)))))
      : h("div", { class: "empty" }, state.changesKind === "picks" ? "No draft picks have changed hands yet."
        : "No changes yet. They're recorded from the next sync or contract edit."));
  } catch (err) {
    if (token === changesSeq) out.replaceChildren(h("div", { class: "empty" }, err.message));
  } finally {
    if (token === changesSeq) out.classList.remove("is-loading");
  }
}

/* ================================================================ offseason */

// What each player becomes when his contract ends. Every listed player has a contract_end.
const EXPIRY_SEGMENTS = [
  { key: "rfa", label: "RFAs", sub: "restricted free agents", test: (p) => p.contract_end.status === "RFA" },
  { key: "ufa", label: "UFAs", sub: "unrestricted free agents", test: (p) => p.contract_end.status === "UFA" },
  { key: "unknown", label: "Status unknown", sub: "no birth date on file", test: (p) => !p.contract_end.status },
];

// `res`: the /offseason response, for the season the final salary is from. `team`: the team filter.
const offseasonColumns = (res, team) => [
  { key: "name", label: "Player", left: true, value: (p) => p.name },
  team ? null : { key: "fantasy_team", label: "Team", left: true, value: (p) => p.fantasy_team || "",
    render: (p) => p.fantasy_team || "–" },
  { key: "age_at_expiry", label: "Age July 1", value: (p) => p.contract_end.age_at_expiry,
    render: (p) => (p.contract_end.age_at_expiry != null ? p.contract_end.age_at_expiry : "–") },
  { key: "contract_type", label: "Type", left: true, value: (p) => TYPE_SHORT[p.contract_type] || "",
    render: (p) => TYPE_SHORT[p.contract_type] || "" },
  { key: "final_salary", label: `${res.after_season} salary`, value: (p) => p.final_salary, render: (p) => fmtMoney(p.final_salary) },
  // Every contract ending in the next offseason has one season left, so the column only helps later ones.
  res.year !== res.offseasons[0].year ? { key: "contract_years", label: "Yrs left", value: (p) => p.contract_years } : null,
  { key: "value", label: "Value", help: "value", value: (p) => p.value, render: (p) => fmtNum(p.value, 2) },
  { key: "trade_rank", label: "Trade rank", help: "trade_rank", asc: true, value: (p) => p.trade_rank,
    render: (p) => (p.trade_rank ? `#${p.trade_rank}` : "–") },
  { key: "gp_this_season", label: "GP (this/last)", help: "gp", value: (p) => p.gp_this_season,
    render: (p) => `${p.gp_this_season}/${p.gp_last_season}` },
].filter(Boolean);

let offseasonSeq = 0;
async function loadOffseason() {
  const out = byId("offseason-table");
  const token = ++offseasonSeq;
  out.classList.add("is-loading");
  try {
    const res = await api.get("/offseason", { year: state.offseasonYear });
    if (token !== offseasonSeq) return;
    state.offseason = res;
    renderOffseason();
  } catch (err) {
    if (token !== offseasonSeq) return;
    // A remembered year can fall out of range once the league moves on a season: go back to the next one.
    if (err.status === 400 && state.offseasonYear != null) { state.offseasonYear = null; loadOffseason(); return; }
    out.replaceChildren(h("div", { class: "empty" }, err.message));
  } finally {
    if (token === offseasonSeq) out.classList.remove("is-loading");
  }
}

function renderOffseason() {
  const res = state.offseason;
  const team = state.offseasonTeam;
  const players = res.players.filter((p) => !team || p.fantasy_team_id === team);
  const missing = res.no_contract.filter((p) => !team || p.fantasy_team_id === team);

  byId("offseason-year").replaceChildren(...res.offseasons.map((o) =>
    h("option", { value: o.year, selected: o.year === res.year }, `${o.year} (after ${o.after_season})`)));
  byId("offseason-team").replaceChildren(
    h("option", { value: "" }, "All teams"),
    ...state.teams.map((t) => h("option", { value: t.id, selected: t.id === team }, t.name)));
  const views = [["all", "All"], ["rfa", "RFAs"], ["ufa", "UFAs"]];
  if (res.players.some((p) => !p.contract_end.status)) views.push(["unknown", "Unknown"]);
  if (!views.some(([v]) => v === state.offseasonView)) state.offseasonView = "all";
  segmented(byId("offseason-status"), views, state.offseasonView, (v) => { state.offseasonView = v; renderOffseason(); });

  const total = (list) => list.reduce((sum, p) => sum + (p.final_salary || 0), 0);
  byId("offseason-kpis").replaceChildren(...EXPIRY_SEGMENTS.map((seg) => {
    const list = players.filter(seg.test);
    if (seg.key === "unknown" && !list.length) return null;
    return tile(seg.label, String(list.length), list.length === 1 ? "player" : "players",
      seg.key === "unknown" ? "Add birth dates to place them" : `${fmtMoney(total(list))} in ${res.after_season} salaries`);
  }).filter(Boolean));

  byId("offseason-title").textContent = `${team ? possessive(teamName(team)) : "All"} contracts ending after ${res.after_season}`;
  byId("offseason-sub").textContent = `Free agency is decided on July 1, ${res.year}: players ${res.ufa_age} or older ` +
    `become UFAs, younger players RFAs, and in-season pickups are always UFAs. Click a player to open his contract.`;
  byId("offseason-table").replaceChildren(sectionedTable({
    players, columns: offseasonColumns(res, team), view: state.offseasonView, oneTeam: Boolean(team),
    segments: EXPIRY_SEGMENTS, salary: (p) => p.final_salary,
    sort: state.offseasonSort, onSort: (next) => { state.offseasonSort = next; renderOffseason(); },
    empty: `No contracts on file end after ${res.after_season}.`,
  }));
  byId("offseason-missing").replaceChildren(missing.length ? h("details", { class: "offseason-missing" },
    h("summary", {}, `${missing.length} rostered player${missing.length === 1 ? " has" : "s have"} no contract on file, ` +
      "so they can't be placed in any offseason"),
    h("p", { class: "player-links" }, missing.map((p) => playerLink(p)))) : "");
}

/* ================================================================ sign-in */

// What the signed-in person sees: GMs get the dashboard and players; the admin gets everything.
function applyRole() {
  const me = state.me;
  byId("tabs").hidden = false;
  byId("topbar-actions").hidden = false;
  byId("tab-trade").hidden = !me.is_admin;
  byId("tab-offseason").hidden = !me.is_admin;
  byId("tab-admin").hidden = !me.is_admin;
  byId("sync-btn").hidden = !me.is_admin;
  byId("players-sub").textContent = me.is_admin
    ? "Click a player to edit his contract. Edits override the salary sheet until you revert them."
    : "Click a player to see his contract. You can edit the players on your own team.";
  // No logins when running locally without GOOGLE_CLIENT_ID: the page is still there, for the theme.
  byId("account-link").title = me.auth ? `${me.email}: account, theme and sign out` : "Account and theme";
  byId("account-link").replaceChildren(h("span", { class: "account-name" }, me.auth ? me.name || me.email : "Account"));
  if (me.auth) byId("account-link").prepend(avatar(me));
}

const avatar = (me, cls = "") => h("span", { class: `avatar ${cls}`, "aria-hidden": "true" },
  (me.name || me.email).trim().charAt(0).toUpperCase());

function showGate(title, text, extra = []) {
  for (const v of VIEWS) byId(`view-${v}`).hidden = true;
  byId("tabs").hidden = true;
  byId("topbar-actions").hidden = true;
  byId("league-name").textContent = "";
  byId("gate-title").textContent = title;
  byId("gate-text").textContent = text;
  byId("gate-extra").replaceChildren(...extra);
  byId("gate-msg").textContent = "";
  byId("view-signin").hidden = false;
}

let googleScript = null;
function loadGoogle() {
  googleScript = googleScript || new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = "https://accounts.google.com/gsi/client";
    s.async = true;
    s.onload = () => resolve();
    s.onerror = () => { googleScript = null; reject(new Error("Couldn't load Google sign-in. Check your connection and reload.")); };
    document.head.append(s);
  });
  return googleScript;
}

let signInShown = false;
async function showSignIn() {
  if (signInShown) return;   // several requests can hit an expired session at once
  signInShown = true;
  state.me = null;
  showGate("Sign in", "Use the Google account you play under. If you're new, sign in and the admin will approve you and link your team.");
  const button = byId("google-button");
  button.hidden = false;
  if (new URLSearchParams(window.location.search).has("signin")) {   // /auth/google sent us back
    byId("gate-msg").textContent = "Google sign-in didn't work. Try again.";
    history.replaceState(null, "", window.location.pathname + window.location.hash);
  }
  try {
    const cfg = await api.get("/auth/config");
    await loadGoogle();
    // Redirect, not the default popup: some browsers (Firefox with strict tracking protection or
    // container extensions) never open it. Google posts the token to /auth/google, which signs in
    // and comes back to the page.
    google.accounts.id.initialize({ client_id: cfg.google_client_id, ux_mode: "redirect",
      login_uri: `${window.location.origin}/auth/google` });
    const theme = document.documentElement.dataset.theme;
    const dark = theme ? theme === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
    google.accounts.id.renderButton(button, { theme: dark ? "filled_black" : "outline", size: "large", shape: "pill",
      text: "signin_with" });
  } catch (err) {
    byId("gate-msg").textContent = err.message;
  }
}

function showWaiting() {
  const me = state.me;
  const first = me.name ? `, ${me.name.split(" ")[0]}` : "";
  showGate("Waiting for approval",
    `Thanks${first}. The admin needs to approve ${me.email} and link it to your team. Check back once they have.`,
    [h("button", { class: "btn ghost", type: "button", onclick: signOut }, "Use a different account")]);
  byId("google-button").hidden = true;
}

// React and Effect load with import() when first needed, so tabs that don't use them don't download them.
const signOut = () => import("../auth").then((auth) => auth.signOut());

/* ================================================================ account */

// "system" follows the device's light or dark setting; the others override it in this browser.
// index.html applies the saved choice before the page draws.
const THEMES = [["system", "Match device"], ["light", "Light"], ["dark", "Dark"]];

function setTheme(theme) {
  if (theme === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
  prefs.set("theme", theme);
  renderAccount();
}

function renderAccount() {
  segmented(byId("theme-choice"), THEMES, prefs.get("theme", "system"), setTheme);
  showView("account-card", () => import("../views/account/AccountCard").then((m) => m.AccountCard));
}

/* ================================================================ admin */

let adminSeq = 0;
async function loadAdmin() {
  const out = byId("admin-body");
  const token = ++adminSeq;
  out.classList.add("is-loading");
  try {
    const res = await api.get("/admin/users");
    if (token === adminSeq) renderAdmin(res);
  } catch (err) {
    if (token === adminSeq) out.replaceChildren(h("div", { class: "banner error" }, err.message));
  } finally {
    if (token === adminSeq) out.classList.remove("is-loading");
  }
}

// Every change returns the updated list, which is drawn straight away.
async function adminCall(method, path, body) {
  try {
    renderAdmin(await request(method, path, { body }));
    clearError();
  } catch (err) {
    showError(err);
  }
}

function teamSelect(value, blank) {
  return h("select", { "aria-label": "Team" },
    h("option", { value: "" }, blank),
    ...state.teams.map((t) => h("option", { value: t.id, selected: t.id === value }, t.name)));
}

function adminCard(title, sub, body) {
  return h("article", { class: "card" },
    h("header", { class: "card-head" }, h("div", {}, h("h2", {}, title), h("p", { class: "card-sub" }, sub))),
    body);
}

async function removeUser(u) {
  const ok = await confirmDialog({
    title: `Remove ${u.name || u.email}?`, danger: true, confirmLabel: "Remove",
    body: [h("p", { class: "dialog-text" }, "They lose access straight away. If they sign in again, they show up as a new request.")],
  });
  if (ok) adminCall("DELETE", userPath(u));
}

const userPath = (u) => `/admin/users/${encodeURIComponent(u.email)}`;

function renderAdmin(res) {
  const when = (iso) => (iso ? new Date(iso).toLocaleDateString() : "–");
  const person = (u) => h("td", { class: "left" }, h("div", {}, u.name || u.email), u.name ? h("div", { class: "sub" }, u.email) : null);
  const head = (...cols) => h("thead", {}, h("tr", {}, cols.map((c) => h("th", { class: "left" }, c)), h("th", {}, "")));
  const pending = res.users.filter((u) => u.status === "pending");
  const active = res.users.filter((u) => u.status === "active");

  const requests = !pending.length
    ? h("div", { class: "empty" }, "No one is waiting. Send GMs the site's link: when they sign in with Google, they show up here.")
    : h("div", { class: "scroll-x" }, h("table", { class: "data-table" }, head("Person", "Asked", "Team"),
      h("tbody", {}, pending.map((u) => {
        const team = teamSelect(u.team_id, "Pick their team");
        const approve = h("button", { class: "btn small", type: "button", disabled: !team.value,
          onclick: () => adminCall("PUT", userPath(u), { team_id: team.value, role: "gm", status: "active" }) }, "Approve");
        team.addEventListener("change", () => { approve.disabled = !team.value; });
        return h("tr", {}, person(u), h("td", { class: "left" }, when(u.created_at)), h("td", { class: "left" }, team),
          h("td", {}, h("div", { class: "row-actions" }, approve,
            h("button", { class: "btn small ghost", type: "button", onclick: () => adminCall("DELETE", userPath(u)) }, "Decline"))));
      }))));

  const people = !active.length ? h("div", { class: "empty" }, "No one yet.")
    : h("div", { class: "scroll-x" }, h("table", { class: "data-table" }, head("Person", "Team", "Role", "Last sign-in"),
      h("tbody", {}, active.map((u) => {
        const self = u.email === state.me.email;
        const team = teamSelect(u.team_id, "No team");
        const role = h("select", { "aria-label": "Role", disabled: u.always_admin || self },
          h("option", { value: "gm", selected: u.role !== "admin" && !u.always_admin }, "GM"),
          h("option", { value: "admin", selected: u.role === "admin" || u.always_admin }, "Admin"));
        const save = () => adminCall("PUT", userPath(u), { team_id: team.value || null, role: role.value, status: "active" });
        team.addEventListener("change", save);
        role.addEventListener("change", save);
        return h("tr", {}, person(u), h("td", { class: "left" }, team),
          h("td", { class: "left" }, role, u.always_admin ? h("div", { class: "sub" }, "Always (CFL_ADMIN_EMAILS)") : null),
          h("td", { class: "left" }, when(u.last_login_at)),
          h("td", {}, self ? h("span", { class: "sub" }, "You") :
            h("button", { class: "btn small ghost", type: "button", onclick: () => removeUser(u) }, "Remove")));
      }))));

  const email = h("input", { type: "email", placeholder: "their.name@gmail.com", "aria-label": "Google email", autocomplete: "off" });
  const team = teamSelect("", "Pick their team");
  const add = h("button", { class: "btn", type: "button", onclick: () => {
    if (!email.value.trim() || !team.value) return showError("Enter their Google email and pick their team.");
    adminCall("POST", "/admin/users", { email: email.value.trim(), team_id: team.value, role: "gm", status: "active" });
  } }, "Add");

  byId("admin-body").replaceChildren(
    adminCard("Requests", "People who signed in with Google and are waiting for you. Pick their team to approve them.", requests),
    adminCard("People with access",
      "GMs see every team and edit their own team's contracts. Admins also get the trade builder, the offseason list, full syncs, publishing and this page.",
      people),
    adminCard("Add someone", "They get straight in the first time they sign in with this Google account.",
      h("div", { class: "filters" },
        h("label", { class: "field grow" }, h("span", { class: "field-label" }, "Google email"), email),
        h("label", { class: "field" }, h("span", { class: "field-label" }, "Team"), team),
        add)));
}

boot();
