"use strict";
// "System" page (section operations, see admin_operations.py): gateway status and
// opt-in control, host resources with a 24 h in-memory history, health checks,
// Hermes-home backups and update checks. Everything from the server is untrusted
// text: DOM is built with kit helpers and textContent only.
(() => {
  const HC = typeof window !== "undefined" ? window.HermesConsole : undefined;
  if (!HC) return;
  const STATES = { running: ["Running", "good"], starting: ["Starting", "info"], stopping: ["Stopping", "warning"],
    stopped: ["Stopped", "neutral"], failed: ["Failed", "error"], unknown: ["Unknown", "neutral"] };
  const CHECKS = { pass: ["Pass", "good"], warn: ["Warning", "warning"], fail: ["Fail", "error"], skip: ["Skipped", "neutral"] };
  const UPDATES = { available: ["Update available", "warning"], current: ["Up to date", "good"], unknown: ["Unknown", "neutral"] };
  const RECENT_JOB_MS = 10 * 60 * 1000;
  const finite = value => typeof value === "number" && Number.isFinite(value);
  const text = value => typeof value === "string" || finite(value) ? String(value) : "";
  const pick = (table, key, fallback) => Object.prototype.hasOwnProperty.call(table, key) ? table[key] : table[fallback];

  function duration(seconds) {
    if (!finite(seconds) || seconds < 0) return "—";
    const d = Math.floor(seconds / 86400), h = Math.floor(seconds % 86400 / 3600), m = Math.floor(seconds % 3600 / 60);
    return d ? d + "d " + h + "h" : h ? h + "h " + m + "m" : m ? m + "m" : Math.floor(seconds) + "s";
  }
  const percent = value => finite(value) ? (value >= 10 ? Math.round(value) : value.toFixed(1)) + "%" : "—";
  const short = sha => typeof sha === "string" && sha ? sha.slice(0, 10) : "—";
  const time = iso => { const d = new Date(iso); return Number.isNaN(d.valueOf()) ? "" : String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0"); };

  function command(kit, value) {
    const row = kit.node("div", "ops-command"), code = kit.node("code", "mono", value);
    row.append(code);
    const clip = typeof navigator !== "undefined" ? navigator.clipboard : undefined;
    if (clip && typeof clip.writeText === "function") {
      row.append(kit.button("Copy", "text-button", async () => {
        try { await clip.writeText(value); kit.toast("Copied to the clipboard."); } catch { kit.toast("Copy failed. Select the text instead."); }
      }));
    }
    return row;
  }
  function commands(kit, list) {
    const box = kit.node("div", "ops-commands");
    for (const item of Array.isArray(list) ? list : []) {
      if (!item || !text(item.command)) continue;
      if (text(item.label)) box.append(kit.node("p", "ops-command-label", item.label));
      box.append(command(kit, text(item.command)));
    }
    return box;
  }
  function meter(kit, label, used, total, note) {
    const part = kit.node("div", "resource");
    part.append(kit.node("h3", "", label));
    if (!finite(used) || !finite(total) || total <= 0) { part.append(kit.node("p", "muted", "Not available on this server.")); return part; }
    const bar = kit.node("progress", "meter" + (used / total > 0.9 ? " ops-meter-high" : ""));
    bar.max = total; bar.value = Math.min(used, total); bar.setAttribute("aria-label", label);
    const caption = kit.node("div", "meter-caption");
    caption.append(kit.node("span", "", kit.bytes(used) + " / " + kit.bytes(total) + (note ? " · " + note : "")), kit.node("span", "", percent(used / total * 100)));
    part.append(bar, caption);
    return part;
  }
  const only = (plan, id) => plan.global.filter(a => a.id === id);
  function buttons(kit, actions, cls) { const row = kit.node("div", "ops-actions"); actions.forEach(a => row.append(kit.actionButton(a, cls))); return row; }

  function status(kit, data, plan) {
    const g = data.gateway || {}, [label, kind] = pick(STATES, g.state, "unknown");
    const box = kit.panel("Gateway", "The Hermes process that connects your channels"), body = kit.node("div", "panel-body");
    const head = kit.node("div", "ops-status");
    head.append(kit.badge(label, kind));
    if (text(g.detail)) head.append(kit.node("span", "muted", g.detail));
    body.append(head);
    const platforms = (Array.isArray(g.platforms) ? g.platforms : []).map(p => text(p.name) + " (" + (text(p.state) || "unknown") + (p.needs_attention ? ", needs attention" : "") + ")").join(", ");
    const sched = data.scheduler || {}, api = data.api_server || {};
    const schedule = sched.status === "fresh" ? "Ticking · " + duration(sched.age_seconds) + " ago" : sched.status === "stale" ? "No tick for " + duration(sched.age_seconds) : "No heartbeat yet";
    body.append(kit.definition([
      ["Process ID", finite(g.pid) ? String(g.pid) : "—"], ["Uptime", duration(g.uptime_seconds)],
      ["Detected by", text(g.detected_by) || "—"], ["Channels", platforms || (g.state === "running" ? "None reported" : "—")],
      ["Active conversations", finite(g.active_agents) ? kit.number(g.active_agents) : "—"],
      ["Scheduler", schedule], ["API server", text(api.detail) || "—"]]));
    const control = data.control || {};
    if (control.enabled) {
      const actions = ["gateway_start", "gateway_restart", "gateway_stop"].flatMap(id => only(plan, id));
      if (actions.length) body.append(buttons(kit, actions, "button secondary"));
      body.append(kit.node("p", "field-help", "Controlled with " + text(control.kind) + ": " + text(control.target) + ". Stopping asks you to type the name."));
    } else {
      const off = kit.node("div", "ops-off");
      off.append(kit.node("h3", "", "Gateway control is off"));
      if (text(control.error)) off.append(kit.node("p", "notice warning", control.error));
      off.append(kit.node("p", "muted", "To start, stop and restart the gateway from here, add one line to the console's environment on the server and restart the console:"));
      off.append(commands(kit, (control.examples || []).map(e => ({ label: e.label, command: e.value }))));
      off.append(kit.node("p", "muted", "Or run it yourself on the server:"), commands(kit, control.commands));
      body.append(off);
    }
    box.append(body);
    return box;
  }

  function host(kit, data) {
    const r = data.resources || {}, box = kit.panel("Server", "Live readings from this machine"), body = kit.node("div", "panel-body");
    body.append(meter(kit, "Memory", r.memory_used_bytes, r.memory_total_bytes),
      meter(kit, "Swap", r.swap_used_bytes, r.swap_total_bytes),
      meter(kit, "Disk (Hermes home)", r.disk_used_bytes, r.disk_total_bytes, finite(r.disk_free_bytes) ? kit.bytes(r.disk_free_bytes) + " free" : ""));
    const load = [r.load_1m, r.load_5m, r.load_15m].map(v => finite(v) ? v.toFixed(2) : "—").join(" / ");
    body.append(kit.definition([["CPUs", finite(r.cpu_count) ? String(r.cpu_count) : "—"], ["Load 1 / 5 / 15 min", load],
      ["Server uptime", duration(r.host_uptime_seconds)]]));
    box.append(body);
    return box;
  }

  function history(kit, data) {
    const h = data.history || {}, points = Array.isArray(h.points) ? h.points : [];
    const box = kit.panel("Last 24 hours", "Sampled every minute while the console runs · kept in memory only, reset on restart");
    const body = kit.node("div", "panel-body ops-charts");
    if (points.length < 2) {
      body.append(kit.empty("Collecting readings", "Bars appear as the console records samples (one bar per 30 minutes).", "chart"));
    } else {
      const series = (key, title, format) => {
        const part = kit.node("div", "ops-chart"), last = points.length - 1;
        part.append(kit.node("h3", "", title));
        part.append(kit.barChart(points.map((p, i) => ({ label: time(p.t), value: finite(p[key]) ? p[key] : 0, highlight: i === last,
          title: time(p.t) + " · " + (finite(p[key]) ? format(p[key]) : "no data") })), title));
        return part;
      };
      body.append(series("memory_percent", "Memory in use", percent), series("load_1m", "CPU load (average)", v => v.toFixed(2)));
    }
    box.append(body);
    return box;
  }

  function checks(kit, data, plan) {
    const c = data.checks || {}, items = Array.isArray(c.items) ? c.items : [], s = c.summary || {};
    const summary = (finite(s.fail) && s.fail ? s.fail + " failing · " : "") + (finite(s.warn) && s.warn ? s.warn + " warning(s) · " : "") +
      (c.ran_at ? "full run " + kit.date(c.ran_at) : "quick checks, refreshed live");
    const run = only(plan, "run_checks")[0];
    const box = kit.panel("Health checks", summary, run ? kit.actionButton(run, "button secondary") : undefined), list = kit.node("ul", "ops-checks");
    for (const item of items) {
      const [label, kind] = pick(CHECKS, item.status, "skip"), row = kit.node("li", "ops-check"), info = kit.node("div");
      info.append(kit.node("strong", "", text(item.label)), kit.node("span", "muted", text(item.detail)));
      if (text(item.hint)) info.append(kit.node("span", "ops-hint", "Fix: " + item.hint));
      row.append(kit.badge(label, kind), info);
      list.append(row);
    }
    box.append(items.length ? list : kit.empty("No checks yet", "Run the checks to see how this server is set up.", "shield"));
    return box;
  }

  function jobNotice(kit, job) {
    if (!job || typeof job !== "object") return null;
    if (job.status === "running") {
      const p = job.progress || {}, done = finite(p.files) ? " · " + kit.number(p.files) + " files, " + kit.bytes(p.bytes) + " read" : "";
      return kit.node("p", "notice", (text(job.label) || "Working") + "…" + done);
    }
    const finished = Date.parse(job.finished_at);
    if (!Number.isFinite(finished) || Date.now() - finished > RECENT_JOB_MS) return null;
    return kit.node("p", job.status === "failed" ? "notice error" : "notice", (text(job.label) || "Operation") + (job.status === "failed" ? " failed: " : " finished: ") + text(job.message));
  }

  function backups(kit, data, plan) {
    const b = data.backups || {}, items = (Array.isArray(b.items) ? b.items : []).filter(kit.matches);
    const create = only(plan, "create_backup")[0];
    const box = kit.panel("Backups", "Private archives of the Hermes home on this server · " + kit.bytes(b.total_bytes ?? 0) + " in total",
      create ? kit.actionButton(create, "button primary") : undefined);
    const body = kit.node("div", "panel-body");
    const notice = jobNotice(kit, data.job);
    if (notice && (!data.job || data.job.kind === "backup")) body.append(notice);
    if (items.length) {
      body.append(kit.table(["Backup", "Size", "Created", "Secrets", ""], items.map(item => [
        kit.description(text(item.name), item === items[0] ? "Newest" : undefined), kit.bytes(item.size_bytes), kit.date(item.created_at),
        item.includes_secrets ? kit.badge("Included", "warning") : kit.badge("Excluded", "neutral"), kit.actionRow(plan.row(item))])));
    } else body.append(kit.empty("No backups yet", "Create one before changing settings or updating Hermes.", "folder"));
    const restore = kit.node("div", "ops-restore");
    restore.append(kit.node("h3", "", "Restore (on the server only)"),
      kit.node("p", "muted", "Restoring replaces files in the Hermes home, so it is never done from the web. Stop the gateway, extract the archive, then start the gateway again:"),
      commands(kit, [{ label: "1. Stop the gateway", command: text(b.stop_first) }, { label: "2. Extract the newest backup over the Hermes home", command: text(b.restore_command) }]));
    if (text(b.folder)) restore.append(kit.node("p", "field-help", "Folder: " + b.folder + " · readable only by the server account."));
    body.append(restore);
    box.append(body);
    return box;
  }

  function updates(kit, data, plan) {
    const v = data.versions || {}, u = data.updates || {}, check = only(plan, "check_updates")[0];
    const box = kit.panel("Versions & updates", u.checked_at ? "Checked " + kit.date(u.checked_at) : "Nothing is ever installed from the console",
      check ? kit.actionButton(check, "button secondary") : undefined);
    const body = kit.node("div", "panel-body");
    body.append(kit.definition([["Hermes version", text(v.hermes_version) || "—"], ["Running commit", short(v.hermes_running_commit)],
      ["Installed commit", short(v.hermes_source_commit)], ["Console commit", short(v.console_commit)],
      ["Bot mode", text(v.bot_mode) || "—"], ["Sandbox", text(v.sandbox_backend) || "Not set"]]));
    if (v.hermes_running_commit && v.hermes_source_commit && v.hermes_running_commit !== v.hermes_source_commit) {
      body.append(kit.node("p", "notice warning", "The running gateway is older than the installed code. Restart the gateway to use it."));
    }
    for (const r of Array.isArray(u.results) ? u.results : []) {
      const [label, kind] = pick(UPDATES, r.status, "unknown"), row = kit.node("div", "ops-update");
      row.append(kit.badge(label, kind), kit.description(text(r.label), text(r.message)));
      body.append(row);
    }
    body.append(kit.node("p", "muted", "To update, run on the server:"), commands(kit, u.commands));
    box.append(body);
    return box;
  }

  function render(data, root, kit) {
    const g = data.gateway || {}, r = data.resources || {}, plan = kit.actionPlan(data, Array.isArray(data.backups?.items) ? data.backups.items : []);
    kit.sectionMessage(data, root);
    for (const p of Array.isArray(data.pressure) ? data.pressure : []) root.append(kit.node("p", "notice warning", text(p.label) + ". " + text(p.detail)));
    if (data.job && data.job.kind !== "backup") { const n = jobNotice(kit, data.job); if (n) root.append(n); }
    const [state] = pick(STATES, g.state, "unknown"), metrics = kit.node("div", "metrics");
    metrics.append(kit.metric("Gateway", state, finite(g.uptime_seconds) ? "Up " + duration(g.uptime_seconds) : text(g.detected_by) ? "Checked via " + g.detected_by : "Status unknown", "server"),
      kit.metric("CPU load", finite(r.load_1m) ? r.load_1m.toFixed(2) : "—", finite(r.cpu_count) ? r.cpu_count + " CPU(s) available" : "CPU count unknown", "cpu"),
      kit.metric("Memory", percent(r.memory_percent), finite(r.memory_available_bytes) ? kit.bytes(r.memory_available_bytes) + " available" : "Not available", "activity"),
      kit.metric("Disk", percent(r.disk_percent), finite(r.disk_free_bytes) ? kit.bytes(r.disk_free_bytes) + " free" : "Not available", "folder"));
    root.append(metrics);
    const grid = kit.node("div", "details-grid ops-grid");
    grid.append(status(kit, data, plan), host(kit, data));
    root.append(grid, history(kit, data), checks(kit, data, plan), backups(kit, data, plan), updates(kit, data, plan));
    const busy = data.job?.status === "running" || ["starting", "stopping"].includes(g.state);
    kit.poll(busy ? 3000 : 30000);
  }

  HC.register("operations", { title: "System", description: "Gateway status and control, server health, backups and updates.",
    icon: "server", group: "advanced", actions: true, searchable: false, render });
})();
