"use strict";
// CEO Briefing: the console landing page. Data shape: src/hermes_zalo/admin_briefing.py.
// Everything from the server is untrusted: DOM via kit.node/textContent only, sparklines via createElementNS.
(() => {
  const C = window.HermesConsole;
  if (!C) return;
  const SVG = "http://www.w3.org/2000/svg";
  const LINK = /^#([a-z][a-z0-9-]{1,31})$/;
  const SEVERITY = { critical: ["Urgent", "error"], warning: ["Review", "warning"], info: ["New", "info"] };
  const KIND_ICON = { automation: "calendar", connector: "plug", quota: "zap", gateway: "server", scheduler: "calendar", channel: "send", report: "file" };
  // Platform marks: two-letter monogram, or an icon for internal sources.
  const PLATFORM = { facebook: ["Fb"], zalo: ["Za"], telegram: ["Tg"], x: ["X"], instagram: ["Ig"], tiktok: ["Tt"], youtube: ["Yt"],
    work: [null, "check"], assistant: [null, "bot"], automations: [null, "calendar"], reports: [null, "file"], channels: [null, "send"] };
  const TONE = { bad: ["Needs you", "error"], warning: ["Worth a look", "warning"], good: ["All clear", "good"] };
  const compactFormat = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });
  const known = v => typeof v === "number" && Number.isFinite(v);
  const compact = v => known(v) ? (Math.abs(v) < 10000 ? new Intl.NumberFormat("en-US").format(v) : compactFormat.format(v)) : "—";
  const list = v => Array.isArray(v) ? v : [];
  const block = v => v && typeof v === "object" ? v : { available: false, items: [] };

  // ---- helpers ------------------------------------------------------------
  function when(value) {
    const d = new Date(value || "");
    return Number.isNaN(d.valueOf()) ? null : d;
  }
  function relative(value, now = Date.now()) {
    const d = when(value); if (!d) return "—";
    const delta = d.valueOf() - now, minutes = Math.round(delta / 60000), abs = Math.abs(minutes);
    if (abs < 1) return delta > 0 ? "in under a minute" : "just now";
    const text = abs < 60 ? abs + " min" : abs < 36 * 60 ? Math.round(abs / 60) + " h" : Math.round(abs / 1440) + " d";
    return minutes > 0 ? "in " + text : text + " ago";
  }
  function clock(value) {
    const d = when(value); if (!d) return "—";
    const today = new Date(), tomorrow = new Date(today.valueOf() + 86400000);
    const time = d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
    if (d.toDateString() === today.toDateString()) return "Today " + time;
    if (d.toDateString() === tomorrow.toDateString()) return "Tomorrow " + time;
    return d.toLocaleDateString("en-US", { weekday: "short", day: "numeric", month: "short" }) + " " + time;
  }
  function greeting(date = new Date()) {
    const h = date.getHours();
    return h < 5 ? "Good evening" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
  }
  function ownerName() {
    const n = document.getElementById?.("user-name"), text = String(n?.textContent || "").trim();
    return text && text !== "Administrator" ? text.split(/\s+/)[0].slice(0, 40) : "";
  }
  // Only console hashes are followed; anything else from the server is ignored.
  function linkView(link) { const m = LINK.exec(String(link || "")); return m ? m[1] : null; }
  // A report reference opens that exact report when the Reports page offers HermesReports.open (console-integrations.js);
  // otherwise the link's page opens. Job ids and file names are checked against the same shapes the server uses.
  const JOB = /^[A-Za-z0-9_-]{1,64}$/, FILE = /^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.md$/;
  function reportRef(ref) { return ref && JOB.test(String(ref.job)) && FILE.test(String(ref.file)) ? { job: String(ref.job), file: String(ref.file) } : null; }
  function go(kit, link, report) {
    const view = linkView(link), ref = view === "reports" ? reportRef(report) : null, reader = window.HermesReports;
    if (ref && reader && typeof reader.open === "function") { try { reader.open(ref.job, ref.file); return; } catch { /* fall back to the page */ } }
    if (view) kit.navigate(view);
  }

  // Open Chat with the message prefilled (not sent): the owner reviews it and presses Send.
  function openChat(kit, text) {
    if (typeof kit.openChat === "function") kit.openChat(text);
    else kit.navigate("chat");
    if (kit.view() !== "chat") kit.toast("Chat is not available for this account.");
  }

  function sparkline(series, trend, label) {
    const values = list(series).filter(known).slice(-30);
    const svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("class", "brf-spark " + trend);
    svg.setAttribute("viewBox", "0 0 100 32");
    svg.setAttribute("preserveAspectRatio", "none");
    svg.setAttribute("aria-hidden", "true");
    if (values.length < 2) { svg.setAttribute("class", "brf-spark empty"); return svg; }
    const min = Math.min(...values), max = Math.max(...values), span = max - min || 1;
    const pts = values.map((v, i) => [i / (values.length - 1) * 100, 29 - (v - min) / span * 26]);
    const line = pts.map(([x, y], i) => (i ? "L" : "M") + x.toFixed(2) + " " + y.toFixed(2)).join(" ");
    const area = document.createElementNS(SVG, "path"), stroke = document.createElementNS(SVG, "path"), dot = document.createElementNS(SVG, "circle");
    area.setAttribute("d", line + " L100 32 L0 32 Z"); area.setAttribute("class", "brf-spark-area");
    stroke.setAttribute("d", line); stroke.setAttribute("class", "brf-spark-line"); stroke.setAttribute("vector-effect", "non-scaling-stroke");
    const [lx, ly] = pts[pts.length - 1];
    dot.setAttribute("cx", lx.toFixed(2)); dot.setAttribute("cy", ly.toFixed(2)); dot.setAttribute("r", "1.6"); dot.setAttribute("class", "brf-spark-dot");
    const title = document.createElementNS(SVG, "title"); title.textContent = label;
    svg.append(title, area, stroke, dot);
    return svg;
  }

  function changeChip(kit, tile) {
    if (!known(tile.change)) return kit.node("span", "brf-change flat", tile.change_label || "");
    const pct = Math.abs(tile.change * 100), up = tile.change > 0, flat = pct < 0.05;
    const good = tile.good_when === "down" ? !up : tile.good_when === "neutral" ? null : up;
    const chip = kit.node("span", "brf-change " + (flat || good === null ? "flat" : good ? "good" : "bad"));
    chip.append(kit.node("strong", "", (flat ? "±" : up ? "▲ " : "▼ ") + (pct >= 100 ? Math.round(pct) : pct.toFixed(1)) + "%"));
    if (tile.change_label) chip.append(kit.node("span", "", " " + tile.change_label));
    return chip;
  }

  function trendClass(tile) {
    if (!known(tile.change) || tile.good_when === "neutral") return "neutral";
    const up = tile.change >= 0;
    return (tile.good_when === "down" ? !up : up) ? "good" : "bad";
  }

  function platformMark(kit, platform) {
    const [mono, iconName] = PLATFORM[platform] || [String(platform || "?").slice(0, 2)];
    const mark = kit.node("span", "brf-mark brf-mark-" + (PLATFORM[platform] ? platform : "custom"));
    if (iconName) mark.append(kit.icon(iconName)); else mark.textContent = mono;
    return mark;
  }

  function section(kit, title, subtitle, cls, action) {
    const box = kit.panel(title, subtitle, action);
    box.className = "panel brf-panel " + cls;
    return box;
  }
  function unavailable(kit, data, title, iconName) {
    return kit.empty(title, data.message || "This information is not available right now.", iconName);
  }

  // ---- blocks -----------------------------------------------------------------
  function hero(data, kit) {
    const tone = TONE[data.tone] || TONE.good, box = kit.node("section", "brf-hero " + (TONE[data.tone] ? data.tone : "good"));
    const text = kit.node("div", "brf-hero-text"), name = ownerName();
    text.append(
      kit.node("p", "brf-date", new Date().toLocaleDateString("en-US", { weekday: "long", day: "numeric", month: "long", year: "numeric" })),
      kit.node("h2", "brf-greeting", greeting() + (name ? ", " + name : "")),
      kit.node("p", "brf-summary", data.summary || "Your briefing is ready."));
    const status = kit.node("div", "brf-hero-stats"), attention = block(data.attention), next = list(block(data.up_next).items)[0];
    const stat = (value, label, cls = "") => { const s = kit.node("div", "brf-stat " + cls); s.append(kit.node("strong", "", value), kit.node("span", "", label)); return s; };
    status.append(
      stat(String(list(attention.items).length), "need attention", list(attention.items).some(i => i.severity === "critical") ? "bad" : ""),
      stat(known(data.connected) ? String(data.connected) : "—", "connectors configured"),
      stat(next ? relative(next.at) : "—", next ? "next: " + (next.name || "automation") : "nothing scheduled"));
    const pill = kit.badge(tone[0], tone[1]); pill.className += " brf-tone";
    text.prepend(pill);
    box.append(text, status);
    return box;
  }

  function attentionPanel(data, kit) {
    const a = block(data.attention), items = list(a.items);
    const restore = list(data.actions).find(x => x && x.id === "restore"), all = list(data.actions).find(x => x && x.id === "dismiss_all");
    const tools = kit.node("div", "brf-panel-tools");
    if (all && items.length > 1) tools.append(kit.actionButton(all, "text-button"));
    if (restore) tools.append(kit.actionButton(restore, "text-button"));
    const box = section(kit, "Needs your attention", items.length ? items.length + " open · most urgent first" : "Nothing waiting for you",
      "brf-attention", tools.childElementCount ? tools : undefined);
    if (a.message) box.append(kit.node("p", "brf-inline-note", a.message));
    if (!items.length) {
      box.append(kit.empty("You're all caught up", a.dismissed ? a.dismissed + " dismissed item(s) are hidden." : "Failed automations, connector problems and new reports show up here.", "check"));
      return box;
    }
    const ul = kit.node("ul", "brf-queue");
    for (const item of items) {
      const [label, kind] = SEVERITY[item.severity] || SEVERITY.info;
      const li = kit.node("li", "brf-item " + (SEVERITY[item.severity] ? item.severity : "info"));
      const mark = kit.node("span", "brf-item-icon"); mark.append(kit.icon(KIND_ICON[item.kind] || "alert"));
      const body = kit.node("div", "brf-item-body"), head = kit.node("div", "brf-item-head");
      head.append(kit.badge(label, kind), kit.node("strong", "brf-item-title", item.title || "Item"));
      if (item.at) head.append(kit.node("time", "brf-item-time", relative(item.at)));
      body.append(head);
      if (item.detail) body.append(kit.node("p", "brf-item-detail", item.detail));
      const actions = kit.node("div", "brf-item-actions"), view = linkView(item.link);
      if (view) { const open = kit.button(item.link_label || "Open", "button secondary small", () => go(kit, item.link, item.report)); actions.append(open); }
      if (a.can_dismiss && data.revision) actions.append(kit.button("Dismiss", "text-button brf-dismiss", event => dismiss(kit, data, item, event)));
      body.append(actions);
      li.append(mark, body);
      ul.append(li);
    }
    box.append(ul);
    if (a.dismissed) box.append(kit.node("p", "brf-footnote", a.dismissed + " dismissed item" + (a.dismissed === 1 ? " is" : "s are") + " hidden."));
    return box;
  }

  // One click, no dialog: dismissing is low-risk and reversible ("Show dismissed items").
  let dismissing = false;
  async function dismiss(kit, data, item, event) {
    if (dismissing) return;
    const button = event?.currentTarget || event?.target;
    dismissing = true; if (button) button.disabled = true;
    try {
      const result = await kit.api("/api/assistant/briefing/action", { action: "dismiss", expected_revision: data.revision, confirm: true, values: { id: item.id } });
      kit.toast(result.message || "Dismissed.");
    } catch (error) {
      kit.toast(error?.message || "Could not dismiss this item. Refresh and try again.");
    } finally {
      dismissing = false;
      kit.reload({ quiet: true });
    }
  }

  function kpiStrip(data, kit) {
    const k = block(data.kpis), tiles = list(k.items);
    const wrap = kit.node("section", "brf-kpis");
    wrap.setAttribute("aria-label", "Key numbers" + (k.period ? " · " + k.period : ""));
    if (k.available === false || !tiles.length) {
      const box = section(kit, "Key numbers", k.period || "", "brf-kpi-empty");
      box.append(unavailable(kit, k, "No numbers yet", "chart"));
      return box;
    }
    for (const tile of tiles) {
      const view = linkView(tile.link), card = kit.node(view ? "button" : "div", "brf-kpi");
      if (view) { card.type = "button"; card.addEventListener("click", () => kit.navigate(view)); }
      const head = kit.node("span", "brf-kpi-head");
      head.append(platformMark(kit, tile.platform), kit.node("span", "brf-kpi-label", tile.label || "Metric"));
      const value = kit.node("span", "brf-kpi-value");
      value.append(kit.node("strong", "", compact(tile.value)));
      if (tile.unit) value.append(kit.node("small", "", " " + tile.unit));
      card.setAttribute("aria-label", (tile.label || "Metric") + ": " + compact(tile.value) + (tile.unit ? " " + tile.unit : "") + (known(tile.change) ? ", " + (tile.change >= 0 ? "up " : "down ") + Math.abs(tile.change * 100).toFixed(1) + "%" : ""));
      card.append(head, value, changeChip(kit, tile), sparkline(tile.series, trendClass(tile), (tile.label || "Metric") + " trend"));
      wrap.append(card);
    }
    if (k.message) wrap.append(kit.node("p", "brf-inline-note brf-kpi-note", k.message));
    return wrap;
  }

  function reportsPanel(data, kit) {
    const r = block(data.reports), items = list(r.items);
    const more = kit.button("All reports →", "text-button", () => kit.navigate("reports"));
    const box = section(kit, "Latest reports", known(r.total) && r.total > items.length ? items.length + " of " + r.total : "Newest first", "brf-reports", more);
    if (r.available === false) { box.append(unavailable(kit, r, "Reports unavailable", "file")); return box; }
    if (!items.length) { box.append(kit.empty("No reports yet", "Create a report from a template and its runs appear here.", "file")); return box; }
    const ul = kit.node("ul", "brf-report-list");
    for (const rep of items) {
      const li = kit.node("li"), view = linkView(rep.link), card = kit.node(view ? "button" : "div", "brf-report" + (rep.status === "failed" ? " failed" : ""));
      if (view) { card.type = "button"; card.addEventListener("click", () => go(kit, rep.link, rep.report)); }
      const head = kit.node("span", "brf-report-head"), mark = kit.node("span", "brf-report-icon");
      mark.append(kit.icon("file"));
      head.append(mark, kit.node("strong", "brf-report-title", rep.title || "Report"));
      if (rep.status === "failed") head.append(kit.badge("Last run failed", "error"));
      const meta = kit.node("span", "brf-report-meta", [rep.source, relative(rep.at)].filter(Boolean).join(" · "));
      card.append(head, meta);
      if (rep.excerpt) card.append(kit.node("span", "brf-report-excerpt", rep.excerpt));
      li.append(card); ul.append(li);
    }
    box.append(ul);
    return box;
  }

  function upNextPanel(data, kit) {
    const u = block(data.up_next), items = list(u.items);
    const box = section(kit, "Up next", known(u.total) && u.total ? u.total + " scheduled" : "Scheduled automations", "brf-next",
      kit.button("Automations →", "text-button", () => kit.navigate("routines")));
    if (u.available === false) { box.append(unavailable(kit, u, "Schedule unavailable", "calendar")); return box; }
    if (!items.length) { box.append(kit.empty("Nothing scheduled", "Automations and report jobs you schedule appear here.", "calendar")); return box; }
    const ol = kit.node("ol", "brf-timeline");
    for (const job of items) {
      const li = kit.node("li", "brf-slot " + (job.kind === "report" ? "report" : "automation"));
      const time = kit.node("time", "brf-slot-time"); time.append(kit.node("strong", "", clock(job.at)), kit.node("span", "", relative(job.at)));
      const body = kit.node("div", "brf-slot-body"), view = linkView(job.link);
      const name = view ? kit.button(job.name || "Automation", "text-button brf-slot-name", () => kit.navigate(view)) : kit.node("strong", "brf-slot-name", job.name || "Automation");
      body.append(name, kit.node("span", "brf-slot-meta", [job.kind === "report" ? "Report" : "Automation", job.schedule ? kit.describeSchedule(job.schedule) : ""].filter(Boolean).join(" · ")));
      li.append(time, body); ol.append(li);
    }
    box.append(ol);
    return box;
  }

  function promptsPanel(data, kit) {
    const prompts = list(data.prompts).filter(p => p && typeof p.prompt === "string" && p.prompt.trim());
    const box = section(kit, "Ask your assistant", "Opens Chat with the question ready to send", "brf-prompts");
    if (!prompts.length) { box.append(kit.empty("No suggestions", "Open Chat to ask anything.", "chat")); return box; }
    const grid = kit.node("div", "brf-prompt-grid");
    for (const p of prompts.slice(0, 6)) {
      const b = kit.node("button", "brf-prompt"); b.type = "button";
      b.title = p.prompt.slice(0, 400);
      const mark = kit.node("span", "brf-prompt-icon"); mark.append(kit.icon("sparkles"));
      b.append(mark, kit.node("span", "", p.label || p.prompt.slice(0, 60)));
      b.addEventListener("click", () => openChat(kit, p.prompt.slice(0, 4000)));
      grid.append(b);
    }
    box.append(grid);
    return box;
  }

  function render(data, root, kit) {
    if (data.message) root.append(typeof kit.note === "function" ? kit.note(data.message, "", data.source === "demo" ? "info" : "warning") : kit.node("p", "notice", data.message));
    root.append(hero(data, kit), kpiStrip(data, kit));
    const grid = kit.node("div", "brf-grid"), main = kit.node("div", "brf-col brf-col-main"), side = kit.node("div", "brf-col brf-col-side");
    main.append(attentionPanel(data, kit), reportsPanel(data, kit));
    side.append(upNextPanel(data, kit), promptsPanel(data, kit));
    grid.append(main, side);
    root.append(grid);
    kit.poll(60000);
    if (pendingDismissAll) {
      pendingDismissAll = false;
      const all = list(data.actions).find(x => x && x.id === "dismiss_all");
      if (all) setTimeout(() => { if (kit.view() === "briefing") kit.actionButton(all).click(); }, 0);
      else kit.toast("Nothing to dismiss right now.");
    }
  }

  // Ctrl/Cmd+K palette entries. Prompts open Chat prefilled (never sent).
  let pendingDismissAll = false;
  const PALETTE_PROMPTS = [
    ["Ask: summarize today's social activity", "Summarize today's activity across my connected social channels: reach, notable posts, comments that need a reply and anything unusual."],
    ["Ask: what needs my reply?", "List the messages, comments and mentions that are waiting for my reply, most urgent first."],
  ];
  function commands(kit) {
    const list = [{ label: "Dismiss all attention items", icon: "check", keywords: "briefing clear attention queue reviewed done inbox",
      run: k => { pendingDismissAll = true; if (k.view() === "briefing") k.reload(); else k.navigate("briefing"); } }];
    for (const [label, prompt] of PALETTE_PROMPTS) list.push({ label, icon: "sparkles", keywords: "chat ask assistant briefing", run: k => openChat(k, prompt) });
    return list;
  }

  C.register("briefing", {
    title: "Briefing", description: "What needs you, how things are going and what happens next.",
    icon: "home", group: "main", essential: true, searchable: false, actions: true, render, commands,
  });
})();
