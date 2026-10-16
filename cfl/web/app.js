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
    throw new Error(typeof detail === "string" ? detail : (detail && detail.error) || `Request failed (${res.status})`);
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
function fmtMoney(v) {
  if (v == null) return "–";
  const n = Math.round(Number(v) * 10) / 10;
  return (n < 0 ? "−$" : "$") + Math.abs(n).toString();
}
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
  rosterView: "roster",
  playersSort: { key: "value", dir: -1 },
  playersView: "all",
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
const seqs = { dashboard: 0, tradeLoad: 0, suggest: 0, evaluate: 0, pool: 0 };

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
  let searchTimer = null;
  byId("players-search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(loadPlayers, 250); });
  byId("players-team").addEventListener("change", (e) => { state.playersTeam = e.target.value; loadPlayers(); });
  byId("players-missing").addEventListener("change", loadPlayers);
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
    loadDashboard();
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
  quick: { resources: ["rosters", "stats", "match", "bios"], busy: "Refreshing…",
    status: "Pulling today's Fantrax rosters and NHL.com stats…", done: "Rosters and stats refreshed" },
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
    resetCaches();
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
  } finally {
    buttons.forEach((b) => { b.disabled = false; });
    btn.textContent = label;
  }
}

const VIEWS = ["dashboard", "trade", "players"];

function currentView() {
  const v = window.location.hash.slice(1);
  return VIEWS.includes(v) ? v : "dashboard";
}

function route() {
  const view = currentView();
  for (const v of VIEWS) {
    byId(`view-${v}`).hidden = v !== view;
    byId(`tab-${v}`).setAttribute("aria-selected", String(v === view));
  }
  if (!state.team) return;
  if (view === "dashboard") loadDashboard();
  else if (view === "trade") loadTrade();
  else loadPlayers();
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
  byId("kpis").replaceChildren(...[
    tile("Expected category wins per week", fmtNum(profile.expected_category_wins, 2), `of ${nCats}`,
      "Against an average opponent, from projected rest-of-season lineups", { hero: true }),
    tile("Projected strength", ordinal(profile.projected_overall_rank), `of ${state.teams.length}`,
      "Total category strength across the league"),
    tile("Standings", standing.rank ? ordinal(standing.rank) : "–", standing.record || "",
      "Fantrax head-to-head standings"),
    tile("Skater games per week", fmtNum(skaterGames, 1), null, "Lineup games, including bench fill-ins"),
    tile("Goalie starts per week", fmtNum(goalieStarts, 2), null, "Expected starts across all your goalies"),
    profile.cap ? tile("Cap space", fmtMoney(profile.cap.space), `of ${fmtMoney(profile.cap.cap)}`,
      `${fmtMoney(profile.cap.committed)} committed` +
      (profile.cap.unknown_salary.length ? ` · no salary for ${profile.cap.unknown_salary.join(", ")}` : "")) : null,
    profile.cap ? yearsTile(profile.cap) : null,
  ].filter(Boolean));
  renderCap(profile.cap);
}

function yearsTile(cap) {
  const over = cap.contract_years - cap.contract_year_bank;
  return h("div", { class: "tile" },
    h("div", { class: "tile-label" }, "Contract years"),
    h("div", { class: "tile-value" }, String(cap.contract_years), h("small", {}, `of ${cap.contract_year_bank}`)),
    h("div", { class: "tile-sub" + (over > 0 ? " warn" : "") },
      over > 0 ? [h("span", { class: "down", "aria-hidden": "true" }, "▲ "), `${over} over the bank`]
        : `${-over} left in the bank`));
}

