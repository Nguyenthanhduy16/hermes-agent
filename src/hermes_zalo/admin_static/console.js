"use strict";

(() => {
  const $ = id => document.getElementById(id);
  const views = {
    overview: ["Home", "How your assistant is doing at a glance."],
    chat: ["Chat", "Talk to your assistant directly from the console."],
    sessions: ["Conversations", "Conversations people allowed the assistant to keep."],
    memories: ["Memory", "What the assistant remembers, with controls to edit or retire it."],
    models: ["Models", "Choose the models that power the assistant."],
    skills: ["Skills", "Reusable workflows the assistant can follow."],
    routines: ["Automations", "Work the assistant runs on its own: reports, reminders and checks."],
    mcp: ["Tools (MCP)", "External tool servers the assistant can call."],
    channels: ["Channels", "Where people can reach the assistant."],
    users: ["Users", "Who can use the assistant and how much they used today."],
    usage: ["Usage & cost", "Model requests, tokens and recorded cost."],
    keys: ["API keys", "Credential status. Stored values are never shown."],
    config: ["Settings", "Allowlisted runtime settings for this bot home."],
    logs: ["Logs", "Search redacted operational log entries."],
    runtime: ["System", "Server resources and the execution environment."],
    "system-overview": ["System overview", "Technical health: conversations, model usage and capabilities."],
    files: ["Files", "Browse files inside the assistant workspace."],
    plugins: ["Plugins", "Installed plugins and their next-start selection."],
    webhooks: ["Webhooks", "Signed event entry points."],
    access: ["Admin accounts", "Who can sign in to this console and what they can do."],
    documentation: ["Help", "Setup and operation guides."]
  };
  // Information architecture: a short "Essentials" set for daily use (in this order); every other
  // page lives in the collapsible "Advanced" area. Extensions opt in with `essential: true`.
  const ESSENTIALS = ["overview", "briefing", "chat", "reports", "routines", "connectors", "channels", "memories"];
  // The CEO briefing (console-briefing.js) replaces the technical overview as Home once registered.
  const HOME_PAGE = "briefing";
  // Friendly hashes that map onto existing views; old hashes keep working unchanged.
  const ALIASES = { home: "overview", automations: "routines", schedules: "routines" };
  // Views that read another section's API.
  const API_VIEW = { "system-overview": "overview" };
  const NAV_GROUPS = new Set(["main", "agent", "integrations", "observe", "people", "advanced"]);
  const PAGE_ICONS = { overview: "home", chat: "chat", sessions: "history", memories: "memory", models: "cpu", skills: "sparkles", routines: "calendar", mcp: "plug", channels: "send", users: "users", usage: "chart", keys: "key", config: "sliders", logs: "list", runtime: "server", files: "folder", plugins: "box", webhooks: "zap", access: "shield", documentation: "book", "system-overview": "activity" };
  const PAGE_KEYWORDS = { overview: "home dashboard start", routines: "schedules scheduled jobs cron routines", "system-overview": "dashboard health tokens cost", sessions: "history threads", mcp: "tools servers", keys: "credentials secrets tokens", config: "configuration settings", access: "admins accounts permissions", documentation: "docs guides help" };
  // Pages where the record filter has nothing to filter.
  const UNSEARCHABLE = new Set(["overview", "system-overview", "chat", "runtime", "logs"]);
  // Feature sections registered by console-*.js through window.HermesConsole.register.
  const extensions = new Map();
  const state = { pollTimer: 0, requestedView: "", user: null, csrf: "", mode: "local", runtimeHistory: false, view: "overview", page: 1, data: null, request: 0, controller: null, editing: null, history: null, historyPage: 1, detailRequest: 0, actionBusy: false, filePath: "", chatMessages: [], chatDraft: "", control: null, controlRefresh: "", skillCategory: "", logFilter: {}, logDraft: null, chatBusy: false, chatPending: "", chatRequest: 0, pendingAction: null, palette: { entries: [], items: [], index: 0, returnFocus: null, restore: true } };
  const number = value => typeof value === "number" && Number.isFinite(value) ? new Intl.NumberFormat("en-US").format(value) : "—";
  const money = value => typeof value === "number" && Number.isFinite(value) ? new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 4 }).format(value) : "Pricing unavailable";
  function date(value) { if (!value) return "—"; const d = new Date(typeof value === "number" ? value * 1000 : value); return Number.isNaN(d.valueOf()) ? "—" : d.toLocaleString("en-US", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }); }
  function bytes(value) { if (typeof value !== "number" || !Number.isFinite(value)) return "—"; for (const [unit, scale] of [["GB", 1073741824], ["MB", 1048576], ["KB", 1024]]) if (value >= scale) return (value / scale).toFixed(1) + " " + unit; return number(value) + " B"; }
  const can = permission => state.user?.permissions?.includes(permission);
  const isGroup = value => ["group", "supergroup"].includes(String(value || "").toLowerCase());
  const operator = () => state.user?.role === "faculty";
  function allowed(view) { if (API_VIEW[view]) view = API_VIEW[view]; const ext = extensions.get(view); if (ext) return typeof ext.allowed === "function" ? ext.allowed({ can, operator: operator() }) === true : operator(); if (view === "sessions") return can("conversations"); if (view === "memories") return !state.runtimeHistory && can("conversations") && can("memories"); return operator(); }
  function node(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined && text !== null) n.textContent = String(text); return n; }
  function button(text, cls, callback) { const b = node("button", cls || "text-button", text); b.type = "button"; b.addEventListener("click", callback); return b; }
  function badge(text, kind = "neutral") { return node("span", "badge " + kind, text); }
  const SVG = "http://www.w3.org/2000/svg";
  function icon(name) { const svg = document.createElementNS(SVG, "svg"), use = document.createElementNS(SVG, "use"); svg.setAttribute("class", "icon"); svg.setAttribute("aria-hidden", "true"); use.setAttribute("href", "#i-" + name); svg.append(use); return svg; }
  function iconBox(name, kind = "accent") { const box = node("span", "service-icon" + (kind ? " " + kind : "")); box.append(icon(name)); return box; }
  function iconButton(text, name, cls, callback) { const b = node("button", cls); b.type = "button"; b.append(icon(name), node("span", "", text)); b.addEventListener("click", callback); return b; }
  function empty(title, description, name = "box") { const div = node("div", "empty"), mark = node("span", "empty-icon"); mark.append(icon(name)); div.append(mark, node("strong", "", title), node("p", "", description)); return div; }
  function panel(title, description, action) { const box = node("section", "panel"), head = node("div", "panel-header"), text = node("div"); text.append(node("h2", "", title)); if (description) text.append(node("p", "", description)); head.append(text); if (action) head.append(action); box.append(head); return box; }
  // Compact one-line notice; the long explanation sits behind a "Details" disclosure.
  const NOTE_ICON = { info: "info", warning: "alert", error: "alert", good: "check" };
  let noteCount = 0;
  function note(title, text, tone = "info") {
    const kind = Object.hasOwn(NOTE_ICON, tone) ? tone : "info", box = node("div", "note " + kind), mark = node("span", "note-icon"), body = node("div", "note-body"), line = node("div", "note-line");
    box.setAttribute("role", kind === "error" ? "alert" : "note");
    mark.append(icon(NOTE_ICON[kind]));
    line.append(node("strong", "note-title", title));
    body.append(line);
    if (text !== undefined && text !== null && String(text).trim()) {
      const id = "note-text-" + (++noteCount), more = node("p", "note-text", text), toggle = node("button", "note-toggle", "Details");
      more.id = id; more.hidden = true; toggle.type = "button"; toggle.setAttribute("aria-expanded", "false"); toggle.setAttribute("aria-controls", id);
      toggle.addEventListener("click", () => { more.hidden = !more.hidden; toggle.setAttribute("aria-expanded", String(!more.hidden)); toggle.textContent = more.hidden ? "Details" : "Hide details"; });
      line.append(toggle); body.append(more);
    }
    box.append(mark, body); return box;
  }
  // Splits a server message into a short first sentence and the rest as details.
  function noteFrom(message, tone = "info") {
    const text = String(message ?? "").trim(), split = /^([\s\S]{8,160}?[.!?])\s+([\s\S]+)$/.exec(text);
    return split ? note(split[1], split[2], tone) : note(text, "", tone);
  }
  function notice(target, error) { target.textContent = error?.message || String(error); target.hidden = false; }
  let toastTimer;
  // tone: "good" (default), "info", "warning" or "error"; errors stay on screen a little longer.
  function toast(text, tone = "good") {
    const kind = ["info", "warning", "error"].includes(tone) ? tone : "";
    $("toast").className = "toast" + (kind ? " " + kind : ""); $("toast").setAttribute("role", kind === "error" ? "alert" : "status");
    $("toast").textContent = text; $("toast").hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => { $("toast").hidden = true; }, kind === "error" ? 7000 : 4500);
  }
  // ---- Human-readable time ------------------------------------------------
  const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
  const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  const pad2 = value => String(value).padStart(2, "0");
  const ordinal = n => n + ((n % 100 >= 11 && n % 100 <= 13) ? "th" : ["th", "st", "nd", "rd"][n % 10] || "th");
  const spoken = list => list.length < 2 ? list.join("") : list.slice(0, -1).join(", ") + " and " + list.at(-1);
  function cronList(field, min, max) {
    // "1,3,5" / "1-5" / "7" → sorted numbers within range, or null when the field uses steps or names.
    if (!/^[0-9,-]+$/.test(field)) return null;
    const values = new Set();
    for (const part of field.split(",")) {
      const range = /^(\d+)(?:-(\d+))?$/.exec(part); if (!range) return null;
      const start = Number(range[1]), end = range[2] === undefined ? start : Number(range[2]);
      if (start > end || start < min || end > max) return null;
      for (let v = start; v <= end; v++) values.add(v);
    }
    return [...values].sort((a, b) => a - b);
  }
  function plural(n, unit) { return n === 1 ? unit : n + " " + unit + "s"; }
  function everyInterval(minutes) {
    if (minutes % 1440 === 0) { const d = minutes / 1440; return d === 7 ? "Every week" : d === 1 ? "Every day" : "Every " + plural(d, "day"); }
    if (minutes % 60 === 0) { const h = minutes / 60; return h === 1 ? "Every hour" : "Every " + plural(h, "hour"); }
    return minutes === 1 ? "Every minute" : "Every " + minutes + " minutes";
  }
  // Plain-English schedule for a 5-field cron expression or a Hermes interval ("every 30m"); falls back to the raw text.
  function describeSchedule(value) {
    const raw = String(value ?? "").trim();
    if (!raw) return "—";
    const macros = { "@hourly": "Every hour", "@daily": "Every day at 00:00", "@midnight": "Every day at 00:00", "@weekly": "Every Sunday at 00:00", "@monthly": "Monthly on the 1st at 00:00", "@yearly": "Every year on January 1st", "@annually": "Every year on January 1st" };
    if (Object.hasOwn(macros, raw.toLowerCase())) return macros[raw.toLowerCase()];
    const interval = /^every\s+(\d+)\s*(m|min|mins|minutes?|h|hours?|d|days?)$/i.exec(raw);
    if (interval) { const n = Number(interval[1]), unit = interval[2][0].toLowerCase(); if (n > 0) return everyInterval(n * (unit === "d" ? 1440 : unit === "h" ? 60 : 1)); }
    const parts = raw.split(/\s+/);
    if (parts.length !== 5) return raw;
    const [minute, hour, dom, month, dow] = parts;
    const minutes = cronList(minute, 0, 59), hours = cronList(hour, 0, 23);
    let when;
    if (minute === "*" && hour === "*") when = "Every minute";
    else if (/^\*\/\d+$/.test(minute) && hour === "*") { const n = Number(minute.slice(2)); if (!n) return raw; when = n === 1 ? "Every minute" : "Every " + n + " minutes"; }
    else if (minutes?.length === 1 && hour === "*") when = minutes[0] === 0 ? "Every hour" : "Every hour at " + pad2(minutes[0]) + " past";
    else if (minutes?.length === 1 && /^\*\/\d+$/.test(hour)) { const n = Number(hour.slice(2)); if (!n) return raw; when = (n === 1 ? "Every hour" : "Every " + n + " hours") + (minutes[0] ? " at " + pad2(minutes[0]) + " past" : ""); }
    else if (minutes?.length === 1 && hours?.length) when = "at " + spoken(hours.map(h => pad2(h) + ":" + pad2(minutes[0])));
    else return raw;
    const timed = when.startsWith("at ");
    const days = dow === "*" ? null : cronList(dow.replace(/\b7\b/g, "0"), 0, 6), dates = dom === "*" ? null : cronList(dom, 1, 31), months = month === "*" ? null : cronList(month, 1, 12);
    if ((dow !== "*" && !days) || (dom !== "*" && !dates) || (month !== "*" && !months) || (days && dates)) return raw;
    let scope;
    if (days) {
      const key = days.join(",");
      scope = key === "1,2,3,4,5" ? "Weekdays" : key === "0,6" ? "Weekends" : key === "0,1,2,3,4,5,6" ? "Every day" : "Every " + spoken(days.map(d => WEEKDAYS[d]));
    } else if (dates) scope = "Monthly on the " + spoken(dates.map(ordinal));
    else scope = timed ? "Every day" : "";
    if (months) scope = (scope || "Every day") + " in " + spoken(months.map(m => MONTHS[m - 1]));
    if (!scope) return when;
    return timed ? scope + " " + when : scope + ", " + when.charAt(0).toLowerCase() + when.slice(1);
  }
  function timestamp(value) {
    if (value === null || value === undefined || value === "") return NaN;
    const t = typeof value === "number" ? (value < 1e12 ? value * 1000 : value) : Date.parse(String(value));
    return Number.isFinite(t) ? t : NaN;
  }
  // "in 5 min", "2 h ago", "just now"; older than a week falls back to the date.
  function formatRelative(value, now = Date.now()) {
    const t = timestamp(value);
    if (Number.isNaN(t)) return "—";
    const diff = t - now, future = diff > 0, abs = Math.abs(diff), minute = 60000, hour = 60 * minute, day = 24 * hour;
    if (abs < minute) return future ? "in under a minute" : "just now";
    let text;
    if (abs < hour) text = Math.round(abs / minute) + " min";
    else if (abs < day) text = Math.round(abs / hour) + " h";
    else if (abs < 7 * day) { const d = Math.round(abs / day); text = d === 1 ? (future ? "tomorrow" : "yesterday") : d + " days"; if (d === 1) return text; }
    else return date(typeof value === "number" ? t / 1000 : value);
    return future ? "in " + text : text + " ago";
  }
  function confirmation(title, text) { const d = $("confirm-dialog"); d.returnValue = "cancel"; $("confirm-title").textContent = title; $("confirm-text").textContent = text; return new Promise(resolve => { d.addEventListener("close", () => resolve(d.returnValue === "accept"), { once: true }); d.showModal(); }); }
  async function api(path, payload, signal) {
    const response = await fetch(path, { method: payload === undefined ? "GET" : "POST", credentials: "same-origin", cache: "no-store", signal, headers: payload === undefined ? { Accept: "application/json" } : { "Content-Type": "application/json", "X-CSRF-Token": state.csrf }, ...(payload === undefined ? {} : { body: JSON.stringify(payload) }) });
    let data;
    try { data = await response.json(); } catch { throw new Error("The server returned an invalid response. Refresh the page."); }
    if (data.csrf) state.csrf = data.csrf;
    if (!response.ok) { if (response.status === 401 && state.user) showLogin(); throw new Error(data.error || "Could not load data from the server."); }
    return data;
  }
  const busyForms = new WeakSet();
  async function busy(form, work) { if (busyForms.has(form)) return; busyForms.add(form); const controls = [...form.querySelectorAll("button, input, textarea, select")], previous = controls.map(n => n.disabled); controls.forEach(n => { n.disabled = true; }); try { await work(); } finally { controls.forEach((n, i) => { n.disabled = previous[i]; }); busyForms.delete(form); } }
  function showLogin() {
    clearTimeout(state.pollTimer); state.user = null; state.csrf = ""; state.data = null; state.request++; state.detailRequest++; state.controller?.abort(); state.history = null; state.editing = null; state.chatMessages = []; state.chatDraft = ""; state.control = null; state.controlRefresh = ""; state.skillCategory = ""; state.logFilter = {}; state.logDraft = null; state.chatBusy = false; state.chatPending = ""; state.chatRequest++; state.filePath = ""; state.pendingAction = null;
    pins = { add: [], remove: [] }; layoutNav();
    $("content").replaceChildren(); $("app").hidden = true; $("login").hidden = false; $("boot").hidden = true;
    document.querySelectorAll("dialog[open]").forEach(d => d.close()); $("memory-form").reset(); $("password-form").reset(); $("action-fields").replaceChildren(); $("password").value = "";
  }
  function showApp(session) {
    state.user = session.user; state.mode = session.mode || "local"; state.runtimeHistory = session.runtime_history === true;
    pins = loadPins(); layoutNav();
    $("demo-banner").hidden = state.mode !== "demo"; $("environment").textContent = state.mode === "demo" ? "Demo mode" : "Live data";
    $("connection").className = "status-pill" + (state.mode === "demo" ? " demo" : "");
    $("workspace-mode").textContent = state.mode === "demo" ? "Demo workspace" : state.runtimeHistory ? "Hermes workspace" : "Personal assistant";
    const name = state.user.display_name || state.user.username || "Administrator";
    $("user-avatar").textContent = name.slice(0, 1).toUpperCase(); $("user-name").textContent = name;
    $("user-role").textContent = operator() ? "System owner" : "Administrator";
    $("logout").title = "Sign out " + state.user.username;
    refreshNav();
    $("login").hidden = true; $("boot").hidden = true; $("app").hidden = false;
    const raw = location.hash.slice(1), requested = resolveView(raw), first = Object.keys(views).find(allowed);
    if (!first) { $("content").replaceChildren(empty("No access granted", "Ask the system owner to grant the permissions this account needs.")); return; }
    const target = allowed(requested) && views[requested] ? requested : first;
    navigate(target);
    // A feature script may register the requested page (or the briefing home) after sign-in completes.
    const landing = !raw || raw === "overview" || raw === "home";
    state.requestedView = requested !== target && raw ? requested : landing && state.view === "overview" ? HOME_PAGE : "";
  }
  function homeActive() { return extensions.has(HOME_PAGE) && allowed(HOME_PAGE); }
  function resolveView(view) {
    const name = Object.hasOwn(ALIASES, view) ? ALIASES[view] : view;
    return name === "overview" && homeActive() ? HOME_PAGE : name;
  }
  // ---- Sidebar customization ---------------------------------------------
  // Each viewer can pin any page into Essentials or remove one; stored per browser, Home always stays.
  const PIN_KEY = "hermes-console-sidebar";
  const pinKey = () => PIN_KEY + ":" + encodeURIComponent(state.user?.username || "anonymous");
  // Where a default-essential page goes when the viewer removes it from Essentials.
  const FALLBACK_GROUP = { chat: "agent", routines: "agent", memories: "agent", reports: "integrations", connectors: "integrations", channels: "integrations" };
  const isHome = view => view === "overview" || view === HOME_PAGE;
  const defaultEssential = view => ESSENTIALS.includes(view) || extensions.get(view)?.essential === true || extensions.get(view)?.group === "main";
  function loadPins() {
    const clean = list => Array.isArray(list) ? [...new Set(list.filter(v => typeof v === "string" && /^[a-z][a-z0-9-]{1,31}$/.test(v)))].slice(0, 40) : [];
    try { const data = JSON.parse(localStorage.getItem(pinKey()) || "{}"); return { add: clean(data?.add), remove: clean(data?.remove) }; } catch { return { add: [], remove: [] }; }
  }
  let pins = loadPins();
  function savePins() { try { localStorage.setItem(pinKey(), JSON.stringify(pins)); } catch { /* kept for this page only */ } }
  const customized = () => pins.add.length > 0 || pins.remove.length > 0;
  function essential(view) {
    if (isHome(view)) return true;
    if (pins.remove.includes(view)) return false;
    return pins.add.includes(view) || defaultEssential(view);
  }
  function setPinned(view, on) {
    if (isHome(view) || !views[view]) return;
    pins = { add: pins.add.filter(v => v !== view), remove: pins.remove.filter(v => v !== view) };
    if (on && !defaultEssential(view)) pins.add.push(view);
    if (!on && defaultEssential(view)) pins.remove.push(view);
    savePins(); layoutNav(); refreshNav();
    if ($("customize-dialog").open) $("customize-reset").disabled = !customized();
    if (state.view === view) setAdvanced(!$("advanced-nav").hidden || !on, false);
  }
  function resetPins() { pins = { add: [], remove: [] }; savePins(); layoutNav(); refreshNav(); }
  function navigate(view, page = 1) {
    view = resolveView(view);
    if (!state.user || !views[view] || !allowed(view)) return;
    const changedView = state.view !== view || state.data === null;
    state.requestedView = ""; state.pendingAction = null;
    clearTimeout(state.pollTimer);
    if (state.view !== view) { $("search").value = ""; state.logDraft = null; state.history = null; state.detailRequest++; }
    state.view = view; state.page = page;
    history.replaceState(null, "", "#" + view);
    document.querySelectorAll("[data-view]").forEach(n => { const active = n.dataset.view === view; n.classList.toggle("active", active); if (active) n.setAttribute("aria-current", "page"); else n.removeAttribute("aria-current"); });
    const [title, subtitle] = heading(view); $("page-title").textContent = title; $("page-description").textContent = subtitle;
    state.enterPending = changedView;
    $("search-box").hidden = UNSEARCHABLE.has(view);
    if (!essential(view)) setAdvanced(true, false);
    closeMenu();
    load();
  }
  function closeMenu() { $("sidebar").classList.remove("open"); $("scrim").hidden = true; $("menu-toggle").setAttribute("aria-expanded", "false"); }
  // The Advanced area's open/closed state is a per-browser convenience; storage may be unavailable.
  function setAdvanced(open, remember) {
    $("advanced-nav").hidden = !open; $("advanced-toggle").setAttribute("aria-expanded", String(open));
    if (remember) { try { localStorage.setItem("hermes-console-advanced", open ? "open" : "closed"); } catch { /* preference not saved */ } }
  }
  function storedAdvanced() { try { return localStorage.getItem("hermes-console-advanced") === "open"; } catch { return false; } }
  // Theme preference is a per-browser convenience; storage may be unavailable.
  function storedTheme() { try { return localStorage.getItem("hermes-console-theme"); } catch { return null; } }
  function systemDark() { try { return window.matchMedia("(prefers-color-scheme: dark)").matches; } catch { return false; } }
  function currentTheme() { return document.documentElement?.dataset.theme || (systemDark() ? "dark" : "light"); }
  function applyTheme(theme, remember) {
    const root = document.documentElement; if (!root) return;
    if (theme === "dark" || theme === "light") root.dataset.theme = theme; else delete root.dataset.theme;
    if (remember) { try { localStorage.setItem("hermes-console-theme", theme); } catch { /* preference not saved */ } }
    $("theme-icon").setAttribute("href", currentTheme() === "dark" ? "#i-sun" : "#i-moon");
  }
  async function load(options) {
    const request = ++state.request, user = state.user, quiet = options?.quiet === true; if (!user) return;
    clearTimeout(state.pollTimer);
    if (!quiet) { state.data = null; state.history = null; state.detailRequest++; }
    state.controller?.abort(); state.controller = new AbortController(); $("page-error").hidden = true; $("refresh").disabled = true;
    if (!quiet) $("content").replaceChildren(node("div", "loading", "Loading data…"));
    let path = "/api/assistant/" + (API_VIEW[state.view] || state.view);
    const ext = extensions.get(state.view);
    if (ext && typeof ext.query === "function") { const query = new URLSearchParams(); for (const [key, value] of Object.entries(ext.query() || {})) if (/^[a-z][a-z0-9_]{0,31}$/.test(key) && ["string", "number"].includes(typeof value) && String(value) !== "") query.set(key, String(value).slice(0, 200)); if ([...query.keys()].length) path += "?" + query; }
    if (state.view === "files") path += "?" + new URLSearchParams({ path: state.filePath });
    if (state.view === "sessions") path = (state.runtimeHistory ? "/api/assistant/sessions?page=" : "/api/sessions?page=") + state.page;
    if (state.view === "memories") path = "/api/memories?page=" + state.page;
    if (state.view === "access") path = "/api/admin/accounts?page=" + state.page;
    if (state.view === "logs") { const query = new URLSearchParams(); for (const key of LOG_FILTERS) if (state.logFilter[key]) query.set(key, state.logFilter[key]); if ([...query.keys()].length) path += "?" + query; }
    try {
      const data = await api(path, undefined, state.controller.signal);
      if (request !== state.request || state.user !== user) return;
      state.data = data; if (state.controlRefresh === state.view) state.controlRefresh = ""; $("updated").textContent = "Updated " + date(data.updated_at || Date.now() / 1000);
      $("source-label").textContent = data.source === "demo" || state.mode === "demo" ? "Source: demo data" : data.source ? "Source: " + data.source : "Source: stored records";
      render();
      runPendingAction();
    } catch (e) { if (request !== state.request || state.user !== user || e.name === "AbortError") return; if (quiet && state.data) { notice($("page-error"), e); return; } const failed = empty("Data unavailable", "Check the connection and refresh. Unknown values are shown as unavailable, not zero."); if (state.view === "files" && state.filePath) failed.append(button("Back to workspace root", "button secondary", () => browseFiles(""))); $("content").replaceChildren(failed); notice($("page-error"), e); }
    finally { if (request === state.request) $("refresh").disabled = false; }
  }
  function matches(item) { const q = $("search").value.toLocaleLowerCase("en").trim(); return !q || Object.values(item).filter(v => typeof v === "string" || typeof v === "number").join(" ").toLocaleLowerCase("en").includes(q); }
  function metric(title, value, note, name) { const div = node("div", "metric"), label = node("div", "metric-label"), mark = node("span", "metric-icon"); mark.append(icon(name)); label.append(mark, node("span", "", title)); div.append(label, node("strong", "metric-value", value), node("span", "metric-note", note)); return div; }
  function table(headers, rows) { const wrapper = node("div", "table-scroll"), t = node("table"), head = node("thead"), tr = node("tr"), body = node("tbody"); headers.forEach(h => tr.append(node("th", "", h))); head.append(tr); rows.forEach(cells => { const r = node("tr"); cells.forEach(cell => { const td = node("td"); td.append(cell instanceof Node ? cell : document.createTextNode(String(cell ?? "—"))); r.append(td); }); body.append(r); }); t.append(head, body); wrapper.append(t); return wrapper; }
  function description(strong, small) { const n = node("div"); n.append(node("strong", "", strong)); if (small) n.append(node("small", "", small)); return n; }
  function chart(daily = []) {
    if (!daily.length) return empty("No activity series yet", "The chart appears once telemetry records model requests.", "chart");
    const ns = "http://www.w3.org/2000/svg", svg = document.createElementNS(ns, "svg"); svg.setAttribute("viewBox", "0 0 560 140"); svg.setAttribute("class", "chart"); svg.setAttribute("role", "img"); svg.setAttribute("aria-label", "Daily model requests");
    const data = [...daily].sort((a, b) => String(a.day).localeCompare(String(b.day))).slice(-14), values = data.map(d => Math.max(0, Number(d.calls) || 0)), max = Math.max(1, ...values), step = 540 / data.length;
    for (let y = 12; y <= 132; y += 40) { const line = document.createElementNS(ns, "line"); for (const [k,v] of Object.entries({x1:0,x2:560,y1:y,y2:y,class:"grid"})) line.setAttribute(k, String(v)); svg.append(line); }
    data.forEach((d, i) => { const bar = document.createElementNS(ns, "rect"), height = values[i] / max * 105; for (const [k,v] of Object.entries({x:10+i*step,y:132-height,width:Math.min(34,step*.6),height,rx:4,class:i === data.length - 1 ? "bar latest" : "bar"})) bar.setAttribute(k, String(v)); const title = document.createElementNS(ns, "title"); title.textContent = d.day + ": " + values[i] + " requests"; bar.append(title); svg.append(bar); });
    const wrap = node("div"); wrap.append(svg); const labels = node("div", "chart-labels"); labels.append(node("span", "", data[0].day), node("span", "", data[data.length - 1].day)); wrap.append(labels); return wrap;
  }
  function services(items) { const list = node("div", "panel-body"); for (const c of items.filter(matches)) { const row = node("div", "service"), info = node("div", "service-info"), good = ["ready", "enabled", "active", "available", "connected", "ok"].includes(c.status); info.append(node("strong", "", c.label || c.name || c.id), node("small", "", c.description || c.message || "")); row.append(iconBox(good ? "check" : c.status === "warning" ? "alert" : "minus", good ? "good" : c.status === "warning" ? "warning" : ""), info, badge(c.status_label || (good ? (c.id === "sandbox" ? "Configured" : "Data available") : c.status === "disabled" ? "Disabled" : c.status === "warning" ? "Needs review" : "Unverified"), good ? "good" : c.status === "warning" ? "warning" : "neutral")); list.append(row); } return list; }
  function overview(data, root) {
    const counts = data.counts || {}, usage = data.usage || {}, runtime = data.runtime || {};
    const hero = node("section", "agent-hero"), avatar = node("span", "agent-avatar"), info = node("div", "agent-info"), name = node("h2", "", "Hermes assistant");
    avatar.append(icon("bot"));
    name.append(badge(state.mode === "demo" ? "Demo mode" : "Live data", state.mode === "demo" ? "warning" : "good"));
    const facts = node("div", "agent-facts");
    for (const [label, value] of [["Mode", runtime.bot_mode], ["Sandbox", runtime.sandbox_backend], ["Version", runtime.hermes_commit ? String(runtime.hermes_commit).slice(0, 10) : null]]) { const fact = node("span"); fact.append(node("b", "", label), node("span", "", value || "unknown")); facts.append(fact); }
    info.append(name, node("p", "", "Your personal assistant across Zalo, Telegram and the web console."), facts);
    const quick = node("div", "quick-actions");
    if (allowed("chat")) quick.append(iconButton("Open chat", "chat", "button primary", () => navigate("chat")));
    if (allowed("activity")) quick.append(iconButton("Agent activity", "activity", "button secondary", () => navigate("activity")));
    else if (allowed("sessions")) quick.append(iconButton("Conversations", "history", "button secondary", () => navigate("sessions")));
    if (allowed("memories")) quick.append(iconButton("Add memory", "plus", "button secondary", () => { navigate("memories"); editMemory(); }));
    hero.append(avatar, info, quick); root.append(hero);
    const metrics = node("div", "metrics");
    metrics.append(metric("Conversations", number(counts.sessions), "Stored in the Hermes runtime", "history"), metric("Model requests", number(usage.calls ?? usage.api_calls), "From available telemetry", "activity"), metric("Tokens", typeof usage.input_tokens === "number" && typeof usage.output_tokens === "number" ? number(usage.input_tokens + usage.output_tokens) : "—", "Input + output", "hash"), metric("Recorded cost", money(usage.cost_usd), usage.cost_usd == null ? "No estimate without pricing" : "All sessions in this home", "dollar")); root.append(metrics);
    const cols = node("div", "columns"), use = panel("Activity", "Daily model requests · last 14 days", button("Usage details", "text-button", () => navigate("usage"))), useBody = node("div", "panel-body"), caption = node("div", "chart-caption"); caption.append(node("strong", "", number(usage.calls ?? usage.api_calls)), node("span", "", "requests recorded")); useBody.append(caption, chart(data.daily || usage.daily || []), node("div", "legend", "Model requests · includes retries")); use.append(useBody);
    const health = panel("Health", "Capabilities and data sources"); health.append(services(data.capabilities || [])); cols.append(use, health); root.append(cols);
    const activity = panel("Recent activity", "Operational metadata only, never message content"), rows = (data.activity || []).filter(matches).slice(0, 8);
    const kinds = { api_call: ["Model call", "cpu"], routine: ["Scheduled job", "calendar"], session: ["Conversation", "chat"] };
    if (rows.length) { const list = node("ul", "timeline"); for (const r of rows) { const [kind, mark] = kinds[r.kind] || [r.kind || "Activity", "activity"], item = node("li"), failed = /error|fail/i.test(String(r.status || "")); item.append(iconBox(mark, failed ? "warning" : "accent"), description(r.label || r.name || "Model request", kind + (r.model ? " · " + r.model : "")), badge(r.status || "Recorded", failed ? "error" : r.status === "active" ? "info" : "good"), node("time", "", date(r.at || r.ts))); item.children[1].className = "service-info"; list.append(item); } activity.append(list); }
    else activity.append(empty("No recorded activity", "Activity appears here once the assistant handles new requests.", "activity"));
    root.append(activity);
  }
  function pagination(data, container, callback) { const total = data.total, limit = data.limit || 20, page = data.page || state.page; if (typeof total !== "number" || total <= limit) return; const wrap = node("div", "pagination"), prev = button("← Previous", "button secondary", () => callback(page - 1)), next = button("Next →", "button secondary", () => callback(page + 1)); prev.disabled = page <= 1; next.disabled = page * limit >= total; wrap.append(prev, node("span", "", `Page ${page} / ${Math.ceil(total / limit)}`), next); container.append(wrap); }
  function users(data, root) {
    const metrics = node("div", "metrics");
    metrics.append(metric("Configured IDs", number(data.total ?? data.items?.length), "Explicit allowlist entries", "users"),
      metric("Today's turns", number(data.total_used_today), data.day ? "Date " + data.day + " · Vietnam time" : "Quota data unavailable", "history"),
      metric("Per-user limit", number(data.user_daily_limit), "Configured turns per day", "sliders"),
      metric("Global limit", number(data.total_daily_limit), "Configured turns per day", "chart"));
    root.append(metrics, data.message ? noteFrom(data.message) : note("Explicit allowlist entries only", "The gateway checks group access, registration and revocation separately."));
    const box = panel("Allowed users", "Read-only · missing usage is shown as —"), items = (data.items || []).filter(matches);
    box.append(items.length ? table(["User ID", "Channel", "Today", "Limit", "Status"], items.map(u => [node("span", "mono", u.user_id),
      badge(u.platform === "telegram" ? "Telegram" : "Zalo", "info"), number(u.used_today), number(u.user_daily_limit ?? data.user_daily_limit), badge("Configured", "neutral")]))
      : empty("No matching IDs", "Configure the channel allowlist to grant access. This page does not grant permissions."));
    root.append(box);
  }
  function sessions(data, root) {
    const box = panel("Conversations", "Only permitted history from the configured conversation store"), items = (data.items || []).filter(matches);
    if (items.length) { const rows = items.map(s => [button(s.title || s.member_id || s.user_id || "Conversation", "text-button", () => openSession(s, 1)), badge(isGroup(s.chat_type) ? "Group" : s.runtime_session_id ? "Runtime" : "Personal", "info"), node("span", "mono", s.chat_id || s.source || "—"), number(s.message_count), date(s.updated_at || s.updated)]); box.append(table(["Session / user", "Scope", "Channel", "Messages", "Updated"], rows)); }
    else box.append(empty("No matching conversations", "History appears only when storage is enabled and users have been informed.")); root.append(box); pagination(data, root, p => navigate("sessions", p)); if (state.history) root.append(state.history);
  }
  async function openSession(s, page) {
    const user = state.user, request = ++state.detailRequest, view = state.view;
    const box = panel("Conversation messages", (s.title || s.member_id || s.user_id || "") + " · " + (s.source || (isGroup(s.chat_type) ? "Group" : "Personal"))); box.classList.add("session-detail"); box.append(node("div", "loading", "Loading history…")); state.history = box; render();
    const path = s.runtime_session_id ? "/api/assistant/history?" + new URLSearchParams({ session_id: s.runtime_session_id, page }) : "/api/history?" + new URLSearchParams({ session_key: s.session_key, page });
    try { const data = await api(path); if (user !== state.user || view !== state.view || request !== state.detailRequest) return; box.lastChild.remove(); const messages = node("div", "messages"); for (const m of [...(data.items || [])].reverse()) { const div = node("div", "message " + (m.role === "assistant" ? "assistant" : "user")); div.append(node("small", "", (m.role === "assistant" ? "Hermes" : "User") + " · " + date(m.created_at || m.created)), node("span", "", m.content || m.body || "")); messages.append(div); } box.append(data.items?.length ? messages : empty("No messages", data.message || "No message content is stored for this session.")); pagination(data, box, p => openSession(s, p)); box.scrollIntoView({ block: "nearest", behavior: "smooth" }); } catch (e) { if (user === state.user && view === state.view && request === state.detailRequest) { box.lastChild.remove(); box.append(empty("Could not open conversation", e.message)); } }
  }
  function editMemory(item = null) {
    if (!allowed("memories")) return; state.editing = item; $("memory-form").reset(); $("memory-error").hidden = true; $("memory-title").textContent = item ? "Edit memory" : "Add memory"; $("memory-content").value = item?.content || ""; $("memory-member").value = item?.member_id || ""; $("memory-member").readOnly = Boolean(item); $("memory-member").required = !item || item.scope === "personal"; $("memory-owner-note").textContent = item ? "Editing preserves the memory's scope and source." : "New memories are created with personal scope."; $("memory-dialog").showModal(); $("memory-content").focus();
  }
  function memories(data, root) {
    const toolbar = node("div", "section-toolbar"); toolbar.append(node("p", "", "Managed memory store · " + number(data.total) + " items"), iconButton("Add memory", "plus", "button primary", () => editMemory())); root.append(toolbar);
    root.append(note("Saved memories may not be in use yet", "This is the existing managed memory store. A saved memory does not confirm that the assistant loop is using it."));
    const cards = node("div", "cards"); for (const m of (data.items || []).filter(matches)) { const card = node("article", "card"), head = node("div", "card-header"), active = Boolean(m.active); head.append(iconBox("memory"), badge(active ? "Stored" : "Inactive", active ? "good" : "neutral")); card.append(head, node("h2", "", m.member_id || (m.scope === "group" ? "Group memory" : "Session memory")), node("p", "", m.content)); const meta = node("div", "card-meta"); meta.append(node("span", "", m.scope === "personal" ? "Personal" : m.scope === "group" ? "Group" : "Session"), node("span", "", "Edited " + date(m.updated)), node("span", "", "v" + (m.revision || 1))); card.append(meta); const actions = node("div", "card-actions"); actions.append(button("Edit", "text-button", () => editMemory(m))); if (active) actions.append(button("Stop using", "text-button", async () => { if (state.actionBusy) return; state.actionBusy = true; const user = state.user; try { if (!await confirmation("Stop using this memory?", "The content stays in history but will no longer be selected when preparing context.")) return; if (user !== state.user || !allowed("memories")) return; await api("/api/memory/stop", { id: m.id, expected_revision: m.revision, confirm: true }); if (user === state.user) { toast("Memory deactivated."); await load(); } } catch (e) { if (user === state.user) notice($("page-error"), e); } finally { state.actionBusy = false; } })); card.append(actions); cards.append(card); }
    root.append(cards.childElementCount ? cards : empty("No matching memories", "Add a personal memory or change your filter.", "memory")); pagination(data, root, p => navigate("memories", p));
  }
  function usage(data, root) { const u = data.usage || data.summary || data, metrics = node("div", "metrics"); metrics.append(metric("API requests", number(u.calls ?? u.api_calls), "Includes recorded retries", "activity"), metric("Input tokens", number(u.input_tokens), "As reported by the provider", "hash"), metric("Output tokens", number(u.output_tokens), "As reported by the provider", "hash"), metric("Cost", money(u.cost_usd), u.cost_usd == null ? "Pricing unavailable" : "All Hermes sessions · " + (u.cost_status === "actual" ? "actual" : "estimated"), "dollar")); root.append(metrics); const box = panel("Model requests", "Metadata only · prompts and keys are never shown"), items = (data.items || []).filter(matches); if (items.length) box.append(table(["Model", "Input", "Output", "Duration", "Result", "Time"], items.map(r => [description(r.model || "Unknown", r.provider), number(r.input_tokens), number(r.output_tokens), typeof r.duration_s === "number" ? r.duration_s.toFixed(2) + " s" : "—", badge(r.error_type ? "Error" : r.status || "Recorded", r.error_type ? "error" : "good"), date(r.ts || r.at)]))); else box.append(empty("No recorded requests", data.message || "Enable assistant-core telemetry to inspect individual requests.", "chart")); root.append(box); }
  function definition(entries) { const dl = node("dl", "definition"); for (const [key, value] of entries) dl.append(node("dt", "", key), node("dd", "", value ?? "Unknown")); return dl; }
  function runtime(data, root) {
    const r = data.runtime || data, resources = data.resources || r.resources || {}, cols = node("div", "details-grid"), sys = panel("Hermes runtime", "The console process and selected runtime"), sysBody = node("div", "panel-body");
    sysBody.append(definition([["Bot mode", r.bot_mode], ["Hermes version", r.hermes_commit], ["Sandbox", r.sandbox_backend], ["Platform", r.platform || r.os], ["Process uptime", typeof r.uptime_seconds === "number" ? number(Math.floor(r.uptime_seconds / 60)) + " minutes" : null], ["CPU count", typeof resources.cpu_count === "number" ? number(resources.cpu_count) : null], ["1-minute load", typeof resources.load_1m === "number" ? resources.load_1m.toFixed(2) : null]])); sys.append(sysBody);
    const resource = panel("Server resources", "Current resource readings · not a monitoring history"), body = node("div", "panel-body");
    for (const [label, used, total] of [["Memory", resources.memory_used_bytes, resources.memory_total_bytes], ["Data disk", resources.disk_used_bytes, resources.disk_total_bytes]]) { const part = node("div", "resource"); part.append(node("h3", "", label)); if (typeof used === "number" && typeof total === "number" && total > 0) { const progress = node("progress", "meter"); progress.max = total; progress.value = used; progress.setAttribute("aria-label", label); const caption = node("div", "meter-caption"); caption.append(node("span", "", bytes(used) + " / " + bytes(total)), node("span", "", Math.round(used / total * 100) + "%")); part.append(progress, caption); } else part.append(node("p", "muted", "No resource readings are available in this environment.")); body.append(part); }
    resource.append(body); cols.append(sys, resource); root.append(cols, note("Read-only status", "This console inspects status without exposing a terminal or Docker access. Use the existing operations tools to restart, update or reconfigure the server."));
  }
  function access(data, root) { const box = panel("Administrator accounts", "Web accounts are separate from Zalo identities", (() => { const a = node("a", "button secondary", "Manage accounts"); a.href = "/legacy.html"; return a; })()), items = (data.items || []).filter(matches); box.append(items.length ? table(["Account", "Role", "Permissions", "Status"], items.map(a => [description(a.display_name || a.username, a.username), a.role === "faculty" ? "System owner" : "Administrator", (a.permissions || []).join(", ") || "No permissions", badge(a.active ? "Active" : "Locked", a.active ? "good" : "neutral")])) : empty("No matching accounts", "Try a different search.")); root.append(box); pagination(data, root, p => navigate("access", p)); root.append(note("Conversation and memory access is granted separately", "System owners can inspect operations. Conversation and memory permissions are granted separately.")); }


  function metadata(data, root) {
    const plan = actionPlan(data, data.items || []);
    if (plan.global.length || plan.orphan.length) controlToolbar(views[state.view][1], plan, root);
    const summary = data.summary;
    if (summary && typeof summary === "object" && !Array.isArray(summary)) {
      const card = panel("Summary", "Metadata from the selected runtime");
      const body = node("div", "panel-body");
      body.append(definition(Object.entries(summary).map(([key, value]) => [humanize(key), displayValue(value)])));
      card.append(body);
      root.append(card);
    }
    const items = (data.items || []).filter(matches);
    if (!items.length) {
      root.append(empty("No records available", data.message || "This section has no connected data yet."));
      return;
    }
    const records = node("div", "cards metadata-cards");
    for (const item of items) {
      const title = item.name || item.label || item.title || item.id || views[state.view][0];
      const card = node("article", "card");
      card.append(node("h2", "", title));
      const details = Object.entries(item).filter(([key]) => !["name", "label", "title", "id"].includes(key));
      card.append(definition(details.map(([key, value]) => [humanize(key), displayValue(value)])));
      if (plan.row(item).length) card.append(actionRow(plan.row(item)));
      if (state.view === "documentation" && typeof item.url === "string") {
        const href = safeDocumentationUrl(item.url);
        if (href) {
          const link = node("a", "text-button reference-link", "Open documentation");
          link.href = href;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          card.append(link);
        }
      }
      records.append(card);
    }
    root.append(records);
  }

  function humanize(value) {
    return String(value).replaceAll("_", " ").replace(/\b\w/g, char => char.toUpperCase());
  }

  function displayValue(value) {
    if (value === null || value === undefined) return "Unavailable";
    if (typeof value === "boolean") return value ? "Yes" : "No";
    if (Array.isArray(value)) return value.map(displayValue).join(", ") || "None";
    if (typeof value === "object") return Object.entries(value).map(([key, item]) => humanize(key) + ": " + displayValue(item)).join(" · ");
    return String(value);
  }

  function safeDocumentationUrl(value) {
    try {
      const url = new URL(value);
      if (url.protocol !== "https:" || url.username || url.password) return null;
      if (!["hermes-agent.nousresearch.com", "github.com", "core.telegram.org", "docs.zaloplatforms.com"].includes(url.hostname)) return null;
      return url.href;
    } catch { return null; }
  }

  function browseFiles(path) {
    if (!allowed("files")) return;
    state.filePath = path || "";
    load();
  }

  function files(data, root) {
    const toolbar = node("div", "section-toolbar");
    toolbar.append(node("p", "mono", "/workspace/" + (data.path || "")));
    if (data.parent !== null && data.parent !== undefined) toolbar.append(button("Up one folder", "button secondary", () => browseFiles(data.parent)));
    root.append(toolbar);
    if (typeof data.content === "string") {
      const preview = panel("File preview", data.name || data.path || "Workspace file");
      preview.append(node("pre", "file-preview", data.content));
      root.append(preview);
      if (data.truncated) root.append(note("Preview truncated", "This preview was truncated to the server's display limit.", "warning"));
      return;
    }
    const items = (data.items || []).filter(matches);
    const box = panel("Workspace files", "Read-only browsing · hidden files and paths outside the workspace are not exposed");
    if (!items.length) box.append(empty("No files to show", data.message || "This directory is empty or unavailable.", "folder"));
    else box.append(table(["Name", "Type", "Size", "Modified"], items.map(item => [
      (() => { const b = iconButton(item.name, item.kind === "directory" ? "folder" : "file", "text-button file-link", () => browseFiles(item.path)); return b; })(),
      item.kind || "file", bytes(item.size), date(item.modified)
    ])));
    root.append(box);
  }

  async function sendChat(event) {
    event.preventDefault();
    if (state.chatBusy || !allowed("chat") || state.data?.available === false || state.data?.enabled === false) return;
    const input = $("chat-message"), message = input.value.trim(), user = state.user;
    if (!message) return;
    if (message.length > 4000) {
      notice($("chat-error"), new Error("Messages must be 4,000 characters or fewer."));
      return;
    }
    const request = ++state.chatRequest;
    state.chatBusy = true;
    $("chat-send").disabled = true;
    state.chatPending = message;
    $("chat-error").hidden = true;
    const thread = state.view === "chat" ? $("chat-thread") : null;
    if (thread) { if (!state.chatMessages.length) thread.replaceChildren(); thread.append(...pendingBubbles()); if (thread.scrollTo) thread.scrollTo(0, thread.scrollHeight); }
    try {
      const history = state.chatMessages.filter(item => ["user", "assistant"].includes(item.role) && typeof item.content === "string").slice(-12).map(item => ({ role: item.role, content: Array.from(item.content).slice(0, 4000).join("") }));
      while (history.reduce((size, item) => size + item.content.length, 0) > 24000) history.shift();
      const result = await api("/api/assistant/chat", { message, history });
      if (state.user !== user || request !== state.chatRequest) return;
      if (typeof result.reply !== "string") throw new Error("The gateway did not return a text reply.");
      state.chatMessages.push({ role: "user", content: message }, { role: "assistant", content: result.reply });
      state.chatMessages = state.chatMessages.slice(-20);
      const input = $("chat-message");
      if (input && input.value.trim() === message) input.value = "";
      else if (!input && state.chatDraft.trim() === message) state.chatDraft = "";
      state.chatBusy = false; state.chatPending = "";
      if (state.view === "chat") render();
    } catch (error) {
      if (state.user === user && request === state.chatRequest && state.view === "chat") { state.chatBusy = false; state.chatPending = ""; render(); notice($("chat-error"), error); }
    } finally {
      if (request === state.chatRequest) {
        state.chatBusy = false; state.chatPending = "";
        const send = $("chat-send");
        if (send) send.disabled = false;
      }
    }
  }

  const SUGGESTIONS = ["Summarize today's top news in 5 bullet points", "Help me plan my week", "Draft a polite reply declining a meeting"];
  function pendingBubbles() {
    const mine = node("div", "message user"), waiting = node("div", "message assistant thinking");
    mine.append(node("small", "", "You"), node("span", "", state.chatPending));
    waiting.append(node("small", "", "Hermes"), node("span", "", "Thinking…"));
    return [mine, waiting];
  }
  function chat(data, root) {
    const box = panel("Assistant", "Short in-memory thread · cleared when you sign out");
    box.classList.add("chat-panel");
    const disabled = data.available === false || data.enabled === false;
    const messages = node("div", "messages chat-messages"); messages.id = "chat-thread";
    for (const item of state.chatMessages) {
      const message = node("div", "message " + item.role);
      message.append(node("small", "", item.role === "assistant" ? "Hermes" : "You"), node("span", "", item.content));
      messages.append(message);
    }
    const input = node("textarea"); input.id = "chat-message"; input.required = true; input.maxLength = 4000; input.rows = 1;
    if (!state.chatMessages.length && !state.chatBusy) {
      const start = empty("Start a conversation", data.message || "Messages go to your connected Hermes gateway. Nothing is sent automatically.", "chat");
      if (!disabled) { const ideas = node("div", "suggestions"); for (const text of SUGGESTIONS) ideas.append(button(text, "chip", () => { input.value = text; state.chatDraft = text; input.focus(); })); start.append(ideas); }
      messages.append(start);
    }
    if (state.chatBusy && state.chatPending) messages.append(...pendingBubbles());
    box.append(messages);
    const form = node("form", "chat-composer"), label = node("label");
    input.value = state.chatDraft;
    input.setAttribute("aria-label", "Message");
    input.addEventListener("input", () => { state.chatDraft = input.value; });
    input.addEventListener("keydown", event => { if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); if (form.requestSubmit) form.requestSubmit(); } });
    input.placeholder = disabled ? "Connect and enable the Hermes chat endpoint to send messages." : "Message Hermes…";
    input.disabled = disabled;
    label.append(input);
    const error = node("p", "notice error"); error.id = "chat-error"; error.hidden = true; error.setAttribute("role", "alert");
    const send = node("button", "button primary"); send.append(icon("send"), node("span", "", "Send")); send.id = "chat-send"; send.type = "submit";
    send.disabled = disabled || state.chatBusy;
    form.append(label, send);
    form.addEventListener("submit", sendChat);
    box.append(form, node("p", "chat-hint", "Enter to send · Shift + Enter for a new line"), error); root.append(box);
    if (messages.scrollTo) requestAnimationFrame(() => messages.scrollTo(0, messages.scrollHeight));
  }

  // ---- Management actions -------------------------------------------------
  // Descriptors come from the section snapshot. They are untrusted data: every
  // string is rendered with textContent and only known field types are built.
  const LOG_FILTERS = ["q", "level", "file", "component", "lines"];
  // Fixed server allowlist; "all" is expressed by omitting the parameter.
  const LOG_OPTIONS = { level: ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], file: ["agent", "gateway", "errors", "hermes"], component: ["gateway", "agent", "tools", "cli", "cron"], lines: ["50", "100", "200", "500"] };
  const FIELD_TYPES = new Set(["text", "textarea", "password", "number", "checkbox", "select", "hidden"]);
  const FIELD_NAME = /^[A-Za-z0-9_.-]{1,80}$/;
  const primitive = value => ["string", "number", "boolean"].includes(typeof value);
  function validField(field) {
    if (!field || typeof field !== "object" || !FIELD_NAME.test(String(field.name || "")) || !FIELD_TYPES.has(field.type)) return false;
    if (field.value !== undefined && field.value !== null && !primitive(field.value)) return false;
    if (field.type === "hidden" && !primitive(field.value)) return false;
    if (field.type === "select") return Array.isArray(field.options) && field.options.length > 0 && field.options.every(o => o && primitive(o.value) && (o.label === undefined || primitive(o.label)));
    return true;
  }
  function validAction(action) {
    if (!action || typeof action !== "object" || typeof action.id !== "string" || !action.id || action.id.length > 80 || typeof action.label !== "string") return false;
    if (!["string", "number"].includes(typeof action.revision)) return false;
    const fields = action.fields ?? [];
    if (!Array.isArray(fields) || !fields.every(validField)) return false;
    return new Set(fields.map(f => f.name)).size === fields.length;
  }
  // Only sections the server wires to POST /api/assistant/{section}/action.
  const ACTION_SECTIONS = new Set(["models", "config", "keys", "channels", "mcp", "skills", "routines", "plugins", "webhooks"]);
  const actionSection = view => ACTION_SECTIONS.has(view) || extensions.get(view)?.actions === true;
  const sectionActions = data => (Array.isArray(data?.actions) ? data.actions : []).filter(validAction);
  function rowKeys(item) {
    const keys = [item.id, item.name, item.channel, item.variable, item.role, item.key];
    if (item.section && item.setting) keys.push(item.section + "." + item.setting);
    return keys.filter(primitive).map(String);
  }
  // Split actions into global (no target), per-row and orphaned targeted ones.
  function actionPlan(data, items) {
    const all = actionSection(state.view) ? sectionActions(data) : [], rows = new Map(), used = new Set();
    for (const item of items) {
      const keys = rowKeys(item);
      rows.set(item, all.filter(a => a.target !== undefined && a.target !== null && keys.includes(String(a.target))));
      rows.get(item).forEach(a => used.add(a));
    }
    const global = all.filter(a => a.target === undefined || a.target === null);
    const orphan = all.filter(a => !global.includes(a) && !used.has(a));
    return { global, orphan, row: item => rows.get(item) || [] };
  }
  function actionButton(action, cls = "text-button", label = action.label) {
    const b = button(label, cls, () => openAction(action));
    if (action.description) b.title = action.description;
    if (state.controlRefresh === state.view) b.disabled = true;
    return b;
  }
  function actionRow(actions, cls) { const row = node("div", "card-actions"); actions.forEach(a => row.append(actionButton(a, cls))); return row; }
  function controlToolbar(text, plan, root) {
    const toolbar = node("div", "section-toolbar"), buttons = node("div", "toolbar-actions");
    toolbar.append(node("p", "", text));
    plan.global.forEach(a => buttons.append(actionButton(a, "button primary")));
    plan.orphan.forEach(a => buttons.append(actionButton(a, "button secondary", a.label + " · " + a.target)));
    if (buttons.childElementCount) toolbar.append(buttons);
    root.append(toolbar);
    if (state.controlRefresh === state.view) root.append(note("A change did not complete", "Refresh this section before making another change.", "warning"));
  }
  function sectionMessage(data, root) { if (data.available !== false && data.message) root.append(noteFrom(data.message)); }

  function openAction(action, presentation = null) {
    const view = state.view, data = state.data;
    if (!state.user || !allowed(view) || !actionSection(view) || !data || !sectionActions(data).includes(action)) return;
    if (state.controlRefresh === view) { toast("Refresh this section before making another change.", "warning"); return; }
    if (state.actionBusy) return;
    const controls = [], container = $("action-fields"), advanced = node("details", "action-advanced");
    advanced.append(node("summary", "", "Advanced settings"));
    container.replaceChildren();
    (action.fields || []).forEach((field, index) => {
      if (field.type === "hidden") { controls.push({ field }); return; }
      const id = "action-field-" + index, label = node("label", field.type === "checkbox" ? "check-field" : "");
      let control;
      if (field.type === "textarea") { control = node("textarea"); control.rows = 5; }
      else if (field.type === "select") {
        control = node("select");
        for (const o of field.options) { const option = node("option", "", o.label ?? o.value); option.value = String(o.value); control.append(option); }
      } else {
        control = node("input"); control.type = field.type;
        if (field.type === "password") { control.autocomplete = "new-password"; control.setAttribute("autocomplete", "new-password"); }
        else control.setAttribute("autocomplete", "off");
      }
      control.id = id;
      if (field.type === "checkbox") control.checked = field.value === true;
      else if (field.type !== "password" && field.value !== undefined && field.value !== null) control.value = String(field.value);
      if (field.required && field.type !== "checkbox") control.required = true;
      if (Number.isInteger(field.max_length) && field.max_length > 0 && ["text", "textarea", "password"].includes(field.type)) control.maxLength = field.max_length;
      if (field.type === "checkbox") label.append(control, node("span", "", field.label || field.name));
      else label.append(node("span", "", field.label || field.name), control);
      if (field.help) label.append(node("small", "field-help", field.help));
      else if (field.type === "password") label.append(node("small", "field-help", "Write-only. Leave blank to keep the current value."));
      (presentation?.advanced?.includes(field.name) ? advanced : container).append(label);
      controls.push({ field, control, label });
    });
    if (advanced.childElementCount > 1) container.append(advanced);
    const review = node("div", "action-review"); review.hidden = true; container.append(review);
    state.control = { action, view, data, user: state.user, controls, presentation, step: 0, advanced, review };
    $("action-title").textContent = presentation?.title || action.label;
    $("action-description").textContent = presentation?.description || action.description || (action.target !== undefined && action.target !== null ? "Target: " + action.target : "");
    $("action-description").hidden = !$("action-description").textContent;
    $("action-error").hidden = true;
    $("action-submit").disabled = false;
    $("action-submit").textContent = presentation ? "Save connection" : "Apply change";
    $("action-back").onclick = () => { if (!state.actionBusy) { state.control.step = 0; updateActionFlow(); } };
    $("action-next").onclick = advanceActionFlow;
    controls.forEach(({ control }) => control?.addEventListener("change", updateActionFlow));
    updateActionFlow();
    $("action-dialog").showModal();
    controls.find(c => c.control)?.control.focus();
  }
  function fieldApplies(field, controls) {
    if (!field.when) return true;
    const dependency = controls.find(c => c.field.name === field.when.field);
    return String(dependency?.control?.value ?? dependency?.field.value ?? "") === String(field.when.value);
  }
  function updateActionFlow() {
    const current = state.control; if (!current) return;
    const { controls, presentation, step, review, advanced } = current;
    for (const { field, control, label } of controls) {
      const applies = fieldApplies(field, controls);
      if (label) label.hidden = !applies || Boolean(presentation && step === 1);
      if (control) control.required = applies && !step && field.required === true && field.type !== "checkbox";
      if (control && !applies && field.type === "password") control.value = "";
    }
    advanced.hidden = Boolean(presentation && step === 1);
    review.hidden = !presentation || step !== 1;
    $("action-flow-state").hidden = !presentation;
    $("action-flow-state").textContent = presentation ? (step ? "2 of 2 · Review connection" : "1 of 2 · Connection details") : "";
    $("action-back").hidden = !presentation || step === 0;
    $("action-next").hidden = !presentation || step === 1;
    $("action-submit").hidden = Boolean(presentation && step === 0);
    if (presentation && step === 1) {
      const values = actionValues(controls);
      review.replaceChildren(node("h3", "", "Review before saving"));
      const rows = controls.filter(c => c.control && fieldApplies(c.field, controls)).map(({ field }) => [field.label || field.name,
        field.type === "password" ? "Provided · never shown again" : field.type === "checkbox" ? (values[field.name] ? "Yes" : "No") : String(values[field.name] ?? "—")]);
      review.append(definition(rows), node("p", "muted", presentation.review || "Save once, then test the connection. Runtime use is not verified."));
    }
  }
  function advanceActionFlow() {
    if (!state.control || !state.control.presentation || state.actionBusy) return;
    try {
      const values = actionValues(state.control.controls);
      state.control.presentation.validate?.(values);
      state.control.step = 1; $("action-error").hidden = true; updateActionFlow(); $("action-submit").focus();
    } catch (error) { notice($("action-error"), error); }
  }
  function actionValues(controls) {
    const values = {};
    for (const { field, control } of controls) {
      const name = field.label || field.name;
      if (!fieldApplies(field, controls)) { values[field.name] = ""; continue; }
      if (!control) { values[field.name] = field.value; continue; }
      if (field.type === "checkbox") { values[field.name] = Boolean(control.checked); continue; }
      const raw = String(control.value ?? ""), text = field.type === "text" ? raw.trim() : raw;
      if (field.required && !text.trim()) throw new Error(name + " is required.");
      if (Number.isInteger(field.max_length) && Array.from(text).length > field.max_length) throw new Error(name + " must be " + number(field.max_length) + " characters or fewer.");
      if (field.type === "number") {
        if (!text.trim()) { values[field.name] = null; continue; }
        const parsed = Number(text);
        if (!Number.isFinite(parsed)) throw new Error(name + " must be a number.");
        values[field.name] = parsed; continue;
      }
      if (field.type === "select" && !field.options.some(o => String(o.value) === text)) throw new Error("Choose a valid option for " + name + ".");
      if (field.type === "select") { const option = field.options.find(o => String(o.value) === text); values[field.name] = option.value; continue; }
      values[field.name] = text;
    }
    return values;
  }
  function clearSecrets() { for (const c of state.control?.controls || []) if (c.field.type === "password" && c.control) c.control.value = ""; }
  async function submitAction(event) {
    event?.preventDefault();
    const current = state.control;
    if (!current || state.actionBusy || current.user !== state.user || current.view !== state.view || current.data !== state.data || !allowed(current.view) || !actionSection(current.view)) return;
    if (current.presentation && current.step === 0) { advanceActionFlow(); return; }
    if (state.controlRefresh === current.view) { notice($("action-error"), new Error("Refresh this section before making another change.")); return; }
    $("action-error").hidden = true;
    let values;
    try { values = actionValues(current.controls); } catch (error) { notice($("action-error"), error); return; }
    state.actionBusy = true; $("action-submit").disabled = true; $("action-back").disabled = true; $("action-next").disabled = true;
    const { action, view, user } = current;
    try {
      const target = action.target !== undefined && action.target !== null ? " · " + action.target : "";
      if (!await confirmation(action.label + "?", "Apply \"" + action.label + target + "\". The change is saved once; nothing is retried automatically.")) return;
      if (state.user !== user || state.view !== view || state.control !== current || !allowed(view)) return;
      const payload = { action: action.id, expected_revision: action.revision, confirm: true, values };
      const result = await api("/api/assistant/" + view + "/action", payload);
      if (state.user !== user) return;
      if (!result || result.ok !== true) throw new Error("The server did not confirm the change.");
      if (state.control === current) $("action-dialog").close();
      toast(typeof result.message === "string" && result.message ? result.message : "Change saved.");
      if (state.view === view) await load();
    } catch (error) {
      if (state.user !== user) return;
      state.controlRefresh = view;
      const message = new Error((error?.message || String(error)) + " Refresh this section before trying again.");
      if (state.control === current && $("action-dialog").open) { clearSecrets(); notice($("action-error"), message); }
      else notice($("page-error"), message);
      if (state.view === view) render();
    } finally {
      state.actionBusy = false;
      $("action-back").disabled = false; $("action-next").disabled = false;
      if (state.control === current) {
        if ($("action-dialog").open) $("action-submit").disabled = state.controlRefresh === view;
        else { state.control = null; $("action-fields").replaceChildren(); }
      }
    }
  }

  // ---- Specialized management views ---------------------------------------
  const yesNo = (value, yes, no, unknown = "Unknown") => value === true ? badge(yes, "good") : value === false ? badge(no, "neutral") : badge(unknown, "neutral");
  function models(data, root) {
    const items = (data.items || []).filter(matches), plan = actionPlan(data, items);
    controlToolbar("Model routing for new sessions in this bot home", plan, root); sectionMessage(data, root);
    const main = items.find(i => String(i.role).toLowerCase() === "main"), aux = items.filter(i => i !== main);
    const hero = panel("Main model", "Used for conversations and tool calls", main ? badge(main.provider || "auto", "info") : null), body = node("div", "panel-body model-hero");
    if (main) { body.append(node("strong", "model-name", main.model || "Not set"), node("p", "muted", [main.provider && "Provider: " + main.provider, main.base_url ? "Endpoint: " + main.base_url : main.base_url_configured && "Custom endpoint configured", main.credential_in_config && "Key stored in config.yaml", main.context_length && "Context " + number(main.context_length)].filter(Boolean).join(" · ") || "Provider selected automatically")); body.append(actionRow(plan.row(main), "button secondary")); }
    else body.append(empty("No main model configured", "Set a main model to route new conversations.", "cpu"));
    hero.append(body); root.append(hero);
    const box = panel("Auxiliary models", "Side tasks such as vision, compression and web extraction");
    box.append(aux.length ? table(["Task", "Model", "Provider", ""], aux.map(i => [description(humanize(i.role || i.id || "Task"), i.description), i.model ? node("span", "mono", i.model) : i.uses_main === false ? "Provider default" : "Uses main model", i.provider || "auto", actionRow(plan.row(i))])) : empty("No auxiliary overrides", "Auxiliary tasks use the main model or Hermes defaults.", "cpu"));
    root.append(box);
  }
  function keys(data, root) {
    const items = (data.items || []).filter(matches), plan = actionPlan(data, items), configured = items.filter(i => i.configured === true).length;
    controlToolbar(number(configured) + " of " + number(items.length) + " credentials configured", plan, root); sectionMessage(data, root);
    const groups = new Map();
    for (const i of items) { const g = i.provider || i.category || "Other"; if (!groups.has(g)) groups.set(g, []); groups.get(g).push(i); }
    if (!groups.size) { root.append(empty("No credentials listed", data.message || "No known credentials are available for this bot home.", "key")); return; }
    for (const [group, rows] of groups) {
      const box = panel(group, null);
      box.append(table(["Variable", "Status", "Source", ""], rows.map(i => [description(i.variable || i.name, i.notice || i.note || i.label || i.description), yesNo(i.configured, "Configured", "Not set"), i.source || (i.configured ? "Environment" : "—"), i.editable === false ? badge("Managed elsewhere", "neutral") : actionRow(plan.row(i))])));
      root.append(box);
    }
    root.append(note("Secret values are write-only", "They are never displayed or prefilled. Process environment variables can take precedence over the bot home .env file."));
  }
  function channels(data, root) {
    const items = (data.items || []).filter(matches), plan = actionPlan(data, items);
    controlToolbar("Messaging channels for this bot home", plan, root); sectionMessage(data, root);
    if (!items.length) { root.append(empty("No channels configured", data.message || "Add a channel credential to connect a messaging platform.", "send")); return; }
    const cards = node("div", "cards");
    for (const c of items) {
      const card = node("article", "card"), head = node("div", "card-header");
      head.append(iconBox("send"), yesNo(c.enabled, "Enabled", "Disabled", "Default"));
      card.append(head, node("h2", "", humanize(c.label || c.channel || c.name || "Channel")));
      const modes = { auto: "Automatic (when a token exists)", on: "Always", off: "Never" }, replies = { off: "Never", first: "First message only", all: "Every message" };
      card.append(definition([["Start", modes[c.mode] || "—"], ["Credential", c.credential_configured === true ? "Configured" + (c.credential_source ? " · " + c.credential_source : "") : c.credential_configured === false ? "Missing" : "Not required"], ...(c.reply_to_mode ? [["Reply quoting", replies[c.reply_to_mode] || c.reply_to_mode]] : []), ...(typeof c.typing_indicator === "boolean" ? [["Typing indicator", c.typing_indicator ? "On" : "Off"]] : []), ["Connection", c.connection === "not verified" || !c.connection ? "Not verified" : c.connection], ...(c.home_channel_configured !== undefined ? [["Home channel", c.home_channel_configured ? "Configured" : "Not set"]] : [])]));
      if (c.note) card.append(node("p", "muted", c.note));
      card.append(actionRow(plan.row(c))); cards.append(card);
    }
    root.append(cards);
  }
  function config(data, root) {
    const items = (data.items || []).filter(matches), plan = actionPlan(data, items);
    controlToolbar("Allowlisted settings · changes apply to new sessions or after a restart", plan, root); sectionMessage(data, root);
    if (!items.length) { root.append(empty("No settings available", data.message || "No allowlisted settings were found.", "sliders")); return; }
    const settingName = i => i.label || humanize(i.setting || i.name || i.key || i.id || "Setting");
    const groups = [["Editable settings", "Saved to config.yaml in this bot home", items.filter(i => i.editable !== false)], ["Locked settings", "Shown for reference · change these with the Hermes CLI", items.filter(i => i.editable === false)]];
    for (const [title, note, rows] of groups) {
      if (!rows.length) continue;
      const box = panel(title, note);
      box.append(table(["Setting", "Value", "Source", ""], rows.map(i => {
        const actions = plan.row(i), cell = actions.length ? actionRow(actions) : node("div");
        if (i.editable === false) cell.append(badge("Locked", "neutral"));
        return [description(settingName(i), i.help || i.description), node("span", "mono", displayValue(i.value)), i.source || "—", cell];
      })));
      root.append(box);
    }
  }
  function mcp(data, root) {
    const items = (data.items || []).filter(matches), plan = actionPlan(data, items);
    controlToolbar("External tool servers available to the agent", plan, root); sectionMessage(data, root);
    if (!items.length) { root.append(empty("No MCP servers", data.message || "Add a server to give the agent external tools.", "plug")); return; }
    const cards = node("div", "cards");
    for (const s of items) {
      const card = node("article", "card"), head = node("div", "card-header");
      head.append(iconBox("plug"), yesNo(s.enabled, "Enabled", "Disabled"));
      card.append(head, node("h2", "", s.name || s.id || "Server"));
      card.append(definition([["Transport", s.transport || "Unknown"], ...(s.host ? [["Host", s.host]] : []), ["Credentials", s.credentials_configured ? "Configured" : "None"], ...(s.exposed_to !== undefined ? [["Available to", displayValue(s.exposed_to)]] : []), ["Connection", s.connection === "not tested" || !s.connection ? "Not tested" : s.connection], ...(Array.isArray(s.tools) ? [["Tools", displayValue(s.tools)]] : [])]));
      card.append(actionRow(plan.row(s))); cards.append(card);
    }
    root.append(cards);
  }
  function skills(data, root) {
    const all = data.items || [], categories = [...new Set(all.map(s => s.category || "General"))].sort();
    if (state.skillCategory && !categories.includes(state.skillCategory)) state.skillCategory = "";
    const items = all.filter(s => (!state.skillCategory || (s.category || "General") === state.skillCategory) && matches({ ...s, tags: Array.isArray(s.tags) ? s.tags.join(" ") : "" }));
    const categoryRows = (Array.isArray(data.categories) ? data.categories : []).filter(c => c && typeof c.name === "string");
    const plan = actionPlan(data, [...all, ...categoryRows]), enabled = all.filter(s => s.enabled !== false).length;
    controlToolbar(number(enabled) + " enabled · " + number(all.length) + " installed", plan, root); sectionMessage(data, root);
    const filters = node("div", "chip-row"); filters.setAttribute("role", "group"); filters.setAttribute("aria-label", "Filter skills by category");
    for (const [value, label] of [["", "All"], ...categories.map(c => [c, humanize(c)])]) {
      const chip = button(label + " · " + number(value ? all.filter(s => (s.category || "General") === value).length : all.length), "chip" + (state.skillCategory === value ? " active" : ""), () => { state.skillCategory = value; render(); });
      chip.setAttribute("aria-pressed", String(state.skillCategory === value)); filters.append(chip);
    }
    if (categories.length) root.append(filters);
    const selected = categoryRows.find(c => c.name === state.skillCategory);
    if (selected && plan.row(selected).length) { const bar = node("div", "section-toolbar"); bar.append(node("p", "", humanize(selected.name) + " category · " + number(selected.enabled) + " of " + number(selected.total) + " enabled"), actionRow(plan.row(selected), "button secondary")); root.append(bar); }
    const cards = node("div", "cards");
    for (const s of items) {
      const card = node("article", "card"), head = node("div", "card-header");
      head.append(iconBox("sparkles"), s.enabled === false ? badge("Disabled", "neutral") : badge(s.status_label || (s.essential ? "Essential" : "Enabled"), "good"));
      card.append(head, node("h2", "", s.name || s.label || "Skill"), node("p", "", s.description || s.summary || "A reusable workflow in the Hermes library."));
      const meta = node("div", "card-meta"); meta.append(node("span", "", humanize(s.category || "General"))); if (s.version) meta.append(node("span", "", "v" + s.version)); if (Array.isArray(s.tags)) s.tags.slice(0, 4).forEach(t => meta.append(node("span", "", "#" + t)));
      card.append(meta, actionRow(plan.row(s))); cards.append(card);
    }
    root.append(items.length ? cards : empty("No skills found", data.message || "The skill library is empty or nothing matches this filter.", "sparkles"));
    if (data.truncated) root.append(note("Only the first part of the skill library is shown", "", "warning"));
  }
  function runList(runs) {
    const details = node("details", "run-history"); details.append(node("summary", "", "Recent runs · " + number(runs.length)));
    for (const r of runs.slice(0, 10)) {
      const failed = /fail|error/i.test(String(r.status || "")), row = node("div", "run");
      row.append(badge(r.status || "Unknown", failed ? "error" : r.status === "completed" || r.status === "ok" ? "good" : "neutral"), node("span", "muted", date(r.finished_at || r.started_at || r.claimed_at || r.at)));
      if (typeof r.error === "string" && r.error) row.append(node("pre", "run-output", r.error));
      details.append(row);
    }
    return details;
  }
  function latestOutput(latest, files) {
    const box = node("details", "run-history"), count = Array.isArray(files) ? files.length : 0;
    box.append(node("summary", "", "Latest output · " + outputLabel(latest.name, latest.at) + (count > 1 ? " · " + number(count) + " saved" : "")));
    if (typeof latest.excerpt === "string" && latest.excerpt) {
      box.append(node("pre", "run-output", latest.excerpt));
      if (latest.truncated) box.append(node("p", "muted", "Excerpt shortened · redacted output"));
    } else box.append(node("p", "muted", latest.excerpt_omitted ? "Excerpt omitted to keep this page small. Refresh with fewer jobs to see it." : "No readable excerpt for this run."));
    return box;
  }
  function resultLabel(status) {
    const s = String(status ?? "").trim(); if (!s) return "";
    return /^(ok|success|succeeded|completed|done)$/i.test(s) ? "Succeeded" : /fail|error/i.test(s) ? "Failed" : humanize(s);
  }
  function deliveryLabel(deliver) {
    const d = String(deliver || "local"), [kind, ...rest] = d.split(":"), target = rest.join(":");
    if (d === "local") return "Saved in the bot home";
    if (d === "origin") return "Back to the chat that created it";
    const names = { telegram: "Telegram", zalo: "Zalo", discord: "Discord", slack: "Slack", email: "Email" };
    return Object.hasOwn(names, kind) ? names[kind] + (target ? " · " + target : "") : d;
  }
  // "2026-10-31_07-00-04.md" → the run time; other names are shown as they are.
  function outputLabel(name, at) {
    const m = /^(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})\.md$/.exec(String(name || ""));
    if (m) return "Run of " + date(m[1] + "-" + m[2] + "-" + m[3] + "T" + m[4] + ":" + m[5] + ":" + m[6]);
    if (at) return "Run of " + date(at);
    return typeof name === "string" && name ? name : "saved run";
  }
  function routines(data, root) {
    const items = (data.items || []).filter(r => matches({ ...r, skills: Array.isArray(r.skills) ? r.skills.join(" ") : "" })), plan = actionPlan(data, items);
    const total = data.total ?? items.length;
    controlToolbar(number(total) + (total === 1 ? " automation" : " automations") + " in this bot home", plan, root); sectionMessage(data, root);
    if (data.scheduler_active === false) root.append(note("Scheduler not running", "No running Hermes scheduler was detected. Automations run only while the Hermes gateway is running.", "warning"));
    if (!items.length) { root.append(empty("No automations yet", data.message || "Create an automation to have the assistant run reports, reminders and checks on a schedule.", "calendar")); return; }
    const cards = node("div", "cards routine-cards");
    for (const r of items) {
      const paused = r.enabled === false || r.paused || r.state === "paused", card = node("article", "card"), head = node("div", "card-header");
      const failed = Boolean(r.last_error || r.last_delivery_error) || /fail|error/i.test(String(r.last_status || ""));
      head.append(iconBox("calendar"), badge(paused ? "Paused" : r.state === "completed" ? "Completed" : r.status_label || r.state || "Scheduled", paused || r.state === "completed" ? "neutral" : failed ? "warning" : "good"));
      card.append(head, node("h2", "", r.name || r.label || r.id || "Job"));
      if (r.prompt_preview) card.append(node("p", "", r.prompt_preview));
      const nextAt = r.next_run_at || r.next_run, next = paused ? "Paused" : r.next_run_pending ? "Waiting for the scheduler" : nextAt ? formatRelative(nextAt) + " · " + date(nextAt) : "Not scheduled";
      const repeat = r.repeat && typeof r.repeat.times === "number" ? number(r.repeat.completed ?? 0) + " of " + number(r.repeat.times) : null;
      card.append(definition([["Schedule", r.schedule_label || describeSchedule(r.schedule)], ["Next run", next], ["Last run", r.last_run_at ? formatRelative(r.last_run_at) : "Never"], ["Last result", resultLabel(r.last_status) || (r.has_error ? "Failed" : "—")],
        ["Delivery", r.deliver_label || deliveryLabel(r.deliver)],
        ...(repeat ? [["Runs", repeat]] : []),
        ...(typeof r.failure_streak === "number" && r.failure_streak > 0 ? [["Failures in a row", number(r.failure_streak)]] : []),
        ...(Array.isArray(r.skills) && r.skills.length ? [["Skills", r.skills.join(", ")]] : []),
        ...(r.has_script ? [["Script", "Configured on the host · not editable here"]] : [])]));
      for (const [label, text] of [["Last error", r.last_error], ["Delivery error", r.last_delivery_error]]) if (typeof text === "string" && text) card.append(node("p", "notice error", label + ": " + text));
      if (r.latest_output && typeof r.latest_output === "object") card.append(latestOutput(r.latest_output, r.outputs));
      const runs = r.runs || r.recent_runs; if (Array.isArray(runs) && runs.length) card.append(runList(runs));
      card.append(actionRow(plan.row(r))); cards.append(card);
    }
    root.append(cards);
    if (data.truncated) root.append(note("Only the first automations are shown", "", "warning"));
  }
  function logs(data, root) {
    // Unapplied edits live in logDraft so a re-render (page search, refresh) keeps them; only Apply sends a request.
    const f = state.logDraft || state.logFilter, form = node("form", "log-filter"), apply = node("button", "button secondary", "Apply filters");
    const reset = button("Clear", "text-button", () => { state.logFilter = {}; state.logDraft = null; load(); });
    const track = (control, key) => { const save = () => { state.logDraft = { ...(state.logDraft || state.logFilter), [key]: control.value }; }; control.addEventListener("input", save); control.addEventListener("change", save); return control; };
    const select = (key, label, options) => { const n = node("select"); n.id = "log-" + key; n.setAttribute("aria-label", label); for (const [v, text] of options) { const o = node("option", "", text); o.value = v; n.append(o); } n.value = options.some(([v]) => v === (f[key] || "")) ? f[key] || "" : ""; return track(n, key); };
    const query = node("input"); query.id = "log-q"; query.maxLength = 100; query.value = f.q || ""; query.placeholder = "Search redacted log text"; query.setAttribute("aria-label", "Search log text"); track(query, "q");
    const controls = {
      q: query,
      level: select("level", "Log level", [["", "All levels"], ...LOG_OPTIONS.level.map(v => [v, humanize(v.toLowerCase())])]),
      file: select("file", "Log file", [["", "All files"], ...LOG_OPTIONS.file.map(v => [v, humanize(v) + " log"])]),
      component: select("component", "Component", [["", "All components"], ...LOG_OPTIONS.component.map(v => [v, v === "cli" ? "CLI" : humanize(v)])]),
      lines: select("lines", "Lines", [["", "Default lines"], ...LOG_OPTIONS.lines.map(v => [v, v + " lines"])])
    };
    apply.type = "submit";
    form.append(...Object.values(controls), apply, reset);
    form.addEventListener("submit", event => {
      event.preventDefault();
      const next = {}, q = String(controls.q.value || "").trim().slice(0, 100);
      if (q) next.q = q;
      for (const key of ["level", "file", "component", "lines"]) if (LOG_OPTIONS[key].includes(controls[key].value)) next[key] = controls[key].value;
      state.logFilter = next; state.logDraft = null; load();
    });
    root.append(form); sectionMessage(data, root);
    const items = (data.items || []).filter(matches), plan = actionPlan(data, items);
    if (plan.global.length) controlToolbar("", plan, root);
    const box = panel("Log entries", data.truncated ? "Newest entries · older lines omitted" : "Newest entries first");
    box.append(items.length ? table(["Level", "File", "Entry"], items.map(i => [badge(i.level || "—", ["ERROR", "CRITICAL"].includes(i.level) ? "error" : i.level === "WARNING" ? "warning" : "neutral"), node("span", "mono", i.file || "—"), node("span", "mono log-line", i.line || "")])) : empty("No matching log entries", data.message || "Try a different filter.", "list"));
    root.append(box);
  }

  const renderers = { overview, users, sessions, memories, skills, routines, usage, runtime, access, chat, files, models, keys, channels, config, mcp, logs };
  for (const name of ["plugins", "webhooks", "documentation"]) renderers[name] = metadata;
  renderers["system-overview"] = overview;
  function render() {
    if (!state.data || !state.user) return;
    const draft = $("chat-message"); if (draft) state.chatDraft = draft.value;
    const root = document.createDocumentFragment(); if (state.data.available === false && state.data.message) root.append(noteFrom(state.data.message, "warning")); (renderers[state.view] || metadata)(state.data, root);
    // ui-visual's staggered entrance runs only for the first render after a view change, never for polls or filtering.
    $("content").classList.remove("page-enter");
    $("content").replaceChildren(root);
    if (state.enterPending) { state.enterPending = false; $("content").classList.add("page-enter"); }
  }
  // The briefing is shown as "Home" everywhere (nav, heading, palette).
  function heading(view) {
    if (view !== HOME_PAGE) return views[view];
    const text = views[view][1];
    return ["Home", text ? "Your briefing · " + text : "Your briefing"];
  }
  // ---- Extension API ------------------------------------------------------
  // Feature files (console-*.js) call HermesConsole.register(view, spec):
  //   spec = { title, description, icon, group: "main"|"agent"|"observe"|"integrations"|"people"|"advanced",
  //            essential?: true,  // show in the sidebar's Essentials set instead of under Advanced
  //            render(data, root, kit), query?() -> {key: value}, actions?: true, searchable?: false,
  //            commands?: [{ label, keywords?, icon?, run(kit) }],  // quick actions in the Ctrl/Cmd+K palette
  //            allowed?({ can, operator }) -> boolean }
  // Groups other than "main" render inside the collapsible Advanced area.
  // GET /api/assistant/<view> supplies `data`; descriptor actions post to /api/assistant/<view>/action.
  // Everything from the server is untrusted: build DOM with kit helpers (textContent only).
  function refreshNav() {
    if (!state.user) return;
    // "System overview" duplicates Home until the briefing page takes over Home.
    document.querySelectorAll("[data-view]").forEach(n => { n.hidden = !allowed(n.dataset.view) || (n.dataset.view === "system-overview" && !homeActive()); });
    document.querySelectorAll(".nav-group").forEach(group => { group.hidden = ![...group.querySelectorAll("[data-view]")].some(n => !n.hidden); });
  }
  // Puts every nav button in Essentials or its own Advanced subgroup, in a stable order:
  // Essentials follow ESSENTIALS then the viewer's pins; subgroups keep their original order.
  let navOrder = 0;
  function navHome(button) { if (!button.dataset.navHome) { const group = button.closest?.("[data-nav-group]")?.dataset.navGroup || "agent", view = button.dataset.view; button.dataset.navHome = group === "main" ? FALLBACK_GROUP[view] || "agent" : group; button.dataset.navOrder = String(++navOrder); } return button.dataset.navHome; }
  function layoutNav() {
    const buttons = [...document.querySelectorAll("#navigation [data-view]")], groups = new Map();
    for (const b of buttons) { const home = navHome(b), target = b.id === "nav-home" || essential(b.dataset.view) ? "main" : home; if (!groups.has(target)) groups.set(target, []); groups.get(target).push(b); }
    const rank = b => { if (b.id === "nav-home") return -1; const v = b.dataset.view, i = ESSENTIALS.indexOf(v), p = pins.add.indexOf(v); return i >= 0 ? i : p >= 0 ? ESSENTIALS.length + 1 + p : ESSENTIALS.length; };
    for (const [group, list] of groups) {
      const container = document.querySelector?.('[data-nav-group="' + group + '"]'); if (!container) continue;
      list.sort((a, b) => group === "main" ? rank(a) - rank(b) || Number(a.dataset.navOrder) - Number(b.dataset.navOrder) : Number(a.dataset.navOrder) - Number(b.dataset.navOrder));
      for (const b of list) container.append(b);
    }
  }
  function barChart(points, label = "Chart") {
    if (!points.length) return empty("No data yet", "The chart appears once data is recorded.", "chart");
    const svg = document.createElementNS(SVG, "svg"), values = points.map(p => Math.max(0, Number(p.value) || 0)), max = Math.max(1, ...values), step = 540 / points.length;
    svg.setAttribute("viewBox", "0 0 560 140"); svg.setAttribute("class", "chart"); svg.setAttribute("role", "img"); svg.setAttribute("aria-label", label);
    for (let y = 12; y <= 132; y += 40) { const line = document.createElementNS(SVG, "line"); for (const [k, v] of Object.entries({ x1: 0, x2: 560, y1: y, y2: y, class: "grid" })) line.setAttribute(k, String(v)); svg.append(line); }
    points.forEach((p, i) => { const bar = document.createElementNS(SVG, "rect"), height = values[i] / max * 105, title = document.createElementNS(SVG, "title"); for (const [k, v] of Object.entries({ x: 10 + i * step, y: 132 - height, width: Math.min(34, step * .6), height, rx: 4, class: p.highlight ? "bar latest" : "bar" })) bar.setAttribute(k, String(v)); title.textContent = String(p.title ?? (p.label + ": " + values[i])); bar.append(title); svg.append(bar); });
    const wrap = node("div"), labels = node("div", "chart-labels"); labels.append(node("span", "", points[0].label), node("span", "", points[points.length - 1].label)); wrap.append(svg, labels); return wrap;
  }
  function poll(ms) { clearTimeout(state.pollTimer); const view = state.view, user = state.user; state.pollTimer = setTimeout(() => { if (state.view === view && state.user === user && !state.actionBusy && !document.querySelector?.("dialog[open]")) load({ quiet: true }); }, Math.max(2000, Number(ms) || 5000)); }
  // Opens Chat with a prefilled (not sent) message; the person reviews it and presses Send.
  function openChat(message) {
    if (!allowed("chat")) return;
    state.chatDraft = Array.from(String(message ?? "")).slice(0, 4000).join("");
    if (state.view === "chat" && state.data) { const input = $("chat-message"); if (input) input.value = state.chatDraft; render(); $("chat-message")?.focus(); }
    else navigate("chat");
  }
  // Navigate to a page and open its single matching untargeted descriptor action once it has loaded.
  function openWhenReady(view, pattern) {
    navigate(view);
    if (state.view === resolveView(view)) state.pendingAction = { view: state.view, pattern };
  }
  function runPendingAction() {
    const pending = state.pendingAction;
    if (!pending || pending.view !== state.view || !state.data) return;
    state.pendingAction = null;
    const matching = actionPlan(state.data, state.data.items || []).global.filter(a => pending.pattern.test(a.id));
    if (matching.length === 1) openAction(matching[0]);
  }

  // ---- Command palette (Ctrl/Cmd+K) ----------------------------------------
  function paletteEntries() {
    const entries = [], add = (section, label, name, run, hint = "", keywords = "") => entries.push({ section, label, icon: name, run, hint, keywords });
    if (allowed("chat")) add("Quick actions", "Open chat", "chat", () => navigate("chat"), "", "talk ask message assistant");
    if (allowed("reports")) add("Quick actions", "New report", "file", () => typeof window.HermesReports?.create === "function" ? window.HermesReports.create() : openWhenReady("reports", /^(create|new)/), "", "report template schedule summary");
    if (allowed("routines")) add("Quick actions", "New automation", "calendar", () => openWhenReady("routines", /^(create|new)/), "", "schedule job cron routine");
    if (allowed("connectors")) add("Quick actions", "Connect an app", "plug", () => navigate("connectors"), "", "connector integration facebook zalo telegram notion");
    if (allowed("memories")) add("Quick actions", "Add memory", "plus", () => { navigate("memories"); editMemory(); }, "", "remember note");
    for (const [view, spec] of extensions) {
      if (!allowed(view)) continue;
      let list = [];
      try { list = typeof spec.commands === "function" ? spec.commands(kit) : spec.commands; } catch { list = []; }
      if (!Array.isArray(list)) continue;
      for (const c of list.slice(0, 8)) if (c && typeof c.label === "string" && c.label && typeof c.run === "function") add("Quick actions", c.label.slice(0, 80), typeof c.icon === "string" ? c.icon : spec.icon || "zap", () => c.run(kit), views[view][0], typeof c.keywords === "string" ? c.keywords : "");
    }
    if (state.data && actionSection(state.view) && state.controlRefresh !== state.view) for (const a of actionPlan(state.data, state.data.items || []).global.slice(0, 8)) add("On this page", a.label, "zap", () => openAction(a), views[state.view][0]);
    const pages = Object.keys(views).filter(view => allowed(view) && view !== "runtime" && !(view === "overview" && homeActive()) && !(view === "system-overview" && !homeActive()));
    const rank = view => essential(view) ? (ESSENTIALS.includes(view) ? ESSENTIALS.indexOf(view) : ESSENTIALS.length) : ESSENTIALS.length + 1;
    pages.sort((a, b) => rank(a) - rank(b));
    for (const view of pages) {
      const home = view === HOME_PAGE || view === "overview", ext = extensions.get(view);
      add(essential(view) ? "Pages" : "Advanced", home ? "Home" : views[view][0], ext?.icon || PAGE_ICONS[view] || "box", () => navigate(view), home && view === HOME_PAGE ? "Your briefing" : "", [view, views[view][1], PAGE_KEYWORDS[view] || "", home ? "briefing dashboard" : ""].join(" "));
    }
    const current = state.view;
    if (views[current] && !isHome(current) && allowed(current)) {
      const title = views[current][0];
      if (essential(current)) add("Sidebar", "Remove " + title + " from the sidebar", "minus", () => { setPinned(current, false); toast(title + " moved to Advanced.", "info"); }, "", "unpin hide essentials customize");
      else add("Sidebar", "Pin " + title + " to the sidebar", "pin", () => { setPinned(current, true); toast(title + " pinned to the sidebar."); }, "", "add essentials favourite favorite customize");
    }
    add("Sidebar", "Customize sidebar…", "pin", openCustomize, "", "pin unpin essentials favourite favorite reorder");
    if (customized()) add("Sidebar", "Reset sidebar to default", "refresh", () => { resetPins(); toast("Sidebar reset to the default pages.", "info"); }, "", "customize default");
    const dark = currentTheme() === "dark";
    add("Preferences", dark ? "Switch to light theme" : "Switch to dark theme", dark ? "sun" : "moon", () => applyTheme(dark ? "light" : "dark", true), "", "theme colour color mode");
    add("Preferences", "Change password", "lock", openPassword, "", "account security");
    return entries;
  }
  function paletteScore(entry, query) {
    if (!query) return 1;
    const label = entry.label.toLocaleLowerCase("en"), hay = (label + " " + entry.hint + " " + entry.section + " " + entry.keywords).toLocaleLowerCase("en");
    if (label.startsWith(query)) return 4;
    if (label.split(/[^\p{L}\p{N}]+/u).some(word => word.startsWith(query))) return 3;
    if (label.includes(query)) return 2;
    return query.split(/\s+/).every(term => hay.includes(term)) ? 1 : 0;
  }
  function renderPalette() {
    const list = $("palette-list"), input = $("palette-input"), raw = String(input.value || "").trim(), query = raw.toLocaleLowerCase("en"), p = state.palette;
    let items = p.entries.map((entry, order) => ({ entry, order, score: paletteScore(entry, query) })).filter(x => x.score > 0);
    if (query) items.sort((a, b) => b.score - a.score || a.order - b.order);
    items = items.slice(0, 40).map(x => x.entry);
    if (raw && allowed("chat")) items.push({ section: "Ask", label: "Ask Hermes: \u201c" + raw + "\u201d", icon: "send", run: () => openChat(raw), hint: "Chat" });
    p.items = items; p.index = Math.max(0, Math.min(p.index, items.length - 1));
    list.replaceChildren();
    let section = "";
    items.forEach((item, index) => {
      if (!query && item.section !== section) { section = item.section; const head = node("li", "palette-section", section); head.setAttribute("role", "presentation"); list.append(head); }
      const option = node("li", "palette-item" + (index === p.index ? " active" : "")), mark = node("span", "palette-icon");
      option.id = "palette-opt-" + index; option.setAttribute("role", "option"); option.setAttribute("aria-selected", String(index === p.index));
      mark.append(icon(item.icon || "arrow"));
      option.append(mark, node("span", "palette-label", item.label));
      const hint = query ? item.hint || item.section : item.hint;
      if (hint) option.append(node("span", "palette-hint", hint));
      option.addEventListener("click", () => runPaletteItem(index));
      option.addEventListener("mousemove", () => { if (p.index !== index) movePalette(index - p.index); });
      list.append(option);
    });
    $("palette-empty").hidden = items.length > 0;
    if (items.length) input.setAttribute("aria-activedescendant", "palette-opt-" + p.index); else input.removeAttribute("aria-activedescendant");
  }
  function movePalette(delta) {
    const p = state.palette, count = p.items.length;
    if (!count) return;
    p.index = ((p.index + delta) % count + count) % count;
    for (const option of $("palette-list").children || []) {
      if (option.getAttribute?.("role") !== "option" && option.attributes?.role !== "option") continue;
      const active = option.id === "palette-opt-" + p.index;
      option.classList.toggle("active", active); option.setAttribute("aria-selected", String(active));
      if (active) option.scrollIntoView?.({ block: "nearest" });
    }
    $("palette-input").setAttribute("aria-activedescendant", "palette-opt-" + p.index);
  }
  function openPalette() {
    if (!state.user || $("palette").open) return;
    closeMenu();
    state.palette = { entries: paletteEntries(), items: [], index: 0, returnFocus: document.activeElement || null, restore: true };
    $("palette-input").value = "";
    renderPalette();
    $("palette").showModal();
    $("palette-input").focus();
  }
  function closePalette(restore = true) { state.palette.restore = restore; if ($("palette").open) $("palette").close(); }
  function runPaletteItem(index) {
    const item = state.palette.items[index];
    if (!item || !state.user) return;
    closePalette(false);
    item.run();
    // Move focus to the page (or the dialog the command opened) instead of back to the old control.
    if (!document.querySelectorAll("dialog[open]").length) $("main").focus?.();
  }
  function paletteKey(event) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); movePalette(event.key === "ArrowDown" ? 1 : -1); }
    else if (event.key === "PageDown" || event.key === "PageUp") { event.preventDefault(); movePalette(event.key === "PageDown" ? 5 : -5); }
    else if (event.key === "Enter" && !event.isComposing) { event.preventDefault(); runPaletteItem(state.palette.index); }
    else if (event.key === "Tab") { event.preventDefault(); movePalette(event.shiftKey ? -1 : 1); }
  }
  // ---- Customize sidebar dialog -------------------------------------------
  function customizablePages() {
    return Object.keys(views).filter(view => allowed(view) && !isHome(view) && view !== "runtime" && !(view === "system-overview" && !homeActive()));
  }
  function renderCustomize() {
    const list = $("customize-list"), pages = customizablePages();
    const order = view => essential(view) ? 0 : 1;
    pages.sort((a, b) => order(a) - order(b));
    list.replaceChildren();
    for (const view of pages) {
      const label = node("label", "check-field customize-item"), box = node("input"), mark = node("span", "customize-icon"), text = node("span", "customize-text");
      box.type = "checkbox"; box.checked = essential(view); box.dataset.page = view;
      mark.append(icon(extensions.get(view)?.icon || PAGE_ICONS[view] || "box"));
      text.append(node("strong", "", views[view][0]), node("small", "", defaultEssential(view) ? "Essential by default" : "Advanced by default"));
      box.addEventListener("change", () => setPinned(view, box.checked));
      label.append(box, mark, text); list.append(label);
    }
    $("customize-reset").disabled = !customized();
  }
  function openCustomize() {
    if (!state.user) return;
    closeMenu(); renderCustomize(); $("customize-dialog").showModal();
    $("customize-list").querySelector?.("input")?.focus();
  }
  function openPassword() { $("password-form").reset(); $("password-error").hidden = true; $("password-dialog").showModal(); $("current-password").focus(); }
  const typing = target => Boolean(target && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(String(target.tagName || ""))));

  const kit = Object.freeze({ node, button, iconButton, icon, iconBox, badge, empty, panel, metric, table, description, definition, chart, barChart,
    pagination, toast, confirmation, notice, note, noteFrom, number, money, date, bytes, humanize, displayValue, matches, yesNo,
    actionPlan, actionRow, actionButton, openAction, controlToolbar, sectionMessage, api, navigate, reload: load, render, poll,
    openChat, openPalette, describeSchedule, formatRelative, isDemo: () => state.mode === "demo", view: () => state.view, can });
  function register(view, spec) {
    if (!/^[a-z][a-z0-9-]{1,31}$/.test(view) || (views[view] && !extensions.has(view)) || !spec || typeof spec.render !== "function" || typeof spec.title !== "string") throw new Error("Invalid console extension: " + view);
    extensions.set(view, spec);
    views[view] = [spec.title, typeof spec.description === "string" ? spec.description : ""];
    renderers[view] = (data, root) => spec.render(data, root, kit);
    if (spec.searchable === false) UNSEARCHABLE.add(view);
    if (view === HOME_PAGE) {
      // The briefing becomes Home: reuse the Home button and keep the old overview under Advanced.
      $("nav-home").dataset.view = HOME_PAGE;
      views.overview = ["System overview", views["system-overview"][1]];
    } else {
      const group = NAV_GROUPS.has(spec.group) && spec.group !== "main" ? spec.group : FALLBACK_GROUP[view] || "agent";
      const container = document.querySelector?.('[data-nav-group="' + group + '"]');
      if (container && !document.querySelector('#navigation [data-view="' + view + '"]')) { const b = node("button"); b.type = "button"; b.dataset.view = view; b.dataset.navHome = group; b.dataset.navOrder = String(++navOrder); b.append(icon(spec.icon || "box"), document.createTextNode(spec.title)); b.hidden = true; container.append(b); }
    }
    layoutNav(); refreshNav();
    if (state.user && state.requestedView === view && state.view !== view) navigate(view);
  }
  window.HermesConsole = Object.freeze({ register, kit });

  $("login-form").addEventListener("submit", event => { event.preventDefault(); $("login-error").hidden = true; busy(event.currentTarget, async () => { try { await api("/api/session"); const session = await api("/api/login", { username: $("username").value.trim(), password: $("password").value }); $("password").value = ""; showApp(session.user ? session : await api("/api/session")); } catch (e) { $("password").value = ""; notice($("login-error"), e); } }); });
  $("logout").addEventListener("click", async () => { $("logout").disabled = true; try { await api("/api/logout", {}); showLogin(); } catch (e) { notice($("page-error"), e); } finally { $("logout").disabled = false; } });
  $("navigation").addEventListener("click", event => { const target = event.target.closest("[data-view]"); if (target) navigate(target.dataset.view); });
  $("refresh").addEventListener("click", load); $("search").addEventListener("input", render);
  $("menu-toggle").addEventListener("click", () => { const open = $("sidebar").classList.toggle("open"); $("scrim").hidden = !open; $("menu-toggle").setAttribute("aria-expanded", String(open)); if (open) ($("navigation").querySelector?.('[aria-current="page"]:not([hidden])') || $("navigation").querySelector?.("[data-view]:not([hidden])"))?.focus(); });
  $("scrim").addEventListener("click", closeMenu);
  $("advanced-toggle").addEventListener("click", () => setAdvanced($("advanced-nav").hidden, true));
  setAdvanced(storedAdvanced(), false);
  layoutNav();
  $("customize-open").addEventListener("click", openCustomize);
  $("customize-reset").addEventListener("click", () => { resetPins(); renderCustomize(); toast("Sidebar reset to the default pages.", "info"); });
  $("palette-open").addEventListener("click", openPalette);
  $("palette-input").addEventListener("input", () => { state.palette.index = 0; renderPalette(); });
  $("palette-input").addEventListener("keydown", paletteKey);
  $("palette").addEventListener("close", () => { const back = state.palette.returnFocus; if (state.palette.restore && back && back.isConnected !== false) back.focus?.(); state.palette.entries = []; state.palette.items = []; state.palette.returnFocus = null; $("palette-list").replaceChildren(); });
  $("palette").addEventListener("click", event => { if (event.target === $("palette")) closePalette(); });
  $("theme-toggle").addEventListener("click", () => applyTheme(currentTheme() === "dark" ? "light" : "dark", true));
  applyTheme(storedTheme(), false);
  document.querySelectorAll("[data-close]").forEach(b => b.addEventListener("click", () => $(b.dataset.close).close()));
  $("memory-dialog").addEventListener("close", () => { state.editing = null; $("memory-form").reset(); });
  $("memory-form").addEventListener("submit", event => { event.preventDefault(); $("memory-error").hidden = true; const user = state.user; busy(event.currentTarget, async () => { try { const old = state.editing, payload = { content: $("memory-content").value.trim(), member_id: $("memory-member").value.trim() || null, scope: old?.scope || "personal", active: old ? Boolean(old.active) : true }; if (old) { payload.id = old.id; payload.expected_revision = old.revision; if (old.session_key) payload.session_key = old.session_key; if (old.group_id) payload.group_id = old.group_id; if (old.source_message_id) payload.source_message_id = old.source_message_id; if (old.expires) payload.expires = old.expires; if (payload.scope === "group" || (payload.scope === "session" && String(payload.session_key).includes(":GROUP:"))) { if (!await confirmation("Update shared memory?", "This change may affect other people in the shared group or session.")) return; payload.confirm_share = true; } } if (user !== state.user || !allowed("memories")) return; await api("/api/memory/save", payload); if (user !== state.user) return; $("memory-dialog").close(); toast("Memory saved in the managed store."); await load(); } catch (e) { if (user === state.user) notice($("memory-error"), e); } }); });

  $("action-form").addEventListener("submit", submitAction);
  $("action-dialog").addEventListener("close", () => { if (state.actionBusy) clearSecrets(); else { state.control = null; $("action-fields").replaceChildren(); } $("action-error").hidden = true; });
  $("password-open").addEventListener("click", openPassword);
  $("password-dialog").addEventListener("close", () => $("password-form").reset());
  $("password-form").addEventListener("submit", event => { event.preventDefault(); $("password-error").hidden = true; busy(event.currentTarget, async () => { try { const payload = { current_password: $("current-password").value, new_password: $("new-password").value, confirm_password: $("confirm-password").value }; if (payload.new_password !== payload.confirm_password) throw new Error("The new passwords do not match."); await api("/api/password/change", payload); $("password-dialog").close(); showLogin(); toast("Password changed. Sign in again."); } catch (e) { $("password-form").reset(); notice($("password-error"), e); } }); });
  document.addEventListener("keydown", event => {
    if (!state.user) return;
    const key = String(event.key || "").toLowerCase(), dialogOpen = document.querySelectorAll("dialog[open]").length > 0;
    if ((event.ctrlKey || event.metaKey) && !event.altKey && key === "k") { event.preventDefault(); if ($("palette").open) closePalette(); else if (!dialogOpen) openPalette(); return; }
    // "/" focuses the page filter, as in most dashboards, unless the person is typing.
    if (key === "/" && !event.ctrlKey && !event.metaKey && !event.altKey && !dialogOpen && !typing(event.target) && !$("search-box").hidden) { event.preventDefault(); $("search").focus(); return; }
    if (key === "escape" && $("sidebar").classList.contains("open")) { closeMenu(); $("menu-toggle").focus(); }
  });
  $("search-shortcut").textContent = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform || "") ? "⌘ K" : "Ctrl K";
  window.addEventListener("hashchange", () => { const view = resolveView(location.hash.slice(1)); if (views[view] && allowed(view) && state.view !== view) navigate(view); });
  api("/api/session").then(session => { state.mode = session.mode || "local"; $("demo-banner").hidden = state.mode !== "demo"; if (session.user) showApp(session); else showLogin(); }).catch(e => { showLogin(); notice($("login-error"), e); });
})();
