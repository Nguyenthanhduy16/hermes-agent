"use strict";
// Observe pages: agent activity traces and analytics. Data shapes: src/hermes_zalo/admin_observe.py.
// Everything from the server is untrusted: DOM is built with kit helpers and textContent only.
(() => {
  const C = window.HermesConsole;
  if (!C) return;
  const PERIODS = ["1", "7", "30", "90"];
  const STATUS = { ok: ["OK", "good"], issues: ["Issues", "warning"], failed: ["Failed", "error"] };
  const blankFilters = () => ({ days: "7", platform: "", model: "", status: "", session: "" });
  const activity = { ...blankFilters(), page: 1, turn: "" };
  const analytics = { days: "7" };

  // ---- formatting ---------------------------------------------------------
  const known = v => typeof v === "number" && Number.isFinite(v);
  function secs(v) {
    if (!known(v)) return "—";
    if (v < 1) return Math.round(v * 1000) + " ms";
    if (v < 60) return v.toFixed(v < 10 ? 2 : 1) + " s";
    const m = Math.floor(v / 60), s = Math.round(v - m * 60);
    return m + "m " + String(s).padStart(2, "0") + "s";
  }
  const millis = v => known(v) ? secs(v / 1000) : "—";
  const compactFormat = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });
  const compact = v => known(v) ? compactFormat.format(v) : "—";
  const pct = v => known(v) ? (v * 100).toFixed(1) + "%" : "—";
  const days = d => d === "1" ? "Last 24 hours" : "Last " + d + " days";
  function cost(kit, value, partial) { return known(value) ? kit.money(value) + (partial ? " (partial)" : "") : kit.money(null); }
  function plural(n, word, many) { return (known(n) ? n : 0) + " " + (n === 1 ? word : many || word + "s"); }

  function exportJson(kit, data, name) {
    const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
    const blob = new Blob([JSON.stringify({ exported_at: new Date().toISOString(), ...data }, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob), link = kit.node("a");
    link.href = url; link.download = name + "-" + stamp + ".json"; link.hidden = true;
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
    kit.toast("Snapshot downloaded as JSON.");
  }

  function select(kit, label, options, value) {
    const n = kit.node("select");
    n.setAttribute("aria-label", label);
    const values = options.map(([v]) => v);
    if (value && !values.includes(value)) options = [...options, [value, value]];
    for (const [v, text] of options) { const o = kit.node("option", "", text); o.value = v; n.append(o); }
    n.value = value || "";
    return n;
  }

  // ---- activity: turn list --------------------------------------------------
  function filterForm(data, kit) {
    const facets = data.facets || {}, form = kit.node("form", "log-filter obs-filters");
    const controls = {
      days: select(kit, "Period", PERIODS.map(d => [d, days(d)]), activity.days),
      platform: select(kit, "Platform", [["", "All platforms"], ...(facets.platforms || []).map(p => [p, kit.humanize(p)])], activity.platform),
      model: select(kit, "Model", [["", "All models"], ...(facets.models || []).map(m => [m, m])], activity.model),
      status: select(kit, "Status", [["", "Any status"], ["ok", "OK only"], ["error", "With errors"]], activity.status),
      session: kit.node("input")
    };
    controls.session.value = activity.session; controls.session.maxLength = 64; controls.session.placeholder = "Session id starts with…";
    controls.session.setAttribute("aria-label", "Session id prefix");
    const apply = kit.node("button", "button secondary", "Apply"); apply.type = "submit";
    const clear = kit.button("Clear", "text-button", () => { Object.assign(activity, blankFilters(), { page: 1 }); kit.reload(); });
    form.append(...Object.values(controls), apply, clear);
    form.addEventListener("submit", event => {
      event.preventDefault();
      for (const key of Object.keys(controls)) activity[key] = String(controls[key].value || "").trim().slice(0, key === "session" ? 64 : 120);
      if (!PERIODS.includes(activity.days)) activity.days = "7";
      activity.page = 1; activity.turn = "";
      kit.reload();
    });
    return form;
  }

  function turnCard(t, kit) {
    const [label, kind] = STATUS[t.status] || [kit.humanize(t.status || "unknown"), "neutral"];
    const card = kit.node("button", "obs-turn obs-" + (STATUS[t.status] ? t.status : "unknown"));
    card.type = "button";
    card.setAttribute("aria-label", "Open turn from " + kit.date(t.started_at));
    card.addEventListener("click", () => { activity.turn = String(t.id || ""); kit.reload(); });
    const head = kit.node("span", "obs-turn-head"), when = kit.node("time", "obs-when", kit.date(t.started_at));
    head.append(when, kit.badge(t.platform ? kit.humanize(t.platform) : "Unknown platform", "info"), kit.node("span", "mono obs-session", t.session || "no session"), kit.badge(label, kind));
    const models = kit.node("span", "obs-models");
    for (const m of (t.models || []).slice(0, 4)) models.append(kit.node("span", "obs-chip", m));
    if (!(t.models || []).length) models.append(kit.node("span", "obs-chip muted", "No model call"));
    const stats = kit.node("span", "obs-stats");
    const parts = [plural(t.api_calls, "step"), plural(t.tool_calls, "tool"), compact(t.input_tokens) + " in · " + compact(t.output_tokens) + " out", secs(t.duration_s)];
    if (t.retries) parts.push(plural(t.retries, "retry", "retries"));
    if (t.api_errors) parts.push(plural(t.api_errors, "API error"));
    if (t.tool_failures) parts.push(plural(t.tool_failures, "failed tool"));
    if (t.blocked) parts.push(t.blocked + " blocked");
    for (const p of parts) stats.append(kit.node("span", "", p));
    card.append(head, models, stats);
    return card;
  }

  function renderList(data, root, kit) {
    root.append(filterForm(data, kit));
    if (data.available === false) return;
    kit.sectionMessage(data, root);
    const s = data.summary || {}, metrics = kit.node("div", "metrics");
    metrics.append(
      kit.metric("Turns", kit.number(s.turns), days(String(data.days || activity.days)), "activity"),
      kit.metric("With problems", kit.number((s.failed || 0) + (s.issues || 0)), kit.number(s.failed) + " failed · " + kit.number(s.issues) + " recovered or partial", "alert"),
      kit.metric("Turn latency", secs(s.duration_p50_s), "p50 · p95 " + secs(s.duration_p95_s), "zap"),
      kit.metric("Tokens", compact(s.input_tokens) + " / " + compact(s.output_tokens), "Uncached input / output", "hash"));
    root.append(metrics);
    if (data.truncated) root.append(kit.node("p", "notice warning", "Showing the most recent " + kit.number(data.row_limit) + " records per table; turns before " + kit.date(data.covered_since) + " are not included."));
    const box = kit.panel("Recent turns", kit.number(data.total) + " turns · newest first · times in your browser's timezone");
    const items = data.items || [];
    if (!items.length) box.append(kit.empty("No turns match", "Try a longer period or clear the filters.", "activity"));
    else { const list = kit.node("div", "obs-turns"); items.forEach(t => list.append(turnCard(t, kit))); box.append(list); }
    root.append(box);
    kit.pagination(data, root, page => { activity.page = Math.max(1, page); kit.reload(); });
    root.append(kit.node("p", "obs-footnote", "A turn groups model calls and tool calls by turn id. Older records without one are grouped per session when calls are less than " + secs(data.gap_seconds) + " apart."));
  }

  // ---- activity: one turn step by step -------------------------------------
  function stepLabel(step) { return step.kind === "api" ? (step.model || "Unknown model") : (step.tool_name || "Unknown tool"); }
  function stepMeta(step, kit) {
    const parts = [];
    if (step.kind === "api") {
      parts.push("API call" + (known(step.api_call_count) ? " #" + step.api_call_count : ""));
      if (step.retry_count) parts.push("retry " + step.retry_count);
      if (known(step.ttfb_ms)) parts.push("TTFB " + millis(step.ttfb_ms));
      if (step.finish_reason) parts.push(step.finish_reason);
      if (known(step.input_tokens) || known(step.output_tokens)) parts.push(compact(step.input_tokens) + " in · " + compact(step.output_tokens) + " out" + (step.cache_read_tokens ? " · " + compact(step.cache_read_tokens) + " cached" : ""));
      if (step.tool_call_count) parts.push(plural(step.tool_call_count, "tool call"));
      if (known(step.cost_usd)) parts.push(kit.money(step.cost_usd));
    } else {
      parts.push("Tool" + (step.tool_status ? " · " + step.tool_status : ""));
      if (known(step.requested_by)) parts.push("from step " + (step.requested_by + 1));
    }
    if (step.error_type) parts.push(step.error_type + (step.status_code ? " (" + step.status_code + ")" : ""));
    return parts.join(" · ");
  }

  function waterfall(data, kit) {
    const turn = data.turn || {}, steps = data.items || [], span = Math.max(1, Number(turn.span_ms) || 1);
    const box = kit.panel("Step by step", plural(steps.length, "step") + " over " + millis(turn.span_ms) + " · bars show when each step ran");
    const legend = kit.node("div", "obs-legend");
    for (const [cls, text] of [["api", "Model call"], ["ttfb", "Time to first token"], ["tool", "Tool"], ["error", "Error"], ["blocked", "Blocked"]]) { const item = kit.node("span", "obs-key " + cls); item.append(kit.node("i"), kit.node("span", "", text)); legend.append(item); }
    const list = kit.node("ol", "obs-waterfall");
    steps.forEach((step, index) => {
      const row = kit.node("li", "obs-step obs-" + step.kind + " obs-" + (step.status || "ok"));
      const info = kit.node("div", "obs-step-info"), title = kit.node("div", "obs-step-title");
      title.append(kit.node("span", "obs-step-index", String(index + 1)), kit.icon(step.kind === "api" ? "cpu" : "zap"), kit.node("strong", "", stepLabel(step)));
      if (step.status === "error") title.append(kit.badge("Error", "error"));
      if (step.status === "blocked") title.append(kit.badge("Blocked", "warning"));
      info.append(title, kit.node("small", "", stepMeta(step, kit)));
      const track = kit.node("div", "obs-track"), bar = kit.node("div", "obs-bar");
      const left = Math.min(100, Math.max(0, (Number(step.offset_ms) || 0) / span * 100));
      const width = Math.min(100 - left, Math.max(0.6, (Number(step.duration_ms) || 0) / span * 100));
      bar.style.left = left.toFixed(2) + "%"; bar.style.width = width.toFixed(2) + "%";
      bar.title = stepLabel(step) + ": " + millis(step.duration_ms);
      if (step.kind === "api" && known(step.ttfb_ms) && step.duration_ms > 0) { const ttfb = kit.node("div", "obs-ttfb"); ttfb.style.width = Math.min(100, step.ttfb_ms / step.duration_ms * 100).toFixed(1) + "%"; bar.append(ttfb); }
      track.append(bar);
      const time = kit.node("span", "obs-step-time", "+" + millis(step.offset_ms) + " · " + millis(step.duration_ms));
      row.append(info, track, time);
      list.append(row);
    });
    const axis = kit.node("div", "obs-axis"); axis.append(kit.node("span", "", "0"), kit.node("span", "", millis(span / 2)), kit.node("span", "", millis(turn.span_ms)));
    if (steps.length) box.append(legend, list, axis); else box.append(kit.empty("No steps", "This turn has no recorded steps.", "activity"));
    return box;
  }

  function renderTurn(data, root, kit) {
    const toolbar = kit.node("div", "section-toolbar obs-toolbar"), actions = kit.node("div", "toolbar-actions");
    actions.append(kit.button("← All turns", "button secondary", () => { activity.turn = ""; kit.reload(); }));
    const t = data.turn;
    if (t) actions.append(kit.iconButton("Export JSON", "file", "button secondary", () => exportJson(kit, data, "hermes-turn")));
    toolbar.append(kit.node("p", "", t ? "Turn from " + kit.date(t.started_at) + " · " + (t.platform ? kit.humanize(t.platform) : "unknown platform") : "Turn detail"), actions);
    root.append(toolbar);
    if (!t) { if (data.available !== false) root.append(kit.empty("Turn not found", data.message || "Go back to the list and pick another turn.", "activity")); return; }
    const [label, kind] = STATUS[t.status] || [kit.humanize(t.status || "unknown"), "neutral"];
    const metrics = kit.node("div", "metrics");
    metrics.append(
      kit.metric("Steps", plural(t.api_calls, "call") + " · " + plural(t.tool_calls, "tool"), t.retries ? plural(t.retries, "retry", "retries") : "No retries", "activity"),
      kit.metric("Latency", secs(t.duration_s), "Model " + secs(t.model_time_s) + " · tools " + secs(t.tool_time_s), "zap"),
      kit.metric("Tokens", compact(t.prompt_tokens) + " / " + compact(t.output_tokens), "Prompt incl. cache / output", "hash"),
      kit.metric("Estimated cost", cost(kit, t.cost_usd, t.cost_partial), data.pricing?.message || "From pricing.yaml", "dollar"));
    root.append(metrics);
    if (data.steps_truncated) root.append(kit.node("p", "notice warning", "Only the first " + kit.number(data.limit) + " steps are shown."));
    root.append(waterfall(data, kit));
    const facts = kit.panel("Turn facts", "Metadata only: message text and tool arguments are never recorded");
    const status = kit.node("div", "panel-body");
    const run = (t.runs || []).map(r => [r.label || "Unlabelled run", r.config_hash ? "config " + r.config_hash : "", r.hermes_rev ? "rev " + r.hermes_rev : ""].filter(Boolean).join(" · ")).join("; ");
    status.append(kit.badge(label, kind), kit.definition([
      ["Session", t.session_id || "Unknown"], ["Platform", t.platform ? kit.humanize(t.platform) : "Unknown"],
      ["Started", kit.date(t.started_at)], ["Models", (t.models || []).join(", ") || "None"],
      ["Final step", [t.final_model, t.finish_reason].filter(Boolean).join(" · ") || "Unknown"],
      ["Problems", [t.api_errors ? plural(t.api_errors, "API error") : "", t.tool_failures ? plural(t.tool_failures, "failed tool") : "", t.blocked ? t.blocked + " blocked" : "", ...(t.error_types || [])].filter(Boolean).join(" · ") || "None"],
      ["Tokens", [compact(t.input_tokens) + " uncached in", compact(t.cache_read_tokens) + " cache read", compact(t.cache_write_tokens) + " cache write", compact(t.output_tokens) + " out", compact(t.reasoning_tokens) + " reasoning"].join(" · ")],
      ["Run", run || "Unknown"],
      ["Grouping", t.grouping === "turn_id" ? "By turn id" : "By time gap (no turn id recorded)"]]));
    facts.append(status);
    root.append(facts);
  }

  C.register("activity", {
    title: "Agent activity", description: "Follow what the agent did, turn by turn and step by step.", icon: "activity", group: "observe", searchable: false,
    query() {
      if (activity.turn) return { turn: activity.turn };
      const q = { days: activity.days, platform: activity.platform, model: activity.model, status: activity.status, session: activity.session };
      if (activity.page > 1) q.page = activity.page;
      return q;
    },
    render(data, root, kit) { if (data.view === "detail" || activity.turn) renderTurn(data, root, kit); else renderList(data, root, kit); }
  });

  // ---- analytics ------------------------------------------------------------
  function periodPicker(kit) {
    const group = kit.node("div", "obs-period"); group.setAttribute("role", "group"); group.setAttribute("aria-label", "Period");
    for (const d of PERIODS) {
      const b = kit.button(d === "1" ? "24 h" : d + " days", "obs-period-button" + (analytics.days === d ? " active" : ""), () => { analytics.days = d; kit.reload(); });
      b.setAttribute("aria-pressed", String(analytics.days === d));
      group.append(b);
    }
    return group;
  }

  function daily(data, kit, key, title, unit) {
    const rows = data.daily || [], last = rows.length - 1;
    const points = rows.map((d, i) => ({ label: String(d.day).slice(5), value: Number(d[key]) || 0, highlight: i === last, title: d.day + ": " + kit.number(d[key]) + " " + unit + (d.partial ? " (partial)" : "") }));
    const box = kit.panel(title, days(String(data.days)) + " · " + (data.timezone || "UTC+07:00") + " days"), body = kit.node("div", "panel-body"), caption = kit.node("div", "chart-caption");
    const total = rows.reduce((sum, d) => sum + (Number(d[key]) || 0), 0);
    caption.append(kit.node("strong", "", key === "total_tokens" ? compact(total) : kit.number(total)), kit.node("span", "", unit));
    body.append(caption, rows.some(d => d[key]) ? kit.barChart(points, title) : kit.empty("No data in this period", "Bars appear once the agent handles requests.", "chart"));
    box.append(body);
    return box;
  }

  function tablePanel(kit, title, note, headers, rows, empty) {
    const box = kit.panel(title, note);
    box.append(rows.length ? kit.table(headers, rows) : kit.empty(empty, "Nothing recorded in this period.", "list"));
    return box;
  }

  function renderAnalytics(data, root, kit) {
    const toolbar = kit.node("div", "section-toolbar obs-toolbar"), actions = kit.node("div", "toolbar-actions");
    actions.append(periodPicker(kit));
    if (data.available !== false) actions.append(kit.iconButton("Export JSON", "file", "button secondary", () => exportJson(kit, data, "hermes-analytics-" + (data.days || analytics.days) + "d")));
    toolbar.append(kit.node("p", "", days(String(data.days || analytics.days)) + " · stored telemetry, ready to compare settings"), actions);
    root.append(toolbar);
    if (data.available === false) return;
    kit.sectionMessage(data, root);
    const k = data.kpis || {}, pricing = data.pricing || {};
    if (data.truncated) root.append(kit.node("p", "notice warning", "This period has more than " + kit.number(data.row_limit) + " records per table; figures cover " + kit.date(data.covered_since) + " onwards."));
    if (pricing.message) root.append(kit.node("p", "notice", pricing.message + (pricing.unpriced_models?.length ? " Unpriced: " + pricing.unpriced_models.join(", ") + "." : "")));
    const top = kit.node("div", "metrics"), second = kit.node("div", "metrics");
    top.append(
      kit.metric("Turns", kit.number(k.turns), kit.number(k.failed_turns) + " failed · " + (known(k.steps_per_turn) ? k.steps_per_turn + " calls per turn" : "—"), "activity"),
      kit.metric("API calls", kit.number(k.api_calls), kit.number(k.retries) + " retries · " + kit.number(k.tool_calls) + " tool calls", "cpu"),
      kit.metric("Error rate", pct(k.error_rate), kit.number(k.errors) + " failed attempts", "alert"),
      kit.metric("Latency p50", secs(k.latency_p50_s), "p95 " + secs(k.latency_p95_s) + " · per successful call", "zap"));
    second.append(
      kit.metric("TTFB p50", secs(k.ttfb_p50_s), "Time to first token · avg " + secs(k.ttfb_avg_s), "send"),
      kit.metric("Tokens", compact(k.total_tokens), compact(k.prompt_tokens) + " prompt · " + compact(k.output_tokens) + " output", "hash"),
      kit.metric("Cache hit", pct(k.cache_hit_rate), compact(k.cache_read_tokens) + " prompt tokens from cache", "memory"),
      kit.metric("Estimated cost", cost(kit, k.cost_usd, k.cost_partial), pricing.available ? "From pricing.yaml" : "Add pricing.yaml to estimate", "dollar"));
    root.append(top, second);
    const charts = kit.node("div", "obs-charts");
    charts.append(daily(data, kit, "api_calls", "Model calls per day", "calls"), daily(data, kit, "total_tokens", "Tokens per day", "tokens"));
    root.append(charts);
    root.append(tablePanel(kit, "Models", "Per requested model · latency over successful calls", ["Model", "Calls", "Tokens in / out", "Latency p50 / p95", "Avg TTFB", "Error rate", "Est. cost"],
      (data.models || []).map(m => [kit.description(m.model || "Unknown", (m.providers || []).join(", ")), kit.number(m.api_calls), compact(m.prompt_tokens) + " / " + compact(m.output_tokens), secs(m.latency_p50_s) + " / " + secs(m.latency_p95_s), secs(m.ttfb_avg_s), pct(m.error_rate), m.priced ? cost(kit, m.cost_usd, m.cost_partial) : "No price"]), "No model calls"));
    root.append(tablePanel(kit, "Tools", "Blocked calls were vetoed by assistant-core before running", ["Tool", "Calls", "Failure rate", "Blocked", "Avg duration", "p95 duration"],
      (data.tools || []).map(t => [t.tool || "Unknown", kit.number(t.calls), pct(t.failure_rate), kit.number(t.blocked), millis(t.duration_avg_ms), millis(t.duration_p95_ms)]), "No tool calls"));
    root.append(tablePanel(kit, "Platforms", "Where the turns came from", ["Platform", "Turns", "API calls", "Tool calls", "Tokens", "Error rate", "Turn p50", "Est. cost"],
      (data.platforms || []).map(p => [p.platform ? kit.humanize(p.platform) : "Unknown", kit.number(p.turns), kit.number(p.api_calls), kit.number(p.tool_calls), compact(p.total_tokens), pct(p.error_rate), secs(p.turn_latency_p50_s), cost(kit, p.cost_usd, p.cost_partial)]), "No platforms"));
    root.append(tablePanel(kit, "Runs and settings", "One row per gateway start; compare labels and config hashes to benchmark settings", ["Run", "Config", "Turns", "Calls", "Latency p50", "Error rate", "Cache hit", "Est. cost"],
      (data.runs || []).map(r => [kit.description(r.label || "Unlabelled", (r.run_id || "") + (r.started_at ? " · started " + kit.date(r.started_at) : "")), kit.description(r.config_hash || "—", r.hermes_rev ? "rev " + r.hermes_rev : ""), kit.number(r.turns), kit.number(r.api_calls), secs(r.latency_p50_s), pct(r.error_rate), pct(r.cache_hit_rate), cost(kit, r.cost_usd, r.cost_partial)]), "No runs"));
  }

  C.register("analytics", {
    title: "Analytics", description: "Latency, errors, tokens and cost over time, per model, tool and setting.", icon: "chart", group: "observe", searchable: false,
    query: () => ({ days: analytics.days }),
    render: renderAnalytics
  });
})();