function capRow(label, committed, cap, note) {
  const over = committed > cap;
  const pct = Math.min(committed / cap, 1) * 100;
  const row = h("div", { class: "cap-row", tabindex: "0" },
    h("span", { class: "cap-season" }, label),
    h("div", { class: "meter", role: "img", "aria-label": `${label}: ${fmtMoney(committed)} of ${fmtMoney(cap)}` },
      h("div", { class: "meter-fill" + (over ? " over" : ""), style: { width: pct + "%" } })),
    h("span", { class: "cap-figures num" },
      over ? h("span", { class: "down", "aria-hidden": "true" }, "▲ ") : null,
      `${fmtMoney(committed)} · `, h("strong", {}, over ? `${fmtMoney(committed - cap)} over` : `${fmtMoney(cap - committed)} space`)),
  );
  if (note) bindTip(row, () => tipContent(`${fmtMoney(cap - committed)} space`, label, note));
  return row;
}

// Google Sheet publishing status for the team on screen (only teams whose contracts live in the app).
async function renderPublish() {
  const box = byId("publish-status");
  let st;
  try { st = await api.get("/publish"); } catch (_) { box.replaceChildren(); return; }
  const t = st.teams[state.team];
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

function renderCap(cap) {
  byId("cap-card").hidden = !cap;
  if (!cap) return;
  renderPublish();
  const parts = [`Cap ${fmtMoney(cap.cap)}. Active and bench salaries count, plus buyouts; minors and IR don't`];
  if (cap.ir_relief) parts.push(`${fmtMoney(cap.ir_relief)} IR relief right now`);
  byId("cap-sub").textContent = parts.join(" · ");
  const now = [["Committed", fmtMoney(cap.committed)], ["IR relief", fmtMoney(cap.ir_relief)],
    ["Buyouts", fmtMoney(cap.dead_cap)]];
  if (cap.unknown_salary.length) now.push(["No salary on sheet", cap.unknown_salary.join(", ")]);
  byId("cap-outlook").replaceChildren(
    capRow(`${cap.season} (now)`, cap.committed, cap.cap, now),
    ...cap.future.map((f) => capRow(f.season, f.committed, cap.cap,
      [["Already committed", fmtMoney(f.committed)], ["Includes", "every contract still running that season"]])),
    h("p", { class: "market-note" },
      `${cap.contract_years} of ${cap.contract_year_bank} contract years used (seasons left on each contract; minors and IR excluded).` +
      (cap.unknown_salary.length ? ` Unknown salaries aren't included: ${cap.unknown_salary.join(", ")}.` : "")),
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
  { key: "salary", label: "Salary", value: (p) => p.salary, render: (p) => fmtMoney(p.salary) },
  { key: "contract_years", label: "Yrs", value: (p) => p.contract_years, render: (p) => (p.contract_years || "–") },
  { key: "contract_end", label: "At expiry", asc: true, value: endSortKey, render: endPill },
  { key: "gp_this_season", label: "GP (this/last)", value: (p) => p.gp_this_season,
    render: (p) => `${p.gp_this_season}/${p.gp_last_season}` },
  { key: "best", label: "Best categories", left: true, cls: "cats-inline", sortable: false, render: (p) => bestCats(p.z) },
];

function renderRoster(profile) {
  segmented(byId("roster-view"), [["roster", "Roster"], ["prospects", "Prospects"], ["all", "All"]],
    state.rosterView, (v) => { state.rosterView = v; renderRoster(profile); });
  byId("roster-table").replaceChildren(sectionedTable({
    players: profile.roster, columns: ROSTER_COLUMNS, view: state.rosterView,
    sort: state.rosterSort, onSort: (next) => { state.rosterSort = next; renderRoster(profile); },
    empty: state.rosterView === "prospects" ? "No prospects in the minors." : "No players.",
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

// One table, with a section per roster segment and position group so columns line up across
// sections. Sorting applies within each section.
function sectionedTable({ players, columns, view, sort, onSort, empty, oneTeam = true }) {
  const col = columns.find((c) => c.key === sort.key) || columns[0];
  const cmp = (a, b) => {
    const va = col.value(a), vb = col.value(b);
    if (va == null && vb == null) return 0;
    if (va == null) return 1;
    if (vb == null) return -1;
    return (typeof va === "string" ? va.localeCompare(vb) : va - vb) * sort.dir;
  };
  const head = h("tr", {}, columns.map((c) => {
    const sortable = c.sortable !== false;
    const th = h("th", {
      class: (c.left ? "left " : "") + (sortable ? "sortable" : ""),
      "aria-sort": c.key === sort.key ? (sort.dir === 1 ? "ascending" : "descending") : null,
      tabindex: sortable ? "0" : null,
    }, c.label);
    if (sortable) {
      const go = () => onSort(c.key === sort.key ? { key: sort.key, dir: -sort.dir }
        : { key: c.key, dir: c.asc || c.key === "name" ? 1 : -1 });
      th.addEventListener("click", go);
      th.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
    }
    return th;
  }));
  const money = (list) => list.reduce((sum, p) => sum + (p.salary || 0), 0);
  const bodies = [];
  for (const seg of SEGMENTS.filter((x) => view === "all" || x.key === view)) {
    const inSeg = players.filter(seg.test);
    if (!inSeg.length) continue;
    bodies.push(h("tbody", { class: "seg" }, h("tr", { class: "seg-row" }, h("th", { colspan: String(columns.length) },
      seg.label, seg.sub ? h("span", { class: "seg-sub" }, ` · ${seg.sub}`) : null,
      h("span", { class: "seg-meta" }, `${inSeg.length} player${inSeg.length === 1 ? "" : "s"}` +
        (money(inSeg) ? ` · ${fmtMoney(money(inSeg))} salary` : ""))))));
    for (const group of POSITION_GROUPS) {
      const list = inSeg.filter((p) => posGroup(p) === group.key).sort(cmp);
      if (!list.length) continue;
      const meta = [`${list.length}`];
      if (oneTeam && seg.key === "roster" && lineupSlots(group)) meta.push(`${lineupSlots(group)} lineup slots`);
      if (money(list)) meta.push(`${fmtMoney(money(list))}`);
      bodies.push(h("tbody", { class: "group" },
        h("tr", { class: "group-row" }, h("th", { colspan: String(columns.length) },
          group.label, group.sub ? h("span", { class: "seg-sub" }, ` ${group.sub}`) : null,
          h("span", { class: "seg-meta" }, meta.join(" · ")))),
        list.map((p) => h("tr", {}, columns.map((c) => (c.key === "name" ? playerCell(p)
          : h("td", { class: c.left ? "left" + (c.cls ? " " + c.cls : "") : "num" }, c.render ? c.render(p) : c.value(p))))))));
    }
  }
  if (!bodies.length) return h("div", { class: "empty" }, empty || "No players.");
  return h("table", { class: "data-table sectioned" }, h("thead", {}, head), ...bodies);
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
      ? `* No salary set for ${cap.unknown_incoming.join(", ")}, so cap space after leaves it out. Set the pickup's contract in the Players tab to include it.`
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

const SOURCE_LABEL = { sheet: "Sheet", override: "Edited", app: "App" };
const TYPE_SHORT = { STANDARD: null, ELC: "ELC", IN_SEASON_UFA: "In-season UFA" };

function playerLink(p) {
  return h("button", { type: "button", class: "player-link", onclick: () => openPlayer(p.fantrax_id),
    title: "Edit contract" }, p.name);
}

const PLAYER_COLUMNS = [
  { key: "name", label: "Player", left: true, value: (p) => p.name },
  { key: "fantasy_team", label: "Team", left: true, value: (p) => p.fantasy_team || "", render: (p) => p.fantasy_team || "–",
    leagueOnly: true },
  { key: "salary", label: "Salary", value: (p) => p.salary, render: (p) => fmtMoney(p.salary) },
  { key: "contract_years", label: "Yrs", value: (p) => p.contract_years, render: (p) => p.contract_years || "–" },
  { key: "contract_end", label: "At expiry", asc: true, value: endSortKey, render: endPill },
  { key: "age", label: "Age", value: (p) => p.age, render: (p) => (p.age != null ? p.age : "–") },
  { key: "contract_type", label: "Type", left: true, value: (p) => TYPE_SHORT[p.contract_type] || "",
    render: (p) => TYPE_SHORT[p.contract_type] || "" },
  { key: "contract_source", label: "Source", left: true, value: (p) => p.contract_source || "",
    render: (p) => (p.contract_source
      ? h("span", { class: "badge" + (p.contract_source === "override" ? " solid" : "") }, SOURCE_LABEL[p.contract_source])
      : h("span", { class: "sub" }, "none")) },
  { key: "trade_rank", label: "Trade rank", asc: true, value: (p) => p.trade_rank,
    render: (p) => (p.trade_rank ? `#${p.trade_rank}` : "–") },
  { key: "value", label: "Value", value: (p) => p.value, render: (p) => fmtNum(p.value, 2) },
];

let playersSeq = 0;
async function loadPlayers() {
  if (state.playersTeam === undefined) state.playersTeam = state.team;
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
    out.replaceChildren(sectionedTable({
      players, columns: PLAYER_COLUMNS.filter((c) => !(team && c.leagueOnly)), view: state.playersView, oneTeam: Boolean(team),
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
  if (!c) return "no contract";
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

  // The salary comes from the sheet; this editor sets how many years are left on the contract.
  // Only a player with no salary anywhere gets a salary box.
  const salaryInput = knownSalary == null ? h("input", { type: "number", min: "0", step: "1", inputmode: "numeric",
    placeholder: "$", "aria-label": "Salary per season" }) : null;
  const salaryText = h("div", { class: "fact-value num" });
  const years = h("input", { type: "number", min: "1", step: "1", inputmode: "numeric", class: "stepper-value",
    value: String(salaries.length || 1), "aria-label": "Years left on the contract" });
  const minus = h("button", { type: "button", class: "stepper-btn", "aria-label": "One year fewer" }, "−");
  const plus = h("button", { type: "button", class: "stepper-btn", "aria-label": "One year more" }, "+");
  const range = h("span", { class: "card-sub" });
  const typeSel = h("select", { "aria-label": "Contract type" },
    Object.entries(d.contract_types).map(([k, label]) =>
      h("option", { value: k, selected: k === ((current && current.contract_type) || "STANDARD") }, label)));
  const note = h("input", { type: "text", placeholder: "Optional note (e.g. year 2 of ELC)",
    value: (d.override && d.override.note) || "", "aria-label": "Note" });
  const error = h("div", { class: "banner error", hidden: true });

  const maxYears = () => (typeSel.value === "IN_SEASON_UFA" ? 1
    : typeSel.value === "ELC" ? d.rules.elc_max_years : d.rules.max_years);
  function refresh(n) {
    const v = Math.max(1, Math.min(maxYears(), Math.round(Number(n) || 1)));
    years.value = String(v);
    years.max = String(maxYears());
    minus.disabled = v <= 1;
    plus.disabled = v >= maxYears();
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
    h("div", { class: "dialog-actions" },
      d.override && d.sheet ? h("button", { class: "btn ghost", type: "button", onclick: revert }, "Revert to sheet") : null,
      d.override && !d.sheet ? h("button", { class: "btn ghost", type: "button", onclick: revert }, "Remove contract") : null,
      h("span", { class: "spacer" }),
      h("button", { class: "btn ghost", type: "button", onclick: () => dialog.close() }, "Close"),
      h("button", { class: "btn", type: "button", onclick: save }, "Save")),
  ].filter(Boolean));
}

// Birth date decides RFA vs UFA; it comes from NHL.com but can be set by hand (syncs keep it).
function birthDateRow(p, d) {
  const input = h("input", { type: "date", value: p.birth_date || "", "aria-label": "Birth date", class: "date-input" });
  const save = h("button", { class: "btn small ghost", type: "button", disabled: true }, "Save");
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
  resetCaches();
  byId("sync-status").textContent = `${message}. Cap and trade numbers updated.`;
  setTimeout(renderPublish, 3000);   // the server republishes the sheet in the background
  const view = currentView();
  if (view === "players") loadPlayers();
  else if (view === "trade") loadTrade();
  else loadDashboard();
}

boot();
