"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const state = { session: null, data: null, view: "mine", preview: null, ticket: null, submissionKey: null, epoch: 0 };
  const terminal = new Set(["completed", "rejected", "cancelled", "expired", "stale"]);
  const titles = {
    create: ["Tạo yêu cầu đổi ca", "Chọn hai ca phù hợp, xem trước rồi gửi đề nghị."],
    pending: ["Chờ tôi xác nhận", "Những đề nghị đang cần phản hồi của bạn."],
    mine: ["Yêu cầu của tôi", "Theo dõi những yêu cầu bạn đã gửi hoặc nhận."],
    history: ["Lịch sử đổi ca", "Xem lại kết quả và các bước xử lý của từng yêu cầu."],
  };
  const actionLabels = { accept: "Đồng ý đổi ca", reject: "Từ chối", cancel: "Hủy yêu cầu", approve: "Duyệt đổi ca", reconcile: "Kiểm tra kết quả cập nhật" };
  const actionQuestions = {
    accept: "Bạn đã kiểm tra ngày, giờ và đồng ý nhận ca như bản xem trước?",
    reject: "Bạn muốn từ chối đề nghị này? Lịch sẽ không thay đổi.",
    cancel: "Bạn muốn hủy yêu cầu này? Lịch sẽ không thay đổi.",
    approve: "Bạn đã kiểm tra hai ca và cho phép cập nhật lịch thử nghiệm?",
    reconcile: "Kiểm tra lại lịch để xác định kết quả lần cập nhật trước. Thao tác này không tự ghi lại ca.",
  };

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function message(id, text, kind = "error") {
    const node = $(id);
    if (!node) return;
    node.textContent = text || "";
    node.className = `message message-${kind}`;
    node.hidden = !text;
  }

  function toast(text) {
    $("toast").textContent = text;
    $("toast").hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => { $("toast").hidden = true; }, 4800);
  }

  function roleLabel(role) { return role === "manager" || role === "admin" ? "Quản lý" : "Thành viên"; }
  function initials(name) { return (name || "LC").trim().split(/\s+/).slice(-2).map((part) => part[0]).join("").toLocaleUpperCase("vi"); }
  function stamp(value, options) {
    const date = new Date(value);
    if (!value || Number.isNaN(date.getTime())) return "Chưa xác định thời gian";
    return new Intl.DateTimeFormat("vi-VN", { timeZone: "Asia/Ho_Chi_Minh", ...options }).format(date);
  }
  function dateText(value) { return stamp(value, { day: "2-digit", month: "2-digit", year: "numeric" }); }
  function timeText(value) { return stamp(value, { hour: "2-digit", minute: "2-digit", hour12: false }); }
  function dateTime(value) { return stamp(value, { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }); }
  function schedule(a) {
    if (!a) return "Chưa có thông tin ca";
    const endDate = dateText(a.end) === dateText(a.start) ? "" : `${dateText(a.end)} `;
    return `${dateText(a.start)} · ${timeText(a.start)}–${endDate}${timeText(a.end)}`;
  }
  function location(a) { return [a.course, a.room && `Phòng ${a.room}`, a.period].filter(Boolean).join(" · ") || "Chưa có thông tin lớp, phòng"; }
  function pendingForMe(ticket) { return (ticket.actions || []).some((a) => ["accept", "approve", "reconcile"].includes(a)); }
  function ownTicket(ticket) {
    const user = state.session?.user;
    return user?.role === "manager" || ticket.requester_id === user?.id || ticket.recipient_member_id === user?.member_id;
  }
  function visibleTickets() {
    const tickets = state.data?.tickets || [];
    const filtered = tickets.filter((ticket) => state.view === "pending" ? pendingForMe(ticket) : state.view === "history" ? terminal.has(ticket.status) : ownTicket(ticket));
    const search = $("ticket-search").value.trim().toLocaleLowerCase("vi");
    return filtered.filter((ticket) => !search || [ticket.id, ticket.a?.member_name, ticket.b?.member_name, ticket.a?.course, ticket.b?.course].join(" ").toLocaleLowerCase("vi").includes(search))
      .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
  }

  class APIError extends Error {
    constructor(messageText, status) { super(messageText); this.status = status; }
  }
  async function api(path, body) {
    let response;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 30000);
    try {
      response = await fetch(path, {
        method: body === undefined ? "GET" : "POST",
        credentials: "same-origin",
        cache: "no-store",
        signal: controller.signal,
        headers: body === undefined ? { Accept: "application/json" } : { Accept: "application/json", "Content-Type": "application/json", "X-CSRF-Token": state.session?.csrf || "" },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (_) {
      throw new APIError("Chưa kết nối được máy chủ. Kiểm tra mạng rồi tải lại. Nếu bạn vừa gửi yêu cầu, hãy xem danh sách trước khi gửi lại.", 0);
    } finally { clearTimeout(timeout); }
    let data;
    try { data = await response.json(); }
    catch (_) { throw new APIError("Máy chủ trả dữ liệu chưa hợp lệ. Vui lòng thử tải lại.", response.status); }
    if (!response.ok) {
      if (response.status === 401 && path !== "/api/login") {
        state.epoch++;
        state.session = null;
        resetPrivateView();
        queueMicrotask(loadSession);
      }
      throw new APIError(data.error || "Chưa thực hiện được thao tác. Vui lòng tải lại để kiểm tra.", response.status);
    }
    return data;
  }

  async function busy(button, task, text) {
    if (button.disabled) return;
    const previous = button.textContent;
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    if (text) button.textContent = text;
    try { return await task(); }
    finally { button.disabled = false; button.removeAttribute("aria-busy"); button.textContent = previous; }
  }

  function resetPrivateView() {
    state.data = null;
    state.ticket = null;
    state.preview = null;
    state.submissionKey = null;
    $("ticket-list").replaceChildren();
    $("detail-content").replaceChildren();
    $("detail-actions").replaceChildren();
    $("preview-comparison").replaceChildren();
    $("swap-reason").value = "";
    $("ticket-search").value = "";
    $("pending-count").hidden = true;
    for (const id of ["metric-active", "metric-pending", "metric-completed"]) $(id).textContent = "0";
    if (!state.session?.user) {
      $("dashboard").hidden = true;
      $("account-button").hidden = true;
      $("sidebar-name").textContent = "LabCoach Tickets";
      $("sidebar-role").textContent = "Đăng nhập để bắt đầu";
      $("sidebar-avatar").textContent = "LC";
    }
    $("own-assignment").replaceChildren(new Option("Chọn ca của bạn", ""));
    $("other-member").replaceChildren(new Option("Chọn thành viên", ""));
    $("other-assignment").replaceChildren(new Option("Chọn thành viên trước", ""));
    $("other-assignment").disabled = true;
    $("own-assignment-info").replaceChildren();
    $("other-assignment-info").replaceChildren();
    $("own-assignment-info").hidden = true;
    $("other-assignment-info").hidden = true;
    for (const id of ["preview-dialog", "detail-dialog", "account-dialog"]) $(id).close();
    for (const id of ["form-message", "preview-message", "global-message"]) message(id, "");
  }

  function renderSession() {
    const s = state.session;
    const signedIn = Boolean(s?.user);
    const demo = s?.mode === "demo";
    $("mode-badge").textContent = demo ? "BẢN DEMO" : "SHEETS THỬ NGHIỆM";
    $("environment-note").hidden = false;
    $("environment-note").textContent = demo ? "Bản demo cục bộ · Dữ liệu mẫu, không cập nhật Google Sheets. Chuyển tài khoản để thử các bước xác nhận." : "Môi trường thử nghiệm · Chỉ sử dụng bảng lịch thử đã được cấu hình. Thời gian hiển thị theo giờ Việt Nam (UTC+7).";
    $("login-section").hidden = signedIn;
    $("dashboard").hidden = !signedIn;
    $("loading-state").hidden = true;
    $("account-button").hidden = !signedIn;
    $("refresh-button").hidden = !signedIn;
    $("sidebar-name").textContent = s?.user?.name || "LabCoach Tickets";
    $("sidebar-role").textContent = signedIn ? roleLabel(s.user.role) : "Đăng nhập để bắt đầu";
    $("sidebar-avatar").textContent = signedIn ? initials(s.user.name) : "LC";
    $("credentials-form").hidden = demo;
    $("demo-accounts").hidden = !demo;
    $("login-card-title").textContent = demo ? "Chọn vai trò để trải nghiệm" : "Đăng nhập không gian nhóm";
    $("login-description").textContent = demo ? "Tạo yêu cầu với một thành viên, sau đó chuyển sang người nhận để xác nhận." : "Dùng tài khoản do quản trị viên cấp và liên kết với tên của bạn trong lịch.";
    $("demo-accounts").replaceChildren();
    if (demo) for (const account of s.demo_accounts || []) {
      const button = el("button", "demo-account");
      button.type = "button";
      button.append(el("span", "small-avatar", initials(account.name)));
      const label = el("span");
      label.append(el("strong", "", account.name), el("small", "", roleLabel(account.role)));
      button.append(label, el("span", "arrow", "→"));
      button.addEventListener("click", () => login(button, { account_id: account.id }));
      $("demo-accounts").append(button);
    }
    for (const button of document.querySelectorAll(".nav-item")) button.disabled = !signedIn;
  }

  async function loadSession() {
    const epoch = ++state.epoch;
    message("global-message", "");
    try {
      const session = await api("/api/session");
      if (epoch !== state.epoch) return;
      state.session = session;
      renderSession();
      if (session.user) await refreshData(true);
      else resetPrivateView();
    } catch (error) {
      if (epoch !== state.epoch) return;
      $("loading-state").hidden = true;
      $("refresh-button").hidden = false;
      message("global-message", error.message);
    }
  }

  async function login(button, credentials) {
    const accountButtons = Array.from($("demo-accounts").querySelectorAll("button"));
    await busy(button, async () => {
      message("login-message", "");
      accountButtons.forEach((b) => { b.disabled = true; });
      try {
        await api("/api/login", credentials);
        $("password").value = "";
        resetPrivateView();
        state.view = "mine";
        await loadSession();
      } catch (error) {
        message("login-message", error.message);
        if (error.status === 403) {
          try { state.session = await api("/api/session"); } catch (_) { /* Keep the actionable login error visible. */ }
        }
      } finally { accountButtons.forEach((b) => { b.disabled = false; }); }
    });
  }

  async function refreshData(rebuildForm = false) {
    const epoch = state.epoch;
    const data = await api("/api/bootstrap");
    if (epoch !== state.epoch || !state.session?.user) return;
    if (data.user.id !== state.session.user.id) {
      const session = await api("/api/session");
      if (epoch !== state.epoch) return;
      resetPrivateView();
      state.session = session;
      state.view = "mine";
      renderSession();
      if (!session.user || session.user.id !== data.user.id) return;
      rebuildForm = true;
    } else state.session.user = data.user;
    state.data = data;
    message("global-message", data.source_error || "");
    $("approval-help").textContent = data.requires_manager ? "Sau khi hai bên đồng ý, quản lý duyệt trước khi cập nhật lịch." : "Lịch được kiểm tra và cập nhật sau khi hai bên đồng ý.";
    renderMetrics();
    if (rebuildForm) fillAssignments();
    renderView();
  }

  function renderMetrics() {
    const tickets = state.data?.tickets || [];
    const count = tickets.filter(pendingForMe).length;
    $("pending-count").textContent = count;
    $("pending-count").hidden = count === 0;
    $("metric-active").textContent = tickets.filter((t) => !terminal.has(t.status)).length;
    $("metric-pending").textContent = count;
    $("metric-completed").textContent = tickets.filter((t) => t.status === "completed").length;
  }

  function setView(view) {
    if (!state.session?.user || !titles[view]) return;
    state.view = view;
    $("ticket-search").value = "";
    message("form-message", "");
    if (view === "create") fillAssignments();
    renderView();
  }

  function renderView() {
    if (!state.data) return;
    const [title, description] = titles[state.view];
    $("page-title").textContent = title;
    $("page-description").textContent = state.view === "mine" && state.session.user.role === "manager" ? "Theo dõi các yêu cầu trong nhóm bạn quản lý." : description;
    $("create-panel").hidden = state.view !== "create";
    $("tickets-panel").hidden = state.view === "create";
    $("new-ticket-button").hidden = state.view === "create";
    for (const button of document.querySelectorAll(".nav-item")) {
      const active = button.dataset.view === state.view;
      button.classList.toggle("is-active", active);
      if (active) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    }
    if (state.view !== "create") renderTickets();
  }

  function renderTickets() {
    const tickets = visibleTickets();
    $("ticket-list").replaceChildren();
    $("list-count").textContent = tickets.length;
    $("empty-state").hidden = tickets.length > 0;
    const searching = Boolean($("ticket-search").value.trim());
    const empty = searching ? ["Chưa tìm thấy yêu cầu phù hợp", "Thử tìm bằng mã yêu cầu, tên thành viên hoặc tên lớp."] : state.view === "pending" ? ["Bạn đã xử lý hết đề nghị hiện có", "Yêu cầu cần bạn xác nhận sẽ xuất hiện tại đây. Dùng nút tải lại để kiểm tra cập nhật."] : state.view === "history" ? ["Lịch sử đang trống", "Yêu cầu hoàn tất, từ chối, hủy, hết hạn hoặc cần tạo lại sẽ được lưu ở đây."] : ["Chưa có yêu cầu nào", "Khi cần đổi ca, bạn có thể bắt đầu bằng một đề nghị."];
    $("empty-title").textContent = empty[0];
    $("empty-description").textContent = empty[1];
    $("empty-create-button").hidden = searching || state.view !== "mine";
    for (const ticket of tickets) {
      const row = el("article", "ticket-row");
      const identity = el("div", "ticket-identity");
      const label = el("div");
      label.append(el("span", "ticket-code", ticket.id), el("div", "ticket-name", `${ticket.a?.member_name || "Thành viên"} ↔ ${ticket.b?.member_name || "Thành viên"}`));
      identity.append(el("span", "ticket-icon", "↔"), label);
      const dates = el("div", "ticket-schedule");
      dates.append(el("div", "", `${dateText(ticket.a?.start)} ↔ ${dateText(ticket.b?.start)}`), el("small", "", [ticket.a?.course, ticket.b?.course].filter(Boolean).join(" / ")));
      const status = el("div", "ticket-state");
      status.append(statusPill(ticket), el("small", "", `Tạo ${dateText(ticket.created_at)}`));
      const open = el("button", "ticket-open", "→");
      open.type = "button";
      open.setAttribute("aria-label", `Xem yêu cầu ${ticket.id}`);
      open.addEventListener("click", () => busy(open, () => openTicket(ticket.id)));
      row.append(identity, dates, status, open);
      $("ticket-list").append(row);
    }
  }

  function statusPill(ticket) {
    const known = ["pending_peer", "pending_manager", "applying", "uncertain", "completed", "rejected", "cancelled", "expired", "stale"];
    const safeStatus = known.includes(ticket.status) ? ticket.status : "pending_peer";
    return el("span", `status-pill status-${safeStatus}`, ticket.status_label || "Đang xử lý");
  }

  function fillAssignments() {
    const all = (state.data?.assignments || []).filter((a) => new Date(a.start).getTime() > Date.now());
    const own = all.filter((a) => a.member_id === state.session.user.member_id);
    $("own-assignment").replaceChildren(new Option("Chọn ca của bạn", ""));
    for (const a of own) $("own-assignment").append(new Option(`${schedule(a)} · ${a.course || "Chưa có lớp"}`, a.id));
    $("other-member").replaceChildren(new Option("Chọn thành viên", ""));
    const availableIds = new Set(all.filter((a) => a.member_id !== state.session.user.member_id).map((a) => a.member_id));
    for (const member of state.data?.members || []) {
      if (availableIds.has(member.id)) $("other-member").append(new Option(member.name, member.id));
    }
    $("other-assignment").replaceChildren(new Option("Chọn thành viên trước", ""));
    $("other-assignment").disabled = true;
    $("own-assignment-info").hidden = true;
    $("other-assignment-info").hidden = true;
    $("preview-button").disabled = own.length === 0;
    if (!own.length) message("form-message", "Chưa có ca nào được phép đổi cho tài khoản này. Nếu bạn là thành viên, hãy nhờ quản trị viên kiểm tra liên kết tài khoản và lịch thử nghiệm.");
  }

  function showAssignment(selectId, infoId) {
    const a = state.data?.assignments.find((item) => item.id === $(selectId).value);
    const info = $(infoId);
    info.replaceChildren();
    info.hidden = !a;
    if (a) info.append(el("strong", "", schedule(a)), el("span", "", location(a)));
  }

  function personCard(name, assignment) {
    const box = el("div", "person-swap");
    box.append(el("strong", "", name), el("p", "", schedule(assignment)), el("p", "", location(assignment)));
    return box;
  }

  function comparison(a, b) {
    const root = el("div", "comparison");
    if (!a || !b) { root.append(el("p", "muted", "Chưa tải đủ thông tin hai ca.")); return root; }
    const before = el("div", "comparison-card");
    before.append(el("p", "comparison-heading", "Trước khi đổi"), personCard(a.member_name, a), personCard(b.member_name, b));
    const after = el("div", "comparison-card after");
    after.append(el("p", "comparison-heading", "Sau khi đổi"), personCard(a.member_name, b), personCard(b.member_name, a));
    const arrow = el("span", "comparison-arrow", "→");
    arrow.setAttribute("aria-hidden", "true");
    root.append(before, arrow, after);
    return root;
  }

  async function preview(event) {
    event.preventDefault();
    await busy($("preview-button"), async () => {
      message("form-message", "");
      try {
        state.preview = await api("/api/preview", { assignment_a: $("own-assignment").value, assignment_b: $("other-assignment").value });
        state.submissionKey = crypto.randomUUID();
        $("preview-comparison").replaceChildren(comparison(state.preview.a, state.preview.b));
        $("preview-approval").textContent = state.preview.requires_manager ? "Người nhận cần đồng ý, sau đó quản lý duyệt. Lịch chưa được cập nhật khi bạn gửi đề nghị." : "Người nhận cần đồng ý. Sau đó hệ thống kiểm tra lịch trước khi cập nhật.";
        $("preview-expiry").textContent = `Bản xem trước có hiệu lực đến ${dateTime(state.preview.expires_at)}. Thời gian theo giờ Việt Nam.`;
        $("swap-reason").value = "";
        message("preview-message", "");
        $("preview-dialog").showModal();
      } catch (error) { message("form-message", error.message); }
    }, "Đang kiểm tra…");
  }

  async function createTicket() {
    if (!state.preview || !state.submissionKey) return;
    await busy($("submit-ticket-button"), async () => {
      message("preview-message", "");
      const closeButtons = Array.from($("preview-dialog").querySelectorAll("[data-close]"));
      closeButtons.forEach((button) => { button.disabled = true; });
      $("preview-dialog").dataset.busy = "true";
      try {
        const ticket = await api("/api/tickets", { preview_token: state.preview.preview_token, reason: $("swap-reason").value.trim(), idempotency_key: state.submissionKey });
        $("preview-dialog").close();
        state.preview = null;
        state.submissionKey = null;
        $("swap-reason").value = "";
        setView("mine");
        upsertTicket(ticket);
        showTicket(ticket);
        toast("Đã gửi đề nghị. Bạn có thể theo dõi phản hồi trong yêu cầu này.");
        try { await refreshData(); } catch (error) { message("global-message", `Đã tạo yêu cầu ${ticket.id}. ${error.message}`); }
      } catch (error) { message("preview-message", error.message); }
      finally { closeButtons.forEach((button) => { button.disabled = false; }); delete $("preview-dialog").dataset.busy; }
    }, "Đang gửi…");
  }

  function upsertTicket(ticket) {
    if (!state.data) return;
    const index = state.data.tickets.findIndex((t) => t.id === ticket.id);
    if (index < 0) state.data.tickets.unshift(ticket);
    else state.data.tickets[index] = ticket;
    renderMetrics();
    renderView();
  }

  async function openTicket(id) {
    message("global-message", "");
    try { const ticket = await api(`/api/tickets/${encodeURIComponent(id)}`); upsertTicket(ticket); showTicket(ticket); }
    catch (error) { message("global-message", error.message); }
  }

  function showTicket(ticket) {
    state.ticket = ticket;
    $("detail-code").textContent = `YÊU CẦU ${ticket.id}`;
    $("detail-content").replaceChildren();
    $("detail-actions").replaceChildren();
    const meta = el("div", "detail-meta");
    meta.append(statusPill(ticket), el("small", "", `Tạo lúc ${dateTime(ticket.created_at)}`));
    $("detail-content").append(meta, comparison(ticket.a, ticket.b));
    if (ticket.message) $("detail-content").append(el("p", "callout", ticket.message));
    if (ticket.status === "completed") $("detail-content").append(el("p", "callout", state.session.mode === "demo" ? "Đã cập nhật lịch mẫu của bản demo. Google Sheets không thay đổi." : "Kết quả được xác nhận trên bảng Google Sheets thử nghiệm."));
    if (ticket.reason) $("detail-content").append(el("p", "detail-reason", `Lý do: ${ticket.reason}`));
    if (!terminal.has(ticket.status)) $("detail-content").append(el("p", "field-help", `Hạn phản hồi: ${dateTime(ticket.expires_at)} · Giờ Việt Nam (UTC+7).`));
    $("detail-content").append(el("h3", "section-label", "Lịch sử xử lý"));
    const timeline = el("ol", "timeline");
    for (const event of ticket.events || []) {
      const item = el("li");
      item.append(el("strong", "", event.text), el("small", "", [event.actor, dateTime(event.at)].filter(Boolean).join(" · ")));
      timeline.append(item);
    }
    if (!timeline.children.length) timeline.append(el("li", "muted", "Chưa có thao tác được ghi nhận."));
    $("detail-content").append(timeline);
    const actionMessage = el("div", "message message-error");
    actionMessage.id = "detail-message";
    actionMessage.hidden = true;
    actionMessage.setAttribute("role", "alert");
    $("detail-content").append(actionMessage);
    const close = el("button", "button button-outline", "Đóng");
    close.type = "button";
    close.addEventListener("click", () => $("detail-dialog").close());
    $("detail-actions").append(close);
    for (const action of ticket.actions || []) {
      if (!actionLabels[action]) continue;
      const isNegative = action === "reject" || action === "cancel";
      const button = el("button", `button ${isNegative ? "button-danger" : "button-primary"}`, actionLabels[action]);
      button.type = "button";
      button.addEventListener("click", () => confirmAction(action));
      $("detail-actions").append(button);
    }
    if (!$("detail-dialog").open) $("detail-dialog").showModal();
  }

  function confirmAction(action) {
    const existing = document.getElementById("action-confirmation");
    if (existing) existing.remove();
    message("detail-message", "");
    const box = el("div", "detail-confirm");
    box.id = "action-confirmation";
    box.append(el("p", "", actionQuestions[action]));
    const buttons = el("div", "confirm-buttons");
    const back = el("button", "button button-small button-outline", "Quay lại");
    back.type = "button";
    back.addEventListener("click", () => box.remove());
    const confirm = el("button", "button button-small button-primary", "Xác nhận");
    confirm.type = "button";
    confirm.addEventListener("click", () => performAction(confirm, action));
    buttons.append(back, confirm);
    box.append(buttons);
    $("detail-content").append(box);
    box.scrollIntoView({ block: "nearest", behavior: "smooth" });
    confirm.focus({ preventScroll: true });
  }

  async function performAction(button, action) {
    const ticket = state.ticket;
    if (!ticket || !(ticket.actions || []).includes(action)) return;
    await busy(button, async () => {
      const buttons = Array.from($("detail-dialog").querySelectorAll("button"));
      buttons.forEach((item) => { item.disabled = true; });
      $("detail-dialog").dataset.busy = "true";
      try {
        const updated = await api(`/api/tickets/${encodeURIComponent(ticket.id)}/${action}`, { version: ticket.version });
        upsertTicket(updated);
        showTicket(updated);
        toast("Đã ghi nhận. Trạng thái mới được hiển thị trong yêu cầu.");
        try { await refreshData(); } catch (error) { message("global-message", error.message); }
      } catch (error) {
        if (error.status === 409 || error.status === 0 || error.status === 503) {
          try { const latest = await api(`/api/tickets/${encodeURIComponent(ticket.id)}`); upsertTicket(latest); showTicket(latest); } catch (_) { /* Preserve the original error; the user can reload. */ }
        }
        message("detail-message", error.message);
      } finally { buttons.forEach((item) => { item.disabled = false; }); delete $("detail-dialog").dataset.busy; }
    }, "Đang xử lý…");
  }

  document.querySelectorAll(".nav-item").forEach((button) => button.addEventListener("click", () => setView(button.dataset.view)));
  document.querySelectorAll("[data-close]").forEach((button) => button.addEventListener("click", () => { if (!$(button.dataset.close).dataset.busy) $(button.dataset.close).close(); }));
  document.querySelectorAll("dialog").forEach((dialog) => dialog.addEventListener("cancel", (event) => { if (dialog.dataset.busy) event.preventDefault(); }));
  $("new-ticket-button").addEventListener("click", () => setView("create"));
  $("empty-create-button").addEventListener("click", () => setView("create"));
  $("ticket-search").addEventListener("input", renderTickets);
  $("swap-form").addEventListener("submit", preview);
  $("submit-ticket-button").addEventListener("click", createTicket);
  $("own-assignment").addEventListener("change", () => showAssignment("own-assignment", "own-assignment-info"));
  $("other-assignment").addEventListener("change", () => showAssignment("other-assignment", "other-assignment-info"));
  $("other-member").addEventListener("change", () => {
    const memberId = $("other-member").value;
    const assignments = (state.data?.assignments || []).filter((a) => a.member_id === memberId && new Date(a.start).getTime() > Date.now());
    $("other-assignment").replaceChildren(new Option(memberId ? "Chọn ca muốn nhận" : "Chọn thành viên trước", ""));
    $("other-assignment").disabled = !memberId;
    for (const a of assignments) $("other-assignment").append(new Option(`${schedule(a)} · ${a.course || "Chưa có lớp"}`, a.id));
    $("other-assignment-info").hidden = true;
  });
  $("credentials-form").addEventListener("submit", (event) => { event.preventDefault(); login(event.submitter, { username: $("username").value.trim(), password: $("password").value }); });
  $("refresh-button").addEventListener("click", () => busy($("refresh-button"), async () => {
    try {
      if (!state.session?.user) await loadSession();
      else {
        message("global-message", "");
        await loadSession();
        toast("Đã tải dữ liệu mới nhất.");
      }
    } catch (error) {
      if (error.status === 401) { resetPrivateView(); await loadSession(); }
      message("global-message", error.message);
    }
  }));
  $("account-button").addEventListener("click", () => {
    $("account-summary").textContent = `${state.session.user.name} · ${roleLabel(state.session.user.role)}`;
    $("account-demo-note").textContent = state.session.mode === "demo" ? "Đăng xuất rồi chọn tài khoản khác để thử vai trò người nhận hoặc quản lý. Các yêu cầu đã tạo vẫn được lưu." : "Tài khoản được quản trị viên liên kết với thành viên trong lịch.";
    $("account-dialog").showModal();
  });
  $("logout-button").addEventListener("click", () => busy($("logout-button"), async () => {
    try {
      await api("/api/logout", {});
      state.epoch++;
      state.session = null;
      resetPrivateView();
      await loadSession();
    } catch (error) { $("account-dialog").close(); message("global-message", error.message); }
  }, "Đang đăng xuất…"));

  loadSession();
})();
