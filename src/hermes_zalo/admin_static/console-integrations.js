"use strict";
// Integrations console extension: Connectors (MCP connector catalog) and Reports (saved report reader,
// "New report" flow from templates, scheduled report jobs).
// Registers the "connectors" and "reports" pages with window.HermesConsole.register (see console.js).
// Everything from the server is untrusted: DOM is built with kit helpers, createTextNode and textContent only.
(() => {
  const C = window.HermesConsole;
  if (!C || typeof C.register !== "function") return;

  const ui = { category: "", connectorTab: "yours", connectorSearch: "", job: "", open: null, composer: null, moreTemplates: false };
  const TONES = new Set(["social", "messaging", "analytics", "productivity", "developer", "custom"]);
  const STATUS_KIND = { enabled: "good", installed: "info", available: "neutral", needs_credentials: "warning", bridge_needed: "neutral" };
  const RISK_KIND = { read: "good", unknown: "neutral", write: "warning", public: "error", messages: "error" };
  const READY_KIND = { ready: "good", needs_connector: "warning" };
  const STEPS = ["Template", "Connectors", "Schedule", "Delivery", "Confirm"];
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const text = value => typeof value === "string" ? value : "";
  const list = value => Array.isArray(value) ? value.filter(item => item && typeof item === "object") : [];
  const strings = value => Array.isArray(value) ? value.filter(item => typeof item === "string") : [];
  const textNode = value => document.createTextNode(String(value));

  // Documentation links: https only, no embedded credentials.
  function safeLink(value) {
    try {
      const url = new URL(text(value));
      return url.protocol === "https:" && !url.username && !url.password ? url.href : null;
    } catch { return null; }
  }

  // "2026-10-31T08:00:12" (cron output file time, scheduler-local) -> "31 Oct 2026 · 08:00". Never parsed as UTC.
  function fileTime(value) {
    const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(text(value));
    if (!m || Number(m[2]) < 1 || Number(m[2]) > 12) return "—";
    return Number(m[3]) + " " + MONTHS[Number(m[2]) - 1] + " " + m[1] + " · " + m[4] + ":" + m[5];
  }

  // ---- safe Markdown ------------------------------------------------------------
  // A small CommonMark subset for saved reports: headings, paragraphs, lists, block quotes, code,
  // tables, rules, **bold**, *italic*, `code` and [links](https://…). Raw HTML is shown as text,
  // links are http(s) only and open with rel="noopener noreferrer". Input and output are bounded.
  const MD_MAX_CHARS = 64000, MD_MAX_BLOCKS = 1500, MD_MAX_DEPTH = 4, MD_MAX_COLUMNS = 12, MD_MAX_ROWS = 300;
  const INLINE = /`([^`\n]{1,500})`|\*\*(?=\S)([^\n]{0,1000}?\S)\*\*|__(?=\S)([^\n]{0,1000}?\S)__|\*(?=[^\s*])([^*\n]{0,1000}?[^\s*])?\*|(?<![\p{L}\p{N}_])_(?=[^\s_])([^_\n]{0,1000}?[^\s_])?_(?![\p{L}\p{N}_])|\[([^[\]\n]{1,300})\]\(([^()\s]{1,2000})\)/u;
  const FENCE = /^\s{0,3}(`{3,}|~{3,})/, HEADING = /^\s{0,3}(#{1,6})\s+(.*?)(?:\s+#+)?\s*$/, RULE = /^\s{0,3}([-*_])(?:\s*\1){2,}\s*$/;
  const QUOTE = /^\s{0,3}>\s?(.*)$/, ITEM = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/, TABLE_RULE = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;

  function mdLink(value) {
    try {
      const url = new URL(value);
      return (url.protocol === "https:" || url.protocol === "http:") && !url.username && !url.password ? url.href : null;
    } catch { return null; }
  }

  function inline(kit, value, depth = 0) {
    const out = [];
    let rest = String(value), guard = 0;
    while (rest) {
      const m = depth < MD_MAX_DEPTH && guard++ < 2000 ? INLINE.exec(rest) : null;
      if (!m) { out.push(textNode(rest)); break; }
      if (m.index) out.push(textNode(rest.slice(0, m.index)));
      if (m[1] !== undefined) out.push(kit.node("code", "md-code", m[1]));
      else if (m[2] !== undefined || m[3] !== undefined) { const b = kit.node("strong"); b.append(...inline(kit, m[2] ?? m[3], depth + 1)); out.push(b); }
      else if (m[6] !== undefined) {
        const href = mdLink(m[7]);
        if (href) {
          const a = kit.node("a", "md-link"); a.href = href; a.target = "_blank"; a.rel = "noopener noreferrer";
          a.append(...inline(kit, m[6], depth + 1)); out.push(a);
        } else out.push(textNode(m[0]));
      } else {
        const inner = m[0].slice(1, -1);
        if (inner) { const i = kit.node("em"); i.append(...inline(kit, inner, depth + 1)); out.push(i); } else out.push(textNode(m[0]));
      }
      rest = rest.slice(m.index + m[0].length);
    }
    return out;
  }

  function cells(line) {
    let row = line.trim();
    if (row.startsWith("|")) row = row.slice(1);
    if (row.endsWith("|") && !row.endsWith("\\|")) row = row.slice(0, -1);
    return row.split(/(?<!\\)\|/).map(c => c.trim().replace(/\\\|/g, "|")).slice(0, MD_MAX_COLUMNS);
  }

  function table(kit, lines) {
    const head = cells(lines[0]), align = cells(lines[1]).map(c => c.endsWith(":") ? (c.startsWith(":") ? "center" : "right") : "");
    const wrap = kit.node("div", "md-table"), t = kit.node("table"), thead = kit.node("thead"), tbody = kit.node("tbody"), hr = kit.node("tr");
    const cell = (tag, value, index) => { const c = kit.node(tag, align[index] ? "md-align-" + align[index] : ""); c.append(...inline(kit, value)); return c; };
    head.forEach((value, index) => hr.append(cell("th", value, index)));
    thead.append(hr);
    for (const line of lines.slice(2, 2 + MD_MAX_ROWS)) {
      const tr = kit.node("tr"), row = cells(line);
      head.forEach((_, index) => tr.append(cell("td", row[index] ?? "", index)));
      tbody.append(tr);
    }
    t.append(thead, tbody); wrap.append(t);
    return wrap;
  }

  const isTable = (lines, i) => lines[i].includes("|") && i + 1 < lines.length && lines[i + 1].includes("-") && TABLE_RULE.test(lines[i + 1]);
  const startsBlock = (lines, i) => FENCE.test(lines[i]) || HEADING.test(lines[i]) || RULE.test(lines[i]) || QUOTE.test(lines[i]) || ITEM.test(lines[i]) || isTable(lines, i);

  function renderMarkdown(kit, source) {
    const box = kit.node("div", "int-md");
    const lines = String(source ?? "").slice(0, MD_MAX_CHARS).replace(/\r\n?/g, "\n").split("\n");
    let i = 0, blocks = 0;
    const add = el => { blocks++; box.append(el); };
    while (i < lines.length && blocks < MD_MAX_BLOCKS) {
      const line = lines[i];
      if (!line.trim()) { i++; continue; }
      const fence = FENCE.exec(line);
      if (fence) {
        const body = []; i++;
        while (i < lines.length && !lines[i].trimStart().startsWith(fence[1])) body.push(lines[i++]);
        i++;
        const pre = kit.node("pre", "md-pre"); pre.append(kit.node("code", "", body.join("\n"))); add(pre); continue;
      }
      const heading = HEADING.exec(line);
      if (heading) {
        const level = heading[1].length, h = kit.node("h" + Math.min(6, level + 2), "md-h md-h" + level);
        h.append(...inline(kit, heading[2])); add(h); i++; continue;
      }
      if (isTable(lines, i)) {
        const rows = [lines[i], lines[i + 1]]; i += 2;
        while (i < lines.length && lines[i].includes("|") && lines[i].trim()) rows.push(lines[i++]);
        add(table(kit, rows)); continue;
      }
      if (RULE.test(line)) { add(kit.node("hr", "md-rule")); i++; continue; }
      if (QUOTE.test(line)) {
        const body = [];
        while (i < lines.length && QUOTE.test(lines[i])) body.push(QUOTE.exec(lines[i++])[1]);
        const quote = kit.node("blockquote", "md-quote"), p = kit.node("p");
        p.append(...inline(kit, body.join(" ").trim())); quote.append(p); add(quote); continue;
      }
      const item = ITEM.exec(line);
      if (item) {
        const ordered = /\d/.test(item[2]), listEl = kit.node(ordered ? "ol" : "ul", "md-list"), base = item[1].length;
        if (ordered && parseInt(item[2], 10) !== 1) listEl.start = parseInt(item[2], 10);
        let last = null;
        while (i < lines.length && lines[i].trim()) {
          const next = ITEM.exec(lines[i]);
          if (next && /\d/.test(next[2]) === ordered || next && next[1].length > base) {
            last = kit.node("li", next[1].length > base ? "md-nested" : "");
            last.append(...inline(kit, next[3])); listEl.append(last); i++;
          } else if (!next && last && /^\s+/.test(lines[i]) && !startsBlock(lines, i)) {
            last.append(textNode(" "), ...inline(kit, lines[i].trim())); i++;
          } else break;
        }
        add(listEl); continue;
      }
      const body = [line.trim()]; i++;
      while (i < lines.length && lines[i].trim() && !startsBlock(lines, i)) body.push(lines[i++].trim());
      const p = kit.node("p"); p.append(...inline(kit, body.join(" "))); add(p);
    }
    if (blocks >= MD_MAX_BLOCKS && i < lines.length) box.append(kit.node("p", "int-note", "The rest of this report is not shown."));
    return box;
  }

  // ---- connectors ---------------------------------------------------------------
  function logo(kit, item) {
    const tone = TONES.has(item.category) ? item.category : "custom";
    const mark = kit.node("span", "int-logo int-tone-" + tone, Array.from(text(item.initials) || text(item.name) || "?").slice(0, 2).join(""));
    mark.setAttribute("aria-hidden", "true");
    // Fixed local artwork only. No remote images, HTML or server-supplied SVG paths.
    const artwork = {
      "google-drive": [["path", { d: "M9 2h6l8 14h-6Z", fill: "#fbbc04" }], ["path", { d: "M9 2 1 16l3 5 8-14Z", fill: "#34a853" }], ["path", { d: "M4 21h16l3-5H7Z", fill: "#4285f4" }]],
      gmail: [["path", { d: "M3 20V5l9 7 9-7v15", fill: "none", stroke: "#ea4335", "stroke-width": "4", "stroke-linejoin": "round" }]],
      youtube: [["rect", { x: "1", y: "4", width: "22", height: "16", rx: "5", fill: "#ff0033" }], ["path", { d: "m10 8 6 4-6 4Z", fill: "#fff" }]],
      "google-analytics": [["rect", { x: "2", y: "14", width: "5", height: "8", rx: "2", fill: "#f9ab00" }], ["rect", { x: "9", y: "8", width: "5", height: "14", rx: "2", fill: "#e37400" }], ["rect", { x: "16", y: "2", width: "5", height: "20", rx: "2", fill: "#f9ab00" }]],
    }[item.id];
    if (artwork) {
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg"); svg.setAttribute("viewBox", "0 0 24 24"); svg.setAttribute("width", "27"); svg.setAttribute("height", "27");
      for (const [tag, attributes] of artwork) { const shape = document.createElementNS("http://www.w3.org/2000/svg", tag); for (const [key, value] of Object.entries(attributes)) shape.setAttribute(key, value); svg.append(shape); }
      mark.replaceChildren(svg);
    }
    return mark;
  }

  function capabilities(kit, item) {
    const caps = list(item.capabilities);
    if (!caps.length) return null;
    const box = kit.node("ul", "int-caps");
    box.setAttribute("aria-label", "What the assistant can do");
    for (const cap of caps) {
      const row = kit.node("li");
      row.append(kit.badge(text(cap.access_label) || "Unknown", RISK_KIND[cap.access] || "neutral"), kit.node("span", "", text(cap.label)));
      box.append(row);
    }
    return box;
  }

  function credentials(kit, item) {
    const creds = list(item.credentials);
    if (!creds.length) return null;
    const box = kit.node("div", "int-block");
    box.append(kit.node("h3", "int-block-title", "Credentials"));
    for (const cred of creds) {
      const row = kit.node("div", "int-row"), info = kit.node("div", "int-names");
      info.append(kit.node("span", "mono", text(cred.variable)), kit.node("small", "muted", cred.writable === false ? "Set on the server · " + text(cred.help) : text(cred.help)));
      row.append(info, cred.configured === true ? kit.badge(cred.source === "process environment" ? "Set · environment" : "Set", "good") : kit.badge("Missing", cred.writable === false ? "neutral" : "warning"));
      box.append(row);
    }
    return box;
  }

  function lastTest(kit, test) {
    if (!test || typeof test !== "object") return null;
    if (test.ok === true) {
      const names = strings(test.tools), count = typeof test.count === "number" ? test.count : names.length;
      return kit.node("p", "int-test good", "Last test " + kit.date(test.at) + ": " + kit.number(count) + " tools" + (names.length ? " · " + names.slice(0, 6).join(", ") + (count > 6 ? ", …" : "") : ""));
    }
    return kit.node("p", "int-test error", "Last test " + kit.date(test.at) + " failed: " + (text(test.error) || "unknown error"));
  }

  function servers(kit, item) {
    const rows = list(item.servers);
    if (!rows.length) return null;
    const box = kit.node("div", "int-block");
    box.append(kit.node("h3", "int-block-title", rows.length === 1 ? "Installed server" : "Installed servers"));
    for (const server of rows) {
      const row = kit.node("div", "int-server"), top = kit.node("div", "int-row"), info = kit.node("div", "int-names");
      info.append(kit.node("strong", "mono", text(server.name)), kit.node("small", "muted", [server.transport === "stdio" ? "Local command" : server.transport === "http" ? "HTTP" : "Unknown transport", text(server.host)].filter(Boolean).join(" · ")));
      top.append(info, kit.yesNo(server.enabled, "Enabled", "Disabled"));
      row.append(top);
      if (server.auth_method === "oauth_operator") row.append(kit.node("p", "int-note", "OAuth: authorize and test this server using Hermes MCP CLI or its separate Dashboard. Browser sign-in is not available here."));
      const exposed = strings(server.exposed_to);
      if (exposed.length) row.append(kit.node("p", "int-test warning", "Chat users on " + exposed.join(", ") + " would receive its tools, so it cannot be enabled until those channels list no_mcp."));
      const missing = strings(server.missing_credentials);
      if (missing.length) row.append(kit.node("p", "int-test warning", "Needs " + missing.join(", ") + " in the bot home .env or environment."));
      const test = lastTest(kit, server.last_test); if (test) row.append(test);
      box.append(row);
    }
    return box;
  }

  function links(kit, item) {
    const docs = list(item.docs).map(doc => [text(doc.label) || "Documentation", safeLink(doc.url)]).filter(([, href]) => href);
    if (!docs.length) return null;
    const box = kit.node("div", "int-links");
    for (const [label, href] of docs) {
      const a = kit.node("a", "text-button", label + " ↗");
      a.href = href; a.target = "_blank"; a.rel = "noopener noreferrer";
      box.append(a);
    }
    return box;
  }

  function snippet(kit, item) {
    if (!text(item.snippet)) return null;
    const box = kit.node("details", "run-history");
    box.append(kit.node("summary", "", "Install on the server · config snippet"), kit.node("pre", "run-output", item.snippet),
      kit.node("p", "muted", "Add this to the bot home config.yaml on the server, set the credentials, then enable it here once chat channels list no_mcp."));
    return box;
  }

  function isYours(item) {
    return item.configured === true || list(item.servers).length > 0 || list(item.credentials).some(c => c.configured === true);
  }
  function methodHint(item) {
    if (list(item.servers).some(s => s.auth_method === "oauth_operator")) return "OAuth · authorize in Hermes CLI / Dashboard";
    return text(item.connection?.hint) || ({ bridge: "Your bridge · URL + optional token", local: "Local server · operator setup", remote: "Hosted MCP · bearer token", custom: "MCP URL · optional token" }[item.kind] || "Review setup requirements");
  }
  function testState(item) {
    const tests = list(item.servers).map(s => s.last_test);
    if (tests.some(t => t && t.ok === false)) return ["Test failed", "error"];
    if (tests.length && tests.every(t => t && t.ok === true)) return ["Test passed", "good"];
    return ["Not tested", "neutral"];
  }
  function validateConnection(values) {
    if (!/^[a-z][a-z0-9_-]{0,47}$/.test(String(values.name || ""))) throw new Error("Use a server name starting with a letter, followed by lowercase letters, numbers, - or _.");
    let url; try { url = new URL(values.url); } catch { throw new Error("Enter a valid MCP endpoint URL."); }
    const local = ["localhost", "127.0.0.1", "[::1]", "::1"].includes(url.hostname);
    if ((url.protocol !== "https:" && !(url.protocol === "http:" && local)) || url.username || url.password || url.hash || url.search)
      throw new Error("Use HTTPS, or HTTP on localhost, without credentials, query parameters or a fragment.");
  }
  let connectorDialog = null;
  function closeConnector() {
    if (!connectorDialog) return;
    const dialog = connectorDialog; connectorDialog = null;
    dialog.close(); dialog.remove();
  }
  function setupConnection(kit, item, action) {
    closeConnector(); ui.connectorTab = "yours";
    kit.openAction(action, {
      title: "Connect " + (text(item.name) || "MCP server"), advanced: ["timeout", "enabled"], validate: validateConnection,
      description: "Save the endpoint securely. New connections stay disabled unless you enable them in Advanced settings.",
      review: "After saving, open Manage to test the server and review its tools. Enable it only after checking channel access, then restart the gateway. Runtime use is not verified here.",
    });
  }
  function connectorDetails(kit, item, actions, root, trigger) {
    closeConnector();
    const dialog = kit.node("dialog", "co-dialog"), head = kit.node("div", "dialog-heading"), title = kit.node("h2", "", text(item.name) || "Connector");
    title.id = "connector-dialog-title"; dialog.setAttribute("aria-labelledby", title.id);
    dialog.setAttribute("aria-modal", "true");
    const close = kit.button("Close", "button secondary", closeConnector);
    head.append(logo(kit, item), title, close); dialog.append(head);
    const body = kit.node("div", "co-dialog-body");
    body.append(kit.node("p", "co-publisher", text(item.publisher) || "Review provider documentation"), kit.node("p", "co-benefit", text(item.description)),
      kit.node("p", "co-method", methodHint(item)));
    const yours = isYours(item), hasServers = list(item.servers).length > 0;
    if (yours) {
      const state = kit.node("div", "co-status");
      state.append(kit.badge(hasServers ? list(item.servers).some(s => s.enabled) ? "Enabled in configuration" : "Saved · disabled" : "Setup incomplete", "info"), kit.badge(...testState(item)));
      body.append(state, kit.node("p", "int-note", text(item.runtime_note) || "Restart the gateway after changes. Runtime use is not verified by this Console."));
      for (const part of [servers(kit, item)]) if (part) body.append(part);
    }
    const access = kit.node("section", "co-access");
    access.append(kit.node("h3", "", "What you're allowing"), kit.node("p", "int-note", "Capabilities depend on the server and permissions you grant. Review its tools before enabling."));
    const caps = capabilities(kit, item); if (caps) access.append(caps); body.append(access);
    if (!hasServers) {
      const steps = kit.node("ol", "co-instructions");
      const defaults = item.kind === "local" ? ["Have your operator install the documented package in this bot's environment.", "Add the config snippet and credentials on the server, then refresh this page."] :
        item.kind === "bridge" ? ["Run an MCP bridge for this app and authorize the provider inside that bridge.", "Use its MCP URL and bridge token here. Saving a provider API key alone does not create a bridge."] :
        ["Review the provider's setup documentation and use the narrowest permissions.", "Save the MCP URL and authentication method, then test before enabling."];
      for (const step of strings(item.connection?.steps).length ? strings(item.connection.steps) : defaults) steps.append(kit.node("li", "", step));
      body.append(kit.node("h3", "", item.kind === "local" ? "Operator setup" : "How to connect"), steps);
      if (item.kind === "local") body.append(kit.node("p", "co-prerequisite", "Saving a credential does not install or start the local server."));
    }
    const docs = links(kit, item); if (docs) body.append(docs);
    const advanced = kit.node("details", "co-advanced"); advanced.append(kit.node("summary", "", "Advanced setup and credentials"));
    if (text(item.note)) advanced.append(kit.node("p", "int-note", item.note));
    advanced.append(kit.node("p", "int-note", text(item.connection?.oauth_note) || "Browser OAuth is not available in this Console. Use the Hermes MCP CLI or its separate Dashboard for OAuth servers."));
    for (const part of [snippet(kit, item), item.kind === "local" || list(item.servers).some(s => s.transport === "stdio") ? credentials(kit, item) : null]) if (part) advanced.append(part);
    if (text(item.api)) advanced.append(kit.node("p", "int-note", "API: " + item.api));
    const credentialActions = actions.filter(a => ["set_credential", "remove_credential"].includes(a.id));
    for (const action of credentialActions) advanced.append(kit.button(action.label, "text-button", () => { closeConnector(); kit.openAction(action); }));
    body.append(advanced); dialog.append(body);
    const footer = kit.node("div", "co-dialog-actions");
    const install = actions.find(a => a.id === "install");
    if (install) footer.append(kit.button(item.kind === "bridge" ? "I have a bridge · Continue" : "Continue", "button primary", () => setupConnection(kit, item, install)));
    if (!hasServers && !install) footer.append(kit.button("Refresh status", "button primary", () => { closeConnector(); kit.reload(); }));
    for (const action of actions.filter(a => !["install", "set_credential", "remove_credential"].includes(a.id))) {
      const name = (action.fields || []).find(f => f.name === "name")?.value;
      const labels = { test_connection: "Test connection", replace_token: "Replace token", remove_server: "Disconnect" };
      const label = (labels[action.id] || action.label) + (list(item.servers).length > 1 && name ? " · " + name : "");
      footer.append(kit.button(label, action.id === "remove_server" ? "button danger" : "button secondary", () => { closeConnector(); kit.openAction(action); }));
    }
    dialog.append(footer); document.getElementById("content").append(dialog); connectorDialog = dialog;
    dialog.addEventListener("close", () => { if (connectorDialog === dialog) connectorDialog = null; dialog.remove(); trigger?.focus(); });
    dialog.showModal(); close.focus();
  }
  function connectorCard(kit, item, plan, root) {
    const card = kit.node("article", "co-card"), info = kit.node("div", "co-card-info"), yours = isYours(item);
    info.append(kit.node("h2", "", text(item.name) || "Connector"), kit.node("p", "co-benefit", text(item.description)),
      kit.node("p", "co-publisher", text(item.publisher) || "See provider documentation"), kit.node("p", "co-method", methodHint(item)));
    if (yours) {
      const statuses = kit.node("div", "co-status");
      statuses.append(kit.badge(list(item.servers).some(s => s.enabled) ? "Enabled in config" : list(item.servers).length ? "Saved · disabled" : "Needs setup", "info"), kit.badge(...testState(item)));
      info.append(statuses);
    }
    const action = kit.button(yours ? "Manage" : "+", "co-card-action", () => connectorDetails(kit, item, plan.row(item), root, action));
    action.setAttribute("aria-label", (yours ? "Manage " : "Connect ") + (text(item.name) || "connector"));
    action.title = (yours ? "Manage " : "Connect ") + text(item.name);
    card.append(logo(kit, item), info, action); return card;
  }

  function strip(kit, entries, label) {
    const box = kit.node("div", "int-strip");
    box.setAttribute("role", "group"); box.setAttribute("aria-label", label);
    for (const [title, value, tone] of entries) {
      const cell = kit.node("div", "int-stat int-stat-" + tone);
      cell.append(kit.node("strong", "", kit.number(value ?? 0)), kit.node("span", "", title));
      box.append(cell);
    }
    return box;
  }

  function hint(kit, data, root) {
    if (data.available !== false && text(data.message)) root.append(kit.node("p", "int-hint", data.message));
  }

  function renderConnectors(data, root, kit) {
    const all = list(data.items), plan = kit.actionPlan(data, all), toolbar = kit.node("div", "co-toolbar"), tabs = kit.node("div", "co-tabs");
    tabs.setAttribute("role", "tablist"); tabs.setAttribute("aria-label", "Connector library");
    const results = kit.node("section", "co-results"); results.id = "connector-results"; results.setAttribute("role", "tabpanel");
    const tabButtons = [];
    function selectTab(value, focus = false) {
      ui.connectorTab = value;
      tabButtons.forEach(([key, button]) => { const active = key === value; button.setAttribute("aria-selected", String(active)); button.tabIndex = active ? 0 : -1; button.className = "co-tab" + (active ? " active" : ""); if (active && focus) button.focus(); });
      results.setAttribute("aria-labelledby", "connector-tab-" + value); renderResults();
    }
    for (const [value, label] of [["yours", "Yours"], ["discover", "Discover"]]) {
      const button = kit.button(label, "co-tab", () => selectTab(value)); button.id = "connector-tab-" + value;
      button.setAttribute("role", "tab"); button.setAttribute("aria-controls", results.id);
      button.addEventListener("keydown", event => { if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) { event.preventDefault(); selectTab(event.key === "Home" ? "yours" : event.key === "End" ? "discover" : ui.connectorTab === "yours" ? "discover" : "yours", true); } });
      tabs.append(button); tabButtons.push([value, button]);
    }
    const searchLabel = kit.node("label", "co-search"), search = kit.node("input"); search.type = "search";
    search.placeholder = "Search connectors"; search.setAttribute("aria-label", "Search connectors"); search.maxLength = 120; search.value = ui.connectorSearch;
    search.addEventListener("input", () => { ui.connectorSearch = search.value.slice(0, 120); renderResults(); }); searchLabel.append(kit.icon("search"), search);
    const category = kit.node("select", "co-filter"); category.setAttribute("aria-label", "Filter by category");
    const any = kit.node("option", "", "All categories"); any.value = ""; category.append(any);
    for (const c of list(data.categories)) { const option = kit.node("option", "", text(c.label) || text(c.id)); option.value = text(c.id); category.append(option); }
    if (!list(data.categories).some(c => c.id === ui.category)) ui.category = "";
    category.value = ui.category; category.addEventListener("change", () => { ui.category = category.value; renderResults(); });
    const custom = all.find(i => i.id === "custom"), add = kit.button("+ Add", "button primary", () => { if (custom) connectorDetails(kit, custom, plan.row(custom), root, add); });
    add.disabled = !custom;
    toolbar.append(tabs, searchLabel, category, add); root.append(toolbar);
    if (kit.isDemo()) root.append(kit.note("Demo connectors", "Setup changes and connection tests are synthetic; no external account is contacted.", "info"));
    if (data.available === false) root.append(kit.note("Connectors unavailable", text(data.message), "warning"));
    root.append(results);
    function renderResults() {
      results.replaceChildren();
      const yours = all.filter(isYours), base = ui.connectorTab === "yours" ? yours : all;
      if (ui.connectorTab === "yours" && !yours.length) {
        results.append(kit.empty("Your apps, together", "Connect an app to give Hermes the context and tools you choose.", "plug"), kit.button("Discover connectors", "button primary", () => selectTab("discover", true))); return;
      }
      const query = ui.connectorSearch.trim().toLocaleLowerCase("en");
      const items = base.filter(i => (!ui.category || i.category === ui.category) && (!query || [i.name, i.description, i.publisher, i.category_label, methodHint(i)].some(v => text(v).toLocaleLowerCase("en").includes(query))) && kit.matches(i));
      const heading = kit.node("div", "co-results-heading");
      heading.append(kit.node("h2", "", ui.connectorTab === "yours" ? "Your connectors" : "Explore connectors"), kit.node("span", "muted", kit.number(items.length) + (items.length === 1 ? " app" : " apps"))); results.append(heading);
      if (!items.length) { results.append(kit.empty("No matching connectors", "Try another search or category.", "search")); return; }
      const cards = kit.node("div", "co-grid"); items.forEach(item => cards.append(connectorCard(kit, item, plan, root))); results.append(cards);
      results.append(kit.node("p", "co-footnote", "You choose each app's access. Saved settings and connection tests do not confirm that the running gateway has applied them."));
    }
    selectTab(ui.connectorTab);
  }

  // ---- reports: reader ---------------------------------------------------------------
  function openReport(kit, row) {
    ui.open = row ? { job: text(row.job), file: text(row.file) } : null;
    kit.reload({ quiet: true });
  }

  const REPORT_JOB = /^[A-Za-z0-9_-]{1,64}$/, REPORT_FILE = /^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.md$/;
  function showReports() {
    if (C.kit.view() === "reports") C.kit.reload({ quiet: true });
    else C.kit.navigate("reports");
  }
  // Home supplies a validated reference; keep it before navigate() starts the request.
  window.HermesReports = Object.freeze({
    open(job, file) {
      if (typeof job !== "string" || typeof file !== "string" || !REPORT_JOB.test(job) || !REPORT_FILE.test(file)) return false;
      ui.open = { job, file }; ui.job = job; ui.composer = false;
      showReports(); return true;
    },
    create() { ui.composer = true; ui.moreTemplates = false; if (C.kit.view() === "reports") C.kit.render(); else C.kit.navigate("reports"); },
  });

  function savedList(kit, data, saved, reader) {
    const box = kit.node("div", "int-saved");
    const jobs = list(data.items).filter(j => (j.outputs ?? 0) > 0);
    if (ui.job && !jobs.some(j => j.id === ui.job)) ui.job = "";
    const select = kit.node("select", "int-select");
    select.setAttribute("aria-label", "Filter saved reports by report job");
    for (const [value, label] of [["", "All reports · " + kit.number(saved.length)], ...jobs.map(j => [text(j.id), [text(j.template_name) || text(j.name), text(j.account)].filter(Boolean).join(" · ")])]) {
      const option = kit.node("option", "", label); option.value = value; select.append(option);
    }
    select.value = ui.job;
    select.addEventListener("change", () => { ui.job = select.value; kit.render(); });
    box.append(select);
    const rows = saved.filter(r => (!ui.job || r.job === ui.job) && kit.matches(r));
    if (!rows.length) { box.append(kit.node("p", "int-note", saved.length ? "No saved reports match." : "No saved reports yet. They appear after the first run.")); return box; }
    const ul = kit.node("ul", "int-saved-list");
    ul.setAttribute("aria-label", "Saved reports, newest first");
    for (const row of rows) {
      const active = reader && reader.job === row.job && reader.file === row.file;
      const b = kit.button("", "int-saved-row" + (active ? " active" : ""), () => { if (!active) openReport(kit, row); });
      b.append(kit.node("strong", "", text(row.title) || "Report"), kit.node("span", "muted", [text(row.account), fileTime(row.at)].filter(Boolean).join(" · ")));
      if (active) b.setAttribute("aria-current", "true");
      const li = kit.node("li"); li.append(b); ul.append(li);
    }
    box.append(ul);
    return box;
  }

  function readerPane(kit, data, reader) {
    const pane = kit.node("article", "int-reader");
    if (text(data.reader_error)) {
      pane.append(kit.node("p", "notice warning", data.reader_error), kit.button("Show the latest report", "button secondary", () => openReport(kit, null)));
      return pane;
    }
    if (!reader || typeof reader !== "object") {
      pane.append(kit.empty("Nothing to read yet", "Saved reports open here. Create a report below or wait for the next scheduled run.", "file"));
      return pane;
    }
    const head = kit.node("header", "int-reader-head"), titles = kit.node("div", "int-names");
    titles.append(kit.node("h2", "", text(reader.title) || "Report"),
      kit.node("small", "muted", [text(reader.template_name), text(reader.account), fileTime(reader.at)].filter(Boolean).join(" · ")));
    head.append(titles, kit.badge("Saved", "good"));
    pane.append(head);
    if (reader.truncated) pane.append(kit.node("p", "int-note", "Long report · showing the first " + kit.number(MD_MAX_CHARS) + " characters."));
    // The header already shows the title: drop a leading heading that repeats it.
    let body = text(reader.text);
    const first = /^\s*#{1,6}\s+(.*?)\s*#*\s*(?:\n|$)/.exec(body);
    if (first && first[1].trim() === text(reader.title).trim()) body = body.slice(first[0].length);
    pane.append(renderMarkdown(kit, body));
    return pane;
  }

  // ---- reports: new report flow -------------------------------------------------------
  function stepper(kit, current) {
    const ol = kit.node("ol", "int-steps");
    ol.setAttribute("aria-label", "New report steps");
    STEPS.forEach((label, index) => {
      const li = kit.node("li", index === current ? "active" : index < current ? "done" : "");
      li.append(kit.node("span", "int-step-no", String(index + 1)), kit.node("span", "", label));
      if (index === current) li.setAttribute("aria-current", "step");
      ol.append(li);
    });
    return ol;
  }

  function templateCard(kit, template, plan) {
    const card = kit.node("article", "card int-card int-template"), head = kit.node("div", "card-header");
    const needs = list(template.connectors), enabled = needs.filter(c => c.status === "enabled");
    head.append(kit.iconBox("file", template.readiness === "ready" ? "good" : "accent"), kit.badge(template.readiness === "ready" ? "Ready" : "Needs a connector", READY_KIND[template.readiness] || "neutral"));
    card.append(head, kit.node("h2", "", text(template.name) || "Report"), kit.node("p", "int-desc", text(template.description)));
    if (needs.length) {
      const row = kit.node("div", "int-chips");
      row.setAttribute("aria-label", "Connectors used");
      const shown = enabled.length ? enabled : needs.slice(0, 4);
      for (const c of shown) row.append(kit.badge(text(c.name) + " · " + (text(c.status_label) || "Unknown"), STATUS_KIND[c.status] || "neutral"));
      if (needs.length > shown.length) row.append(kit.node("span", "muted", "+" + kit.number(needs.length - shown.length) + " more"));
      card.append(row);
    }
    const sections = strings(template.sections);
    if (sections.length) card.append(kit.node("p", "int-note", "Sections: " + sections.join(" · ")));
    card.append(kit.node("p", "int-note", ["Suggested: " + kit.describeSchedule(template.schedule), (template.reports ?? 0) ? kit.number(template.reports) + " scheduled" : ""].filter(Boolean).join(" · ")));
    const create = plan.row(template).find(a => a.id === "create_report");
    const row = kit.node("div", "card-actions");
    if (create) row.append(kit.actionButton(create, "button primary", "Use template"));
    plan.row(template).filter(a => a !== create).forEach(a => row.append(kit.actionButton(a)));
    card.append(row);
    return card;
  }

  function composer(kit, templates, plan) {
    const box = kit.panel("New report", "Pick a template. The form then asks for connectors (only enabled ones), the schedule and delivery; confirm to create it.",
      kit.button("Close", "button secondary", () => { ui.composer = false; kit.render(); }));
    const body = kit.node("div", "panel-body int-composer");
    body.append(stepper(kit, 0));
    const featured = templates.filter(t => t.featured === true && kit.matches(t)), more = templates.filter(t => t.featured !== true && kit.matches(t));
    const cards = kit.node("div", "cards int-cards");
    featured.forEach(t => cards.append(templateCard(kit, t, plan)));
    body.append(cards.childElementCount ? cards : kit.empty("No matching templates", "Try a different search.", "file"));
    if (more.length) {
      const extra = kit.node("details", "int-more");
      extra.open = ui.moreTemplates;
      extra.addEventListener("toggle", () => { ui.moreTemplates = extra.open; });
      extra.append(kit.node("summary", "", "More templates · " + kit.number(more.length)));
      const moreCards = kit.node("div", "cards int-cards");
      more.forEach(t => moreCards.append(templateCard(kit, t, plan)));
      extra.append(moreCards);
      body.append(extra);
    }
    box.append(body);
    return box;
  }

  // ---- reports: scheduled jobs ---------------------------------------------------------
  function output(kit, latest) {
    if (!latest || typeof latest !== "object") return null;
    const box = kit.node("details", "run-history");
    const stamp = REPORT_FILE.test(text(latest.name)) ? fileTime(latest.name.slice(0, 10) + "T" + latest.name.slice(11, 19).replaceAll("-", ":")) : "";
    box.append(kit.node("summary", "", "Latest saved report" + (stamp ? " · " + stamp : "")));
    if (text(latest.excerpt)) {
      box.append(kit.node("pre", "run-output", latest.excerpt));
      if (latest.truncated) box.append(kit.node("p", "muted", "Excerpt shortened · open it in the reader above"));
    } else box.append(kit.node("p", "muted", latest.excerpt_omitted ? "Excerpt omitted to keep this page small." : "No readable excerpt for this run."));
    return box;
  }

  function reportCard(kit, job, plan) {
    const paused = job.enabled === false || job.state === "paused", failed = Boolean(job.last_error) || /fail|error/i.test(text(job.last_status));
    const card = kit.node("article", "card int-card"), head = kit.node("div", "card-header");
    head.append(kit.iconBox("calendar", failed ? "warning" : "accent"), kit.badge(paused ? "Paused" : job.state === "completed" ? "Completed" : failed ? "Last run failed" : "Scheduled", paused || job.state === "completed" ? "neutral" : failed ? "warning" : "good"));
    card.append(head, kit.node("h2", "", text(job.template_name) || text(job.name) || "Report"));
    if (text(job.account)) card.append(kit.node("p", "", job.account));
    const next = paused ? "Paused" : job.next_run_pending ? "Waiting for the scheduler" : kit.formatRelative(job.next_run_at);
    card.append(kit.definition([["Schedule", kit.describeSchedule(job.schedule)], ["Next run", next], ["Last run", job.last_run_at ? kit.formatRelative(job.last_run_at) : "Never"],
      ["Last result", text(job.last_status) || "—"], ["Saved reports", kit.number(job.outputs ?? 0)], ["Delivery", text(job.deliver_label) || (job.deliver === "local" || !job.deliver ? "Saved in this bot home" : "Configured chat")]]));
    if (text(job.last_error)) card.append(kit.node("p", "notice error", "Last error: " + job.last_error));
    const latest = output(kit, job.latest_output); if (latest) card.append(latest);
    const row = kit.actionRow(plan.row(job));
    if ((job.outputs ?? 0) > 0) row.append(kit.button("Read reports", "text-button", () => { ui.job = text(job.id); kit.render(); }));
    card.append(row);
    return card;
  }

  function renderReports(data, root, kit) {
    const templates = list(data.templates), jobs = list(data.items), saved = list(data.saved), plan = kit.actionPlan(data, [...templates, ...jobs]);
    const summary = data.summary && typeof data.summary === "object" ? data.summary : {};
    const reader = data.reader && typeof data.reader === "object" ? data.reader : null;
    if (ui.composer === null) ui.composer = !jobs.length;
    kit.controlToolbar(kit.number(data.total ?? jobs.length) + " report jobs · " + kit.number(summary.saved ?? saved.length) + " saved reports", plan, root);
    hint(kit, data, root);
    if (data.scheduler_active === false && !kit.isDemo()) root.append(kit.node("p", "int-hint warning", "No running Hermes scheduler was detected: reports run only while the gateway is running."));
    const top = kit.node("div", "int-strip-row");
    top.append(strip(kit, [["Scheduled", summary.scheduled ?? jobs.length, "good"], ["Paused", summary.paused ?? 0, "neutral"],
      ["Saved reports", summary.saved ?? saved.length, "info"], ["Failing", summary.failing ?? 0, (summary.failing ?? 0) > 0 ? "warning" : "neutral"]], "Report status"),
    kit.button("New report", "button primary", () => { ui.composer = !ui.composer; kit.render(); }));
    root.append(top);
    if (ui.composer) root.append(composer(kit, templates, plan));

    const library = kit.panel("Saved reports", "Newest first · each run saves a read-only Markdown report in this bot home");
    const layout = kit.node("div", "panel-body int-library");
    layout.append(savedList(kit, data, saved, reader), readerPane(kit, data, reader));
    library.append(layout);
    root.append(library);

    const scheduled = kit.panel("Report jobs", "Run now, pause or delete · edit instructions on Automations");
    const shown = jobs.filter(job => kit.matches(job));
    if (shown.length) { const cards = kit.node("div", "cards int-cards"); shown.forEach(job => cards.append(reportCard(kit, job, plan))); const body = kit.node("div", "panel-body"); body.append(cards); scheduled.append(body); }
    else scheduled.append(kit.empty(jobs.length ? "No matching reports" : "No reports yet", jobs.length ? "Try a different search." : "Use New report to schedule your first report.", "file"));
    root.append(scheduled);
  }

  C.register("connectors", { title: "Connectors", description: "Connect the assistant to social, messaging and work apps.", icon: "plug", group: "integrations", essential: true, searchable: false, actions: true, render: renderConnectors });
  C.register("reports", { title: "Reports", description: "Read saved reports and schedule new ones from templates.", icon: "file", group: "integrations", essential: true, actions: true,
    commands: [
      { label: "Open latest report", icon: "file", keywords: "reports latest saved read briefing", run: () => { ui.open = null; ui.job = ""; ui.composer = false; showReports(); } },
      { label: "New report from template", icon: "plus", keywords: "report create template schedule", run: () => window.HermesReports.create() },
    ],
    query: () => (ui.open && ui.open.job && ui.open.file ? { job: ui.open.job, file: ui.open.file } : {}), render: renderReports });
})();
