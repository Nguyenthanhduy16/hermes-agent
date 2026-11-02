"use strict";
// Experiments and Benchmarks pages (see console.js "Extension API").
// All server data is untrusted: DOM is built with kit helpers and textContent only.
(() => {
  const consoleApi = window.HermesConsole;
  if (!consoleApi || typeof consoleApi.register !== "function") return;
  const MAX_COMPARE = 4;
  const list = value => Array.isArray(value) ? value : [];
  const text = value => value === null || value === undefined ? "" : String(value);
  const finite = value => typeof value === "number" && Number.isFinite(value);

  function format(kit, value, kind) {
    if (!finite(value)) return kind === "money" ? "No price" : "—";
    if (kind === "percent") { const p = Math.round(value * 1000) / 10; return (Number.isInteger(p) ? p : p.toFixed(1)) + "%"; }
    if (kind === "seconds") return value < 10 ? value.toFixed(2) + " s" : value.toFixed(1) + " s";
    if (kind === "money") return kit.money(value);
    if (kind === "decimal") return value.toFixed(2);
    return kit.number(Math.round(value));
  }
  function duration(seconds) {
    if (!finite(seconds) || seconds < 0) return "—";
    if (seconds < 90) return Math.round(seconds) + " s";
    if (seconds < 5400) return Math.round(seconds / 60) + " min";
    if (seconds < 172800) return (seconds / 3600).toFixed(1) + " h";
    return (seconds / 86400).toFixed(1) + " d";
  }
  const short = (value, size = 10) => text(value).slice(0, size) || "—";
  const STATUS = { queued: ["Queued", "info"], running: ["Running", "info"], done: ["Done", "good"], cancelled: ["Cancelled", "warning"], failed: ["Failed", "error"] };
  const statusBadge = (kit, status) => { const [label, kind] = STATUS[status] || [text(status) || "Unknown", "neutral"]; return kit.badge(label, kind); };

  // A checkbox toggling membership in a bounded selection set.
  function selector(kit, selection, key, label) {
    const wrap = kit.node("label", "bench-select"), box = kit.node("input");
    box.type = "checkbox"; box.checked = selection.has(key);
    box.setAttribute("aria-label", "Select " + label);
    box.addEventListener("change", () => {
      if (box.checked && selection.size >= MAX_COMPARE) { box.checked = false; kit.toast("Compare up to " + MAX_COMPARE + " items."); return; }
      if (box.checked) selection.add(key); else selection.delete(key);
      kit.render();
    });
    wrap.append(box);
    return wrap;
  }
  function compareBar(kit, selection, onCompare, onClear, noun) {
    const bar = kit.node("div", "bench-compare-bar");
    bar.append(kit.node("span", "muted", selection.size ? selection.size + " " + noun + " selected · choose 2 to " + MAX_COMPARE : "Select 2 to " + MAX_COMPARE + " " + noun + " to compare."));
    const go = kit.button("Compare", "button primary", onCompare); go.disabled = selection.size < 2;
    bar.append(go);
    if (selection.size) bar.append(kit.button("Clear", "button secondary", onClear));
    return bar;
  }
  function settingsDiff(kit, settings, columns) {
    const box = kit.panel("Settings diff", "Only settings that differ · secrets are never captured");
    const rows = list(settings?.rows);
    const body = kit.node("div", "panel-body");
    if (list(settings?.missing).length) body.append(kit.node("p", "notice warning", "No settings snapshot for: " + settings.missing.map(i => columns[i] || "column " + (i + 1)).join(", ") + ". Runs from before snapshots were added cannot be diffed."));
    if (rows.length) body.append(kit.table(["Setting", ...columns], rows.map(r => [kit.node("span", "mono", text(r.key)), ...list(r.values).map(v => text(v))])));
    else body.append(kit.empty("Identical settings", "The captured settings are the same for every column.", "check"));
    if (finite(settings?.same)) body.append(kit.node("p", "muted bench-footnote", kit.number(settings.same) + " other settings are identical."));
    box.append(body);
    return box;
  }
  function matrix(kit, title, description, columns, rows) {
    const box = kit.panel(title, description), body = kit.node("div", "panel-body");
    body.append(kit.table(["Metric", ...columns], list(rows).map(r => {
      const values = list(r.values), numeric = values.filter(finite);
      const higherBetter = /pass rate/i.test(text(r.label));
      const best = numeric.length > 1 && new Set(numeric).size > 1 ? (higherBetter ? Math.max(...numeric) : Math.min(...numeric)) : null;
      const lowerBetter = /latency|cost|error|failure|tokens|calls per/i.test(text(r.label));
      return [text(r.label), ...values.map(v => { const cell = kit.node("span", "bench-value", format(kit, v, r.format)); if (best !== null && v === best && (higherBetter || lowerBetter)) cell.classList.add("best"); return cell; })];
    })));
    box.append(body);
    return box;
  }

  // ---- Experiments ------------------------------------------------------------
  const experiments = { selection: new Set(), compare: "" };
  consoleApi.register("experiments", {
    title: "Experiments", group: "observe", icon: "sliders",
    description: "Gateway runs tied to the exact settings they used, compared side by side.",
    query: () => experiments.compare ? { compare: experiments.compare } : {},
    render(data, root, kit) {
      const items = list(data.items), labels = list(data.labels);
      const metrics = kit.node("div", "metrics");
      metrics.append(kit.metric("Gateway runs", kit.number(items.length), "Most recent 100 starts", "activity"),
        kit.metric("Labels", kit.number(labels.length), "Set ASSISTANT_RUN_LABEL", "list"),
        kit.metric("With settings", kit.number(items.filter(i => i.has_snapshot).length), "Snapshot captured at start", "sliders"),
        kit.metric("Pricing", data.pricing ? "Configured" : "Not set", data.pricing ? "Costs are estimates" : "Add pricing.yaml for cost", "dollar"));
      root.append(metrics);
      if (data.available === false) return;
      const compare = data.compare;
      if (compare && list(compare.columns).length) {
        const titles = compare.columns.map(c => (c.kind === "label" ? "Label " : "") + text(c.title) + (c.kind === "label" ? " (" + list(c.runs).length + ")" : ""));
        const head = kit.node("div", "section-toolbar"); head.append(kit.node("p", "", "Comparing " + titles.join(" · ")), kit.button("Clear comparison", "button secondary", () => { experiments.compare = ""; experiments.selection.clear(); kit.reload(); }));
        root.append(head);
        root.append(matrix(kit, "Side by side", "Per-turn averages and latency percentiles from gateway telemetry", titles, compare.rows));
        const models = kit.panel("Models used", "Most frequent models per column"), body = kit.node("div", "panel-body");
        body.append(kit.table(["Column", "Models (API calls)", "Config"], compare.columns.map((c, i) => [titles[i], list(c.models).map(m => text(m.model) + " (" + kit.number(m.api_calls) + ")").join(", ") || "—", kit.node("span", "mono", list(c.config_hashes).join(", ") || "—")])));
        models.append(body); root.append(models);
        root.append(settingsDiff(kit, compare.settings, titles));
      }
      if (labels.length) {
        const box = kit.panel("Labels", "Each label groups the gateway runs started with the same ASSISTANT_RUN_LABEL"), body = kit.node("div", "panel-body");
        body.append(kit.table(["", "Label", "Runs", "Configs"], labels.filter(kit.matches).map(l => [selector(kit, experiments.selection, "label:" + text(l.label), text(l.label)), text(l.label), kit.number(l.runs), l.configs > 1 ? kit.badge(l.configs + " configs", "warning") : kit.badge("1 config", "neutral")])));
        box.append(body); root.append(box);
      }
      const box = kit.panel("Gateway runs", "One row per gateway start · select runs or labels to compare");
      const body = kit.node("div", "panel-body");
      body.append(compareBar(kit, experiments.selection, () => { experiments.compare = [...experiments.selection].join(","); kit.reload(); }, () => { experiments.selection.clear(); kit.render(); }, "items"));
      const rows = items.filter(kit.matches);
      body.append(rows.length ? kit.table(["", "Run", "Started", "Active", "Turns", "API calls", "Tokens", "Errors", "Tool failures", "Settings"], rows.map(r => [
        selector(kit, experiments.selection, text(r.id), text(r.label || r.id)),
        kit.description(text(r.label) || "Unlabeled", short(r.id, 22) + " · rev " + short(r.hermes_rev)),
        kit.date(r.started_at), duration(r.active_s), kit.number(r.turns), kit.number(r.api_calls),
        kit.number((finite(r.input_tokens) ? r.input_tokens : 0) + (finite(r.output_tokens) ? r.output_tokens : 0)),
        r.errors ? kit.badge(kit.number(r.errors), "error") : "0",
        r.tool_failures ? kit.badge(kit.number(r.tool_failures) + " / " + kit.number(r.tool_calls), "warning") : kit.number(r.tool_calls) + " ok",
        r.has_snapshot ? kit.node("span", "mono", short(r.config_hash, 16)) : kit.badge("Not captured", "neutral")]))
        : kit.empty("No gateway runs", "Runs appear after the gateway starts with assistant-core telemetry.", "activity"));
      box.append(body); root.append(box);
    }
  });

  // ---- Benchmarks ---------------------------------------------------------------
  const benchmarks = { selection: new Set(), compare: "", run: "" };
  function setup(kit, data, root) {
    const box = kit.panel("Connect the Hermes API server", "Benchmarks send prompts to the same API server the web chat uses"), body = kit.node("div", "panel-body");
    if (data.target_message) body.append(kit.node("p", "notice warning", text(data.target_message)));
    const steps = kit.node("ol", "bench-steps"); list(data.setup).forEach(step => steps.append(kit.node("li", "", text(step))));
    body.append(steps, kit.node("p", "muted", "Starting a run stays disabled until the endpoint and key are configured on the server."));
    box.append(body); root.append(box);
  }
  function progressPanel(kit, run, plan) {
    const box = kit.panel("Run in progress", text(run.suite) + (run.label ? " · " + text(run.label) : "") + " · " + text(run.target_host)), body = kit.node("div", "panel-body");
    const bar = kit.node("progress", "meter"); bar.max = Math.max(1, run.total || 1); bar.value = Math.min(bar.max, run.completed || 0);
    const caption = kit.node("div", "meter-caption");
    caption.append(kit.node("span", "", kit.number(run.completed) + " of " + kit.number(run.total) + " requests"), kit.node("span", "", kit.number(run.passed) + " passed · " + kit.number(run.errors) + " errors"));
    body.append(bar, caption, kit.actionRow(plan.row(run), "button secondary"), kit.button("Open details", "text-button", () => { benchmarks.run = text(run.id); kit.reload(); }));
    box.append(body);
    return box;
  }
  function detail(kit, run, root) {
    const summary = run.summary || {}, levels = summary.levels || {};
    const box = kit.panel("Run " + text(run.label || run.id), text(run.suite) + " · " + text(run.id), kit.button("Close", "button secondary", () => { benchmarks.run = ""; kit.reload(); }));
    const body = kit.node("div", "panel-body");
    if (run.reason) body.append(kit.node("p", run.status === "failed" ? "notice error" : "notice", text(run.reason)));
    if (run.suite_changed) body.append(kit.node("p", "notice warning", "The suite file changed after this run. New runs are not directly comparable."));
    if (run.gateway_config_hash && run.config_hash && run.gateway_config_hash !== run.config_hash) body.append(kit.node("p", "notice warning", "config.yaml differed from the running gateway's config when this run started; results may reflect older settings."));
    const metrics = kit.node("div", "metrics");
    metrics.append(kit.metric("Pass rate", format(kit, summary.pass_rate, "percent"), kit.number(summary.passed) + " of " + kit.number(summary.tasks) + " results", "check"),
      kit.metric("Median latency", format(kit, summary.median_latency_s, "seconds"), "p95 " + format(kit, summary.p95_latency_s, "seconds"), "activity"),
      kit.metric("Tokens per task", format(kit, summary.tokens_per_task, "number"), finite(summary.api_calls_per_task) ? format(kit, summary.api_calls_per_task, "decimal") + " API calls per task" : "From API usage", "hash"),
      kit.metric("Cost per task", format(kit, summary.cost_per_task, "money"), finite(summary.cost_coverage) && summary.cost_coverage < 1 ? Math.round(summary.cost_coverage * 100) + "% of results priced" : "Estimate", "dollar"));
    body.append(metrics);
    body.append(kit.definition([["Status", STATUS[run.status]?.[0] || text(run.status)], ["Levels", ["L1", "L2", "L3"].map(l => l + " " + format(kit, levels[l]?.pass_rate, "percent")).join(" · ")], ["Config", short(run.config_hash, 16)], ["Gateway config", short(run.gateway_config_hash, 16)], ["Target", text(run.target_host) || "—"], ["Repeats · parallel", text(run.repeats) + " · " + text(run.concurrency)], ["Started", kit.date(run.started_at)], ["Finished", kit.date(run.finished_at)]]));
    const results = list(run.results).filter(kit.matches);
    body.append(results.length ? kit.table(["Task", "Level", "Result", "Latency", "Tokens", "Checks"], results.map(r => {
      const failed = list(r.checks).filter(c => !c.passed), cell = kit.node("div", "bench-checks");
      if (r.error) cell.append(kit.badge(text(r.error), "error"));
      failed.forEach(c => cell.append(kit.node("small", "", text(c.type) + ": " + text(c.detail))));
      if (!r.error && !failed.length) cell.append(kit.node("small", "muted", list(r.checks).length ? "All " + list(r.checks).length + " checks passed" : "No checks"));
      if (r.response_excerpt) { const more = kit.node("details", "bench-reply"); more.append(kit.node("summary", "", "Reply excerpt"), kit.node("p", "", text(r.response_excerpt))); cell.append(more); }
      return [kit.description(text(r.task_id), "#" + text(r.attempt) + " · " + text(r.category)), kit.badge(text(r.level), "neutral"), r.passed ? kit.badge("Pass", "good") : kit.badge("Fail", "error"), format(kit, r.latency_s, "seconds"), format(kit, r.total_tokens, "number"), cell];
    })) : kit.empty("No results yet", "Results appear as each request finishes.", "list"));
    if (list(run.settings).length) { const more = kit.node("details", "bench-settings"); more.append(kit.node("summary", "", "Settings snapshot (" + run.settings.length + ")"), kit.table(["Setting", "Value"], run.settings.map(s => [kit.node("span", "mono", text(s.key)), text(s.value)]))); body.append(more); }
    box.append(body); root.append(box);
  }
  consoleApi.register("benchmarks", {
    title: "Benchmarks", group: "observe", icon: "zap", actions: true,
    description: "Run repeatable task suites against your assistant and compare settings.",
    query: () => { const q = {}; if (benchmarks.compare) q.compare = benchmarks.compare; if (benchmarks.run) q.run = benchmarks.run; return q; },
    render(data, root, kit) {
      const items = list(data.items), suites = list(data.suites), plan = kit.actionPlan(data, items);
      kit.controlToolbar(data.configured ? "Target " + text(data.target?.host) + " · model " + text(data.target?.model) : "Not connected to a Hermes API server", plan, root);
      kit.sectionMessage(data, root);
      if (data.available === false) return;
      if (!data.configured) setup(kit, data, root);
      if (data.active) { root.append(progressPanel(kit, data.active, plan)); kit.poll(3000); }
      else if (items.some(i => i.status === "queued" || i.status === "running")) kit.poll(3000);
      if (data.run) detail(kit, data.run, root);
      if (data.compare && list(data.compare.columns).length) {
        const titles = data.compare.columns.map(c => text(c.label || c.id));
        const head = kit.node("div", "section-toolbar"); head.append(kit.node("p", "", "Comparing " + titles.join(" · ")), kit.button("Clear comparison", "button secondary", () => { benchmarks.compare = ""; benchmarks.selection.clear(); kit.reload(); }));
        root.append(head);
        if (data.compare.same_suite === false) root.append(kit.node("p", "notice warning", "These runs used different suites or suite versions. Compare with care."));
        root.append(matrix(kit, "Comparison", "Best value per row is highlighted · cost needs pricing.yaml", titles, data.compare.rows));
        root.append(settingsDiff(kit, data.compare.settings, titles));
      }
      const runs = kit.panel("Runs", "Each run records the config hash and settings it measured"), body = kit.node("div", "panel-body");
      body.append(compareBar(kit, benchmarks.selection, () => { benchmarks.compare = [...benchmarks.selection].join(","); kit.reload(); }, () => { benchmarks.selection.clear(); kit.render(); }, "runs"));
      const rows = items.filter(kit.matches);
      body.append(rows.length ? kit.table(["", "Run", "Status", "Pass rate", "Progress", "Config", "Started", ""], rows.map(r => {
        const actions = kit.node("div", "bench-row-actions");
        actions.append(kit.button("Details", "text-button", () => { benchmarks.run = text(r.id); kit.reload(); }), kit.actionRow(plan.row(r), "text-button"));
        return [r.status === "done" || r.status === "cancelled" ? selector(kit, benchmarks.selection, text(r.id), text(r.label || r.id)) : "",
          kit.description(text(r.label) || "Unlabeled", text(r.suite) + " · " + short(r.id, 22)), statusBadge(kit, r.status),
          format(kit, r.pass_rate, "percent"), kit.number(r.completed) + " / " + kit.number(r.total),
          kit.node("span", "mono", short(r.config_hash, 16)), kit.date(r.started_at || r.created_at), actions];
      })) : kit.empty("No benchmark runs yet", data.configured ? "Start a run to measure the current settings." : "Connect the API server, then start a run.", "zap"));
      runs.append(body); root.append(runs);
      const box = kit.panel("Suites", "Bundled in config/bench · your own in <home>/benchmarks/*.yaml"), suiteBody = kit.node("div", "panel-body");
      suiteBody.append(suites.length ? kit.table(["Suite", "Tasks", "L1", "L2", "L3", "Source"], suites.map(s => [kit.description(text(s.name), text(s.description)), kit.number(s.tasks), kit.number(s.levels?.L1), kit.number(s.levels?.L2), kit.number(s.levels?.L3), kit.badge(s.source === "home" ? "Yours" : "Bundled", s.source === "home" ? "info" : "neutral")]))
        : kit.empty("No suites found", "Add a YAML suite to <home>/benchmarks. See docs/benchmark.md.", "list"));
      list(data.suite_problems).forEach(p => suiteBody.append(kit.node("p", "notice warning", text(p.file) + ": " + text(p.error))));
      box.append(suiteBody); root.append(box);
    }
  });
})();
