"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const state = {user:null,csrf:"",view:"members",member:null,tab:"info",memberPage:1,historyPage:1,memoryPage:1,issuePage:1,ticketPage:1,accountPage:1,issue:null,ticket:null,ticketBusy:false,ticketRefreshRequired:null,editingAccount:null,operators:[],sessionKey:"",editingMemory:null,linkToken:null,request:0};
  let pendingConfirmation=null;
  function finishConfirmation(accepted=false) {
    const resolve=pendingConfirmation;
    pendingConfirmation=null;
    if($("confirmation-dialog").open)$("confirmation-dialog").close();
    if(resolve)resolve(accepted===true);
  }
  function confirmAction(title,message,label="Xác nhận",danger=false) {
    finishConfirmation(false);
    $("confirmation-title").textContent=title;
    $("confirmation-description").textContent=message;
    $("confirmation-accept").textContent=label;
    $("confirmation-accept").className=`button ${danger?"danger":"primary"}`;
    return new Promise(resolve=>{
      pendingConfirmation=resolve;
      $("confirmation-dialog").showModal();
      $("confirmation-cancel").focus();
    });
  }
  const loads=new WeakMap();
  function beginLoad(target) { const token={user:state.user};loads.set(target,token);loading(target);return token; }
  function currentLoad(target,token) { return token.user===state.user && state.user!==null && loads.get(target)===token; }
  const viewInfo = {
    issues:["HỖ TRỢ CÙNG ĐỘI NGŨ","Issue","Tiếp nhận, phân công và phản hồi các yêu cầu của thành viên."],
    tickets:["ĐỔI CA CÓ XÁC NHẬN","Duyệt đổi ca","Xem hai ca, kiểm tra lý do và quyết định yêu cầu đổi lịch."],
    accounts:["PHÂN QUYỀN RÕ RÀNG","Quản lý Admin","Quản lý tài khoản và theo dõi công việc của đội ngũ hỗ trợ."],
    members:["QUẢN LÝ ĐỘI NGŨ","Thành viên","Theo dõi quyền sử dụng và liên kết lịch của từng thành viên."],
    conversations:["HIỂU TỪNG CUỘC TRAO ĐỔI","Hội thoại","Xem những gì thành viên đã nói và cách bot phản hồi."],
    memories:["GHI NHỚ CÓ KIỂM SOÁT","Thông tin ghi nhớ","Quản lý những thông tin hữu ích cho các lần trao đổi sau."]
  };
  const statusNames = {approved:"Đã duyệt",pending:"Chờ duyệt",rejected:"Từ chối",revoked:"Thu hồi",locked:"Đã khóa",sent:"Đã gửi",received:"Đã nhận",failed:"Gửi lỗi",uncertain:"Chưa rõ kết quả",linked:"Đã liên kết",missing:"Thiếu liên kết",conflict:"Có xung đột",disabled:"Chưa bật"};
  const actionNames = {locked:"Khóa thành viên",unlocked:"Mở quyền thành viên",tickets_link:"Cập nhật liên kết Tickets",context_rebuilt:"Làm mới ngữ cảnh",memory_created:"Tạo ghi nhớ",memory_updated:"Sửa ghi nhớ",memory_stopped:"Ngừng dùng ghi nhớ",member_lock:"Khóa thành viên",member_unlock:"Mở quyền thành viên",member_link:"Cập nhật liên kết",lock:"Khóa thành viên",unlock:"Mở quyền thành viên",link_confirm:"Cập nhật liên kết",context_reset:"Xóa ngữ cảnh",context_rebuild:"Làm mới ngữ cảnh",memory_create:"Tạo ghi nhớ",memory_update:"Sửa ghi nhớ",memory_stop:"Ngừng dùng ghi nhớ",memory_save:"Lưu ghi nhớ"};
  const isGroup = (value) => String(value || "").toUpperCase() === "GROUP";
  const scopeNames = {personal:"Cá nhân",session:"Cuộc trò chuyện",group:"Nhóm"};
  const fieldNames = {member_id:"Mã thành viên",user_id:"ID người dùng",zalo_user_id:"ID Zalo",chat_id:"ID cuộc trò chuyện",private_chat_id:"ID chat riêng",group_id:"ID nhóm",account_id:"Mã tài khoản",id:"Mã",name:"Tên",role:"Vai trò",status:"Trạng thái",locked:"Đã khóa",revision:"Phiên bản",content:"Nội dung",scope:"Phạm vi",active:"Đang sử dụng",session_key:"Mã phiên",expires:"Hết hạn",source_message_id:"Mã tin nguồn",create_account:"Tạo liên kết mới",phone_masked:"Điện thoại đã che"};
  function el(tag, className, text) { const node=document.createElement(tag); if(className) node.className=className; if(text!==undefined && text!==null) node.textContent=String(text); return node; }
  function button(text, className, handler) { const b=el("button",className,text); b.type="button"; if(handler)b.addEventListener("click",handler); return b; }
  function can(permission) { return Boolean(state.user && Array.isArray(state.user.permissions) && state.user.permissions.includes(permission) && (permission!=="memories" || state.user.permissions.includes("conversations"))); }
  function isFaculty() { return state.user?.role==="faculty"; }
  function canView(view) { return view==="accounts"?isFaculty():view==="issues"?isFaculty()||can("issues"):can(view); }
  function firstView() { return ["issues","tickets","members","conversations","memories","accounts"].find(canView)||"members"; }
  function textValue(value, fallback="—") { return value===undefined || value===null || value==="" ? fallback : String(value); }
  function date(value) { if(value===undefined || value===null || value==="")return "—"; const v=typeof value==="number" ? new Date(value<1e12?value*1000:value) : new Date(value); return Number.isNaN(v.getTime())?"—":new Intl.DateTimeFormat("vi-VN",{dateStyle:"short",timeStyle:"short",timeZone:"Asia/Ho_Chi_Minh"}).format(v); }
  function isoDay(value) { if(!value)return ""; const d=typeof value==="number"?new Date(value*1000):new Date(value); if(Number.isNaN(d.getTime()))return ""; return new Intl.DateTimeFormat("en-CA",{year:"numeric",month:"2-digit",day:"2-digit",timeZone:"Asia/Ho_Chi_Minh"}).format(d); }
  function initials(name) { return String(name||"TV").trim().split(/\s+/).slice(-2).map(x=>x[0]||"").join("").toLocaleUpperCase("vi-VN"); }
  function tag(text,kind="") { return el("span",`tag ${kind}`.trim(),text); }
  function statusTag(status) { return tag(statusNames[status]||textValue(status),["rejected","revoked","locked","failed","conflict"].includes(status)?"danger":["pending","missing","uncertain"].includes(status)?"warning":["disabled"].includes(status)?"subtle":""); }
  function messageOf(error) { return error instanceof Error?error.message:"Không thể hoàn tất thao tác. Vui lòng thử lại."; }
  function notice(target,error) { target.textContent=messageOf(error); target.hidden=false; }
  function clearError() { $("page-error").hidden=true; $("page-error").textContent=""; }
  let toastTimer;
  function toast(text) { $("toast").textContent=text; $("toast").hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>{$("toast").hidden=true;},4500); }
  function loading(target,text="Đang tải dữ liệu…") { target.replaceChildren(el("div","loading-state",text)); }
  function empty(target,title,description="",symbol="◇") { const box=el("div","empty-state"); box.append(el("span","empty-symbol",symbol),el("strong","",title)); if(description)box.append(el("p","",description)); target.replaceChildren(box); }
  function noAccess(target) { empty(target,"Bạn chưa có quyền xem phần này","Quyền đọc hội thoại riêng được cấp riêng với quyền quản lý thành viên.","⊘"); }
  function panel() { return el("div","panel"); }
  async function api(path,body) {
    const options={method:body===undefined?"GET":"POST",credentials:"same-origin",cache:"no-store",headers:{Accept:"application/json"}};
    if(body!==undefined){options.headers["Content-Type"]="application/json";options.headers["X-CSRF-Token"]=state.csrf;options.body=JSON.stringify(body);}
    let response;
    try{response=await fetch(path,options);}catch(_error){throw new Error("Không kết nối được máy chủ. Kiểm tra mạng rồi thử lại.");}
    let data;
    try{data=await response.json();}catch(_error){throw new Error("Máy chủ trả về dữ liệu không hợp lệ. Vui lòng thử lại.");}
    if(data.csrf)state.csrf=data.csrf;
    if(data.mode)$("mode-banner").hidden=data.mode!=="demo";
    if(!response.ok){if(response.status===401 && path!=="/api/login")showLogin();const error=new Error(typeof data.error==="string"?data.error:"Không thể thực hiện thao tác này.");error.status=response.status;throw error;}
    return data;
  }
  function query(path,values) { const params=new URLSearchParams(); Object.entries(values).forEach(([key,value])=>{if(value!==undefined && value!==null && value!=="")params.set(key,String(value));});return `${path}?${params}`; }
  async function busy(node,task) { const controls=node.matches("button")?[node]:Array.from(node.querySelectorAll("button[type=submit]"));controls.forEach(b=>{b.disabled=true;});try{return await task();}finally{controls.forEach(b=>{b.disabled=false;});} }
  function paging(target,data,callback) {
    target.replaceChildren(); target.classList.add("pagination"); const page=Number(data.page)||1;const limit=Number(data.page_size??data.limit)||20;const total=Number(data.total)||0;const pages=Math.max(1,Math.ceil(total/limit));
    target.append(el("span","",total?`${(page-1)*limit+1}–${Math.min(page*limit,total)} / ${total} kết quả`:"0 kết quả"));
    const controls=el("div","pagination-controls");const previous=button("←","",()=>callback(page-1));previous.disabled=page<=1;previous.setAttribute("aria-label","Trang trước");const next=button("→","",()=>callback(page+1));next.disabled=page>=pages;next.setAttribute("aria-label","Trang sau");controls.append(previous,el("span","",`Trang ${page} / ${pages}`),next);target.append(controls);
  }
  function pagedShell(target,data,render,callback) { const stack=el("div","stack"); if(data.items && data.items.length)data.items.forEach(item=>stack.append(render(item)));else empty(stack,"Chưa có dữ liệu phù hợp","Thử đổi bộ lọc hoặc quay lại sau.");const pager=el("div","pagination");paging(pager,data,callback);target.replaceChildren(stack,pager); }
  function showLogin() { finishConfirmation(false);state.user=null;state.member=null;state.issue=null;state.ticket=null;state.ticketBusy=false;state.ticketRefreshRequired=null;state.editingAccount=null;state.operators=[];state.sessionKey="";state.editingMemory=null;state.linkToken=null;state.request++;document.querySelectorAll("dialog[open]").forEach(d=>d.close());["members-content","member-profile","member-tab-content","conversations-content","memories-content","source-content","link-preview","memory-source","issues-content","issue-content","tickets-content","ticket-content","accounts-content","account-audit"].forEach(id=>$(id).replaceChildren());$("tickets-nav-count").hidden=true;$("memory-form").reset();$("account-form").reset();$("app-view").hidden=true;$("login-view").hidden=false;$("boot-state").hidden=true;$("password").value="";$("username").focus(); }
  function showApp(user) { state.user=user;$("admin-name").textContent=user.display_name||user.username||"Admin";$("admin-role").textContent=isFaculty()?"Giảng viên":"Admin";$("login-view").hidden=true;$("app-view").hidden=false;$("boot-state").hidden=true;document.querySelectorAll(".nav-item").forEach(node=>{node.hidden=!canView(node.dataset.view);});navigate(firstView());if(firstView()!=="tickets")refreshTicketCount(); }
  async function navigate(view) { state.view=view;state.member=null;state.issue=null;state.ticket=null;state.sessionKey="";state.request++;clearError();["members","member","conversations","memories","issues","issue","tickets","ticket","accounts"].forEach(name=>{$(`${name}-view`).hidden=name!==view;});document.querySelectorAll(".nav-item").forEach(node=>node.classList.toggle("active",node.dataset.view===view));const info=viewInfo[view];$("page-eyebrow").textContent=info[0];$("page-title").textContent=info[1];$("page-description").textContent=info[2];$("breadcrumb-current").textContent=info[1];await refresh(); }
  async function refresh() { clearError();try{if(state.member)await loadMember(state.member.member_id,true);else if(state.issue)await loadIssue(state.issue.id);else if(state.ticket)await loadTicket(state.ticket.id);else if(state.view==="tickets")await loadTickets(state.ticketPage);else if(state.view==="issues")await loadIssues(state.issuePage);else if(state.view==="accounts")await loadAccounts(state.accountPage);else if(state.view==="members")await loadMembers(state.memberPage);else if(state.view==="conversations")await loadHistory($("conversations-content"),state.historyPage,historyFilters());else await loadMemories($("memories-content"),state.memoryPage,$("memories-member").value.trim());}catch(error){notice($("page-error"),error);} }

  async function loadMembers(page=1) {
    const target=$("members-content");if(!can("members")){noAccess(target);$("members-pagination").replaceChildren();return;}state.memberPage=page;const load=beginLoad(target);const data=await api(query("/api/members",{q:$("members-q").value.trim(),status:$("members-status").value,link:$("members-link").value,page}));if(!currentLoad(target,load))return;$("member-total").textContent=textValue(data.total,"0");
    if(!data.items?.length)empty(target,"Chưa có thành viên phù hợp","Thành viên đăng ký với bot bằng tin nhắn riêng. Bạn có thể điều chỉnh bộ lọc.","◫");else{const table=el("table");const head=el("thead");const row=el("tr");["Thành viên","Trạng thái","Liên kết lịch","Cập nhật",""].forEach(t=>row.append(el("th","",t)));head.append(row);const body=el("tbody");data.items.forEach(member=>{const tr=el("tr");const person=el("td");const cell=el("div","person-cell");const nameBox=el("div");const name=button(member.name||member.member_id,"text-button person-name",()=>openMember(member.member_id));nameBox.append(name,el("div","secondary-line",member.member_id));cell.append(el("div","avatar",initials(member.name)),nameBox);person.append(cell);const status=el("td");status.append(statusTag(member.locked?"locked":member.status));const link=el("td");link.append(statusTag(member.link_status||"missing"));if(member.schedule_name)link.append(el("div","secondary-line",member.schedule_name));const action=el("td");action.append(button("Xem →","text-button",()=>openMember(member.member_id)));tr.append(person,status,link,el("td","muted",date(member.updated_at||member.updated)),action);body.append(tr);});table.append(head,body);target.replaceChildren(table);}
    paging($("members-pagination"),data,p=>loadMembers(p).catch(e=>notice($("page-error"),e)));
  }
  async function openMember(id) { state.tab="info";state.sessionKey="";await loadMember(id).catch(e=>notice($("page-error"),e)); }
  async function loadMember(id,preserve=false) {
    clearError();$("members-view").hidden=true;$("member-view").hidden=false;$("page-title").textContent="Chi tiết thành viên";$("page-description").textContent="Danh tính, liên kết và dữ liệu trao đổi của một thành viên.";$("breadcrumb-current").textContent="Thành viên / Chi tiết";const target=$("member-profile");const load=beginLoad(target);$("member-tab-content").replaceChildren();const data=await api(query("/api/member",{id}));if(!currentLoad(target,load)||$("member-view").hidden)return;state.member=data.member||data;renderProfile();await renderMemberTab();
  }
  function renderProfile() { const member=state.member;const info=el("div");info.append(el("h2","",member.name||member.member_id),el("p","secondary-line",member.member_id));const tags=el("div","profile-tags");tags.append(statusTag(member.locked?"locked":member.status),statusTag(member.link_status||"missing"));info.append(tags);const actions=el("div","profile-actions");const lock=button(member.locked?"Mở quyền":"Khóa quyền",`button ${member.locked?"secondary":"danger"}`,()=>changeLock(lock));actions.append(button("Xử lý liên kết","button secondary",openLink),lock);$("member-profile").replaceChildren(el("div","avatar large",initials(member.name)),info,actions);document.querySelectorAll("#member-tabs button").forEach(node=>{node.hidden=(node.dataset.tab==="history"||node.dataset.tab==="context")?!can("conversations"):node.dataset.tab==="memories"?!can("memories"):false;}); }
  function detailPanel(title,rows) { const box=el("article","panel detail-panel");box.append(el("h2","",title));const dl=el("dl","details-list");rows.forEach(([label,value])=>{const row=el("div","detail-row");row.append(el("dt","",label),el("dd","",textValue(value)));dl.append(row);});box.append(dl);return box; }
  async function renderMemberTab() {
    const target=$("member-tab-content");loads.set(target,{});clearError();document.querySelectorAll("#member-tabs button").forEach(node=>node.classList.toggle("active",node.dataset.tab===state.tab));const m=state.member;if(!m)return;
    if(state.tab==="info"){const grid=el("div","detail-grid");grid.append(detailPanel("Thông tin đăng ký",[["Mã thành viên",m.member_id],["Họ tên",m.name],["Điện thoại",m.phone_masked],["Trạng thái",statusNames[m.status]||m.status],["Đăng ký lúc",date(m.created_at||m.created)],["ID Zalo",m.zalo_user_id||m.user_id],["ID chat riêng",m.private_chat_id||m.chat_id]]),detailPanel("Liên kết lịch & Tickets",[["Tên trên lịch",m.schedule_name],["Liên kết",statusNames[m.link_status]||m.link_status],["Chi tiết",m.link_reason],["Tài khoản Tickets",m.tickets_account?.name],["Mã tài khoản",m.tickets_account?.id],["Mã thành viên Tickets",m.tickets_account?.member_id],["Nhóm",m.group_note||"Quyền nhóm được quản lý riêng."]]));target.replaceChildren(grid);return;}
    if(state.tab==="history")return loadHistory(target,1,{member_id:m.member_id});
    if(state.tab==="context")return loadSessionsContext(target,m.member_id);
    if(state.tab==="memories")return loadMemories(target,1,m.member_id,true);
    return loadMemberAudit(target,m.member_id);
  }
  async function changeLock(node) { const m=state.member;const locked=!m.locked;const message=locked?`Khóa quyền của ${m.name||m.member_id}? Thành viên sẽ không thể dùng chức năng nội bộ. Dữ liệu và lịch sử được giữ lại.`:`Mở quyền cho ${m.name||m.member_id}? Thao tác này không cấp quyền quản trị hoặc quyền nhóm mới.`;if(!await confirmAction(locked?"Khóa quyền thành viên":"Mở quyền thành viên",message,locked?"Xác nhận khóa quyền":"Xác nhận mở quyền",locked))return;await busy(node,async()=>{try{await api("/api/member/lock",{member_id:m.member_id,locked,expected_revision:m.revision,confirm:true});toast(locked?"Đã khóa quyền thành viên.":"Đã mở quyền thành viên.");if(state.member?.member_id===m.member_id)await loadMember(m.member_id,true);}catch(e){notice($("page-error"),e);}}); }
  function openLink() { state.linkToken=null;$("link-error").hidden=true;$("link-preview").hidden=true;$("link-preview").replaceChildren();$("link-submit").textContent="Xem trước thay đổi";const previous=$("link-target");let input;
    if(state.member.candidates?.length){input=el("select");input.append(el("option","","Chọn tài khoản phù hợp"));input.firstChild.value="";state.member.candidates.forEach(c=>{const option=el("option","",`${c.name||c.member_id} · ${c.account_id}${c.create_account?" · tạo mới":""}`);option.value=c.account_id;input.append(option);});}else{input=el("input");input.maxLength=200;input.placeholder="Mã tài khoản đích";}
    input.id="link-target";input.required=true;previous.replaceWith(input);$("link-dialog").showModal();input.focus();input.addEventListener("change",()=>{state.linkToken=null;$("link-submit").textContent="Xem trước thay đổi";$("link-preview").hidden=true;}); }
  function pretty(value) { if(value===null||value===undefined)return "—";if(Array.isArray(value))return value.length?value.map(pretty).join("\n\n"):"Không có";if(typeof value==="object")return Object.entries(value).filter(([k])=>!/(token|password|secret|phone(?!_masked))/i.test(k)).map(([k,v])=>`${fieldNames[k]||k}: ${pretty(v)}`).join("\n");if(typeof value==="boolean")return value?"Có":"Không";return String(value); }
  function previewLink(data) { const box=el("div","preview-block");box.append(el("h3","","Thay đổi dự kiến"));if(data.message)box.append(el("p","",data.message));box.append(el("h3","","Hiện tại"),el("pre","",pretty(data.before)),el("h3","","Sau khi xác nhận"),el("pre","",pretty(data.after)));$("link-preview").replaceChildren(box);$("link-preview").hidden=false; }

  function historyFilters() { return {member_id:$("history-member").value.trim(),chat_id:$("history-chat").value.trim(),start:$("history-start").value,end:$("history-end").value}; }
  async function loadHistory(target,page=1,filters={}) { if(!can("conversations")){noAccess(target);return;}const load=beginLoad(target);if(target.id==="conversations-content")state.historyPage=page;const data=await api(query("/api/history",{...filters,page}));if(!currentLoad(target,load))return;pagedShell(target,data,historyCard,p=>loadHistory(target,p,filters).catch(e=>notice($("page-error"),e))); }
  function historyCard(item) { const isBot=item.role==="assistant"||item.role==="bot"||item.direction==="outgoing";const card=el("article",`panel history-item${isBot?" bot":""}`);const header=el("div","history-header");header.append(el("strong","",isBot?"LabCoach":item.member_name||item.member_id||"Thành viên"),tag(isGroup(item.chat_type)?"Nhóm":"Tin nhắn riêng","subtle"));if(item.status)header.append(statusTag(item.status));header.append(el("time","",date(item.created_at??item.created)));const footer=el("div","history-footer");footer.append(el("span","",`Cuộc trò chuyện: ${textValue(item.chat_id)} · Tin: ${textValue(item.id)}`));if(can("memories")&&item.member_id)footer.append(button("+ Tạo ghi nhớ từ tin này","text-button",()=>openMemory(null,item)));card.append(header,el("div","history-body",item.content??item.text??item.body??"(Không có nội dung văn bản)"),footer);return card; }
  async function loadSessionsContext(target,memberId,page=1) { if(!can("conversations")){noAccess(target);return;}const load=beginLoad(target);const data=await api(query("/api/sessions",{member_id:memberId,page}));if(!currentLoad(target,load))return;if(!data.items?.length){empty(target,"Chưa có ngữ cảnh được lưu","Ngữ cảnh xuất hiện khi bot tiếp nhận cuộc trao đổi của thành viên này.");return;}
    const toolbar=el("div","section-toolbar");const label=el("label","","Chọn cuộc trò chuyện");const select=el("select");data.items.forEach(session=>{const option=el("option","",`${isGroup(session.chat_type)?"Nhóm":"Tin nhắn riêng"} · ${textValue(session.chat_id)} · ${date(session.updated_at??session.updated)}`);option.value=session.session_key;select.append(option);});if(data.items.some(x=>x.session_key===state.sessionKey))select.value=state.sessionKey;else state.sessionKey=select.value;label.append(select);toolbar.append(label);const contextTarget=el("div");const pager=el("div","pagination");paging(pager,data,p=>loadSessionsContext(target,memberId,p).catch(e=>notice($("page-error"),e)));target.replaceChildren(toolbar,contextTarget,pager);select.addEventListener("change",()=>{state.sessionKey=select.value;loadContext(contextTarget,select.value).catch(e=>notice($("page-error"),e));});await loadContext(contextTarget,select.value);
  }
  async function loadContext(target,sessionKey) {
    const load=beginLoad(target);const data=await api(query("/api/context",{session_key:sessionKey}));if(!currentLoad(target,load))return;const box=el("div","panel detail-panel");const heading=el("div","section-toolbar");heading.append(el("h2","","Ngữ cảnh của phiên"));const controls=el("div","profile-actions");const rebuild=button("Làm mới ngữ cảnh","button secondary small",()=>changeContext("rebuild",rebuild,target,sessionKey,data.revision));const reset=button("Xóa ngữ cảnh","button danger small",()=>changeContext("reset",reset,target,sessionKey,data.revision));controls.append(rebuild,reset);heading.append(controls);box.append(heading,el("p","muted",`Phiên bản ${textValue(data.revision,"0")} · Chỉ áp dụng cho cuộc trò chuyện đang chọn.`));if(data.truncated)box.append(el("p","notice info","Ngữ cảnh đã được rút gọn để bot xử lý ổn định. Bên dưới là những trích đoạn thực sự được chọn; nội dung đầy đủ vẫn có trong Lịch sử hội thoại."));const summary=el("section","context-section");summary.append(el("h3","","Bản tóm tắt"),el("p","context-summary",data.summary||"Chưa có bản tóm tắt. Bot có thể sử dụng các tin gần đây bên dưới."));box.append(summary);const messages=el("section","context-section");messages.append(el("h3","","Các tin gần đây được dùng cho lượt tiếp theo"));if(data.messages?.length)data.messages.forEach(m=>{const part=el("div","mini-message");part.append(el("strong","",m.role==="assistant"||m.direction==="outgoing"?"LabCoach":"Thành viên"),el("span","",m.content??m.body??m.text??""));messages.append(part);});else messages.append(el("p","muted","Chưa có tin nào trong ngữ cảnh."));box.append(messages);const memories=el("section","context-section");memories.append(el("h3","","Ghi nhớ được chọn cho phiên"));if(data.memories_redacted)memories.append(el("p","muted","Ghi nhớ được ẩn vì tài khoản chưa có quyền xem."));else if(data.memories?.length)data.memories.forEach(m=>{const part=el("div","mini-message");part.append(el("strong","",scopeNames[m.scope]||"Ghi nhớ"),el("span","",m.content));memories.append(part);});else memories.append(el("p","muted","Không có ghi nhớ phù hợp đang được sử dụng."));box.append(memories);if(data.last_prepared){const p=el("p","secondary-line",`Lần chuẩn bị ngữ cảnh gần nhất: ${date(data.last_prepared.prepared_at??data.last_prepared.created_at??data.last_prepared.created??data.last_prepared.at)}. Danh sách trên là ngữ cảnh hiện tại cho lượt tiếp theo.`);box.append(p);}target.replaceChildren(box);
  }
  async function changeContext(action,node,target,key,revision) { const message=action==="reset"?"Xóa ngữ cảnh của phiên này? Lịch sử hội thoại và ghi nhớ dài hạn vẫn được giữ. Thay đổi có hiệu lực ở lượt trả lời tiếp theo.":"Dựng lại ngữ cảnh từ các tin được phép sử dụng? Bản tóm tắt cũ sẽ được thay thế.";if(!await confirmAction(action==="reset"?"Xóa ngữ cảnh":"Làm mới ngữ cảnh",message,action==="reset"?"Xác nhận xóa ngữ cảnh":"Xác nhận làm mới",action==="reset"))return;await busy(node,async()=>{try{await api(`/api/context/${action}`,{session_key:key,expected_revision:revision,confirm:true});toast(action==="reset"?"Đã xóa ngữ cảnh của phiên.":"Đã làm mới ngữ cảnh.");await loadContext(target,key);}catch(e){notice($("page-error"),e);}}); }

  async function loadMemories(target,page=1,memberId="",toolbar=false) { if(!can("memories")){noAccess(target);return;}const load=beginLoad(target);if(target.id==="memories-content")state.memoryPage=page;const data=await api(query("/api/memories",{member_id:memberId,page}));if(!currentLoad(target,load))return;const content=el("div");pagedShell(content,data,memoryCard,p=>loadMemories(target,p,memberId,toolbar).catch(e=>notice($("page-error"),e)));if(toolbar){const controls=el("div","section-toolbar");controls.append(el("p","muted","Ghi nhớ đang dùng và đã ngừng sử dụng."),button("+ Thêm ghi nhớ","button primary",()=>openMemory()));target.replaceChildren(controls,content);}else target.replaceChildren(content); }
  function memoryCard(memory) { const active=Boolean(memory.active);const expiry=memory.expires?(typeof memory.expires==="number"?memory.expires*1000:Date.parse(memory.expires)):null;const expired=Boolean(expiry&&expiry<=Date.now());const card=el("article",`panel memory-card${active&&!expired?"":" inactive"}`);const header=el("div","memory-header");header.append(tag(scopeNames[memory.scope]||memory.scope,"subtle"),tag(expired?"Đã hết hạn":active?"Đang sử dụng":"Đã ngừng",active&&!expired?"":"warning"),el("time","",`Cập nhật ${date(memory.updated_at??memory.updated??memory.created_at??memory.created)}`));card.append(header,el("p","memory-content",memory.content));const meta=el("div","memory-meta");meta.append(el("span","",`Thành viên: ${textValue(memory.member_id)}`));if(memory.chat_id||memory.group_id)meta.append(el("span","",`Nhóm: ${memory.chat_id||memory.group_id}`));if(memory.expires)meta.append(el("span","",`Hết hạn: ${date(memory.expires)}`));meta.append(el("span","",`Phiên bản ${textValue(memory.revision,"1")}`));card.append(meta);const actions=el("div","memory-actions");if(memory.source_message_id)actions.append(button(memory.source_expired?"Nguồn đã hết hạn lưu":"Xem tin nhắn nguồn","text-button",()=>showSource(memory)));actions.append(button("Lịch sử sửa","text-button",()=>showMemoryVersions(memory)),button("Chỉnh sửa","text-button",()=>openMemory(memory)));if(active){const stop=button("Ngừng sử dụng","text-button",()=>stopMemory(memory,stop));actions.append(stop);}card.append(actions);return card; }
  function openMemory(memory=null,source=null) {
    state.editingMemory=memory?{...memory}:source?{source_message_id:source.id,session_key:source.session_key,chat_id:source.chat_id,member_id:source.member_id}:null;
    $("memory-form").reset();$("memory-active").checked=memory?Boolean(memory.active):true;$("memory-error").hidden=true;$("memory-dialog-title").textContent=memory?"Chỉnh sửa ghi nhớ":"Thêm ghi nhớ";$("memory-content").value=memory?.content||"";$("memory-member").value=memory?.member_id||source?.member_id||state.member?.member_id||$("memories-member").value.trim();$("memory-scope").value=memory?.scope||"personal";$("memory-session").value=memory?.session_key||source?.session_key||"";$("memory-chat").value=memory?.chat_id||memory?.group_id||(isGroup(source?.chat_type)?source.chat_id:"")||"";$("memory-expires").value=isoDay(memory?.expires);const sourceId=memory?.source_message_id||source?.id;$("memory-source").hidden=!sourceId;if(sourceId)$("memory-source").textContent=`Tin nhắn nguồn: ${sourceId}${source?". Tóm tắt thông tin hữu ích ở ô nội dung; không cần chép nguyên tin.":""}`;memoryScope();$("memory-dialog").showModal();$("memory-content").focus();
  }
  function memoryScope() { const scope=$("memory-scope").value;const shared=scope==="group" || (scope==="session" && $("memory-session").value.trim().toUpperCase().startsWith("ZALO:GROUP:"));$("memory-member").required=scope==="personal";$("memory-session-wrap").hidden=scope!=="session";$("memory-chat-wrap").hidden=scope!=="group";$("memory-session").required=scope==="session";$("memory-chat").required=scope==="group";$("memory-share-warning").hidden=!shared;$("memory-share-wrap").hidden=!shared;$("memory-share").required=shared; }
  async function refreshMemories() { if(state.member)await renderMemberTab();else await loadMemories($("memories-content"),state.memoryPage,$("memories-member").value.trim()); }
  async function stopMemory(memory,node) { if(!await confirmAction("Ngừng sử dụng ghi nhớ","Bot sẽ không đưa nội dung ghi nhớ này vào lượt trả lời tiếp theo. Lịch sử thay đổi vẫn được giữ.","Xác nhận ngừng sử dụng",true))return;await busy(node,async()=>{try{await api("/api/memory/stop",{id:memory.id,expected_revision:memory.revision,confirm:true});toast("Đã ngừng sử dụng ghi nhớ.");await refreshMemories();}catch(e){notice($("page-error"),e);}}); }
  async function showSource(memory) { if(!can("conversations")){toast("Bạn cần quyền đọc hội thoại để xem tin nhắn nguồn.");return;}$("source-dialog").querySelector("h2").textContent="Tin nhắn nguồn";$("source-dialog").showModal();loading($("source-content"));try{const data=await api(query("/api/history",{message_id:memory.source_message_id,page:1}));const source=data.items?.find(item=>String(item.id)===String(memory.source_message_id));if(source)$("source-content").replaceChildren(historyCard(source));else empty($("source-content"),"Tin nhắn nguồn không còn khả dụng","Nguồn có thể đã hết hạn lưu hoặc nằm ngoài phạm vi truy cập của bạn.");}catch(e){empty($("source-content"),"Không thể xem tin nhắn nguồn",messageOf(e));} }
  async function showMemoryVersions(memory) { $("source-dialog").querySelector("h2").textContent="Lịch sử thay đổi ghi nhớ";$("source-dialog").showModal();loading($("source-content"));try{const data=await api(query("/api/memory/versions",{id:memory.id}));if(!data.items?.length){empty($("source-content"),"Chưa có lịch sử thay đổi");return;}const stack=el("div","stack");data.items.forEach(version=>{const box=el("article","panel detail-panel");box.append(el("h3","",`Phiên bản ${version.revision}`),el("p","secondary-line",`${textValue(version.actor)} · ${date(version.created_at??version.created)}`),el("pre","audit-detail",pretty(version.snapshot??version)));stack.append(box);});$("source-content").replaceChildren(stack);}catch(e){empty($("source-content"),"Không thể tải lịch sử",messageOf(e));} }
  async function loadMemberAudit(target,memberId) {
    const controls=el("div","section-toolbar");const label=el("label","","Loại thay đổi");const select=el("select");const memberOption=el("option","","Quyền & liên kết thành viên");memberOption.value="members";select.append(memberOption);
    if(can("memories")){const conversationOption=el("option","","Ngữ cảnh & ghi nhớ");conversationOption.value="conversations";select.append(conversationOption);}
    label.append(select);controls.append(label);const content=el("div");target.replaceChildren(controls,content);const load=()=>loadAudit(content,1,{member_id:memberId,kind:select.value});select.addEventListener("change",()=>load().catch(e=>notice($("page-error"),e)));await load();
  }
  async function loadAudit(target,page=1,filters={}) { const load=beginLoad(target);const data=await api(query("/api/audit",{...filters,page}));if(!currentLoad(target,load))return;const box=panel();if(data.items?.length)data.items.forEach(item=>{const row=el("article","audit-entry");const header=el("div","audit-header");header.append(el("strong","",actionNames[item.action]||item.action),el("time","",date(item.created_at??item.created??item.at)));row.append(header,el("p","",`Thực hiện: ${textValue(item.actor)}`));if(item.before||item.after)row.append(el("div","audit-detail",`Trước: ${pretty(item.before)}\n\nSau: ${pretty(item.after)}`));else if(item.detail||item.details)row.append(el("div","audit-detail",pretty(item.detail||item.details)));box.append(row);});else empty(box,"Chưa có thay đổi","Các thao tác quản trị sẽ được ghi lại tại đây.");const pager=el("div","pagination");paging(pager,data,p=>loadAudit(target,p,filters).catch(e=>notice($("page-error"),e)));target.replaceChildren(box,pager); }

  const ticketStatusNames={pending_peer:"Chờ người đổi cùng",pending_manager:"Chờ Admin duyệt",applying:"Đang cập nhật lịch",uncertain:"Cần đối soát",completed:"Đã đổi ca",rejected:"Đã từ chối",cancelled:"Đã hủy",expired:"Đã hết hạn",stale:"Lịch hoặc quyền đã thay đổi"};
  function ticketStatusTag(value) { return tag(ticketStatusNames[value]||textValue(value),["pending_peer","pending_manager","applying","uncertain"].includes(value)?"warning":["rejected","stale"].includes(value)?"danger":["expired","cancelled"].includes(value)?"subtle":""); }
  function ticketSchedule(shift) { return shift?`${date(shift.start)} – ${date(shift.end)}`:"Chưa có thông tin ca"; }
  function ticketLocation(shift) { return [shift?.course,shift?.room&&`Phòng ${shift.room}`,shift?.period].filter(Boolean).join(" · ")||"Chưa có thông tin môn học, phòng"; }
  function renderTicketCount(data) {
    const count=Number(data.summary?.pending_manager)||0;
    $("ticket-pending-total").textContent=String(count);
    $("tickets-nav-count").textContent=String(count);
    $("tickets-nav-count").hidden=!count||data.enabled===false;
    $("tickets-nav-count").setAttribute("aria-label",`${count} yêu cầu chờ Admin duyệt`);
  }
  async function refreshTicketCount() {
    if(!can("tickets"))return;
    const user=state.user;
    try{const data=await api(query("/api/tickets",{status:"pending_manager",page:1}));if(state.user===user)renderTicketCount(data);}
    catch(_error){if(state.user===user)$("tickets-nav-count").hidden=true;}
  }
  async function loadTickets(page=1) {
    const target=$("tickets-content");if(!can("tickets")){empty(target,"Bạn chưa có quyền duyệt đổi ca","Giảng viên cần cấp riêng quyền Duyệt đổi ca cho tài khoản này.","⊘");return;}
    state.ticketPage=page;const load=beginLoad(target);$("tickets-source-notice").hidden=true;$("tickets-pagination").replaceChildren();
    const data=await api(query("/api/tickets",{q:$("tickets-q").value.trim(),status:$("tickets-status").value,page}));
    if(!currentLoad(target,load)||$("tickets-view").hidden)return;
    renderTicketCount(data);$("ticket-total").textContent=`${data.total??0} yêu cầu`;
    const sourceNotice=$("tickets-source-notice");
    if(data.enabled===false){sourceNotice.textContent=data.message||"Chức năng đổi ca chưa được bật.";sourceNotice.hidden=false;empty(target,"Chưa bật chức năng đổi ca","Liên hệ người quản trị hệ thống để kiểm tra cấu hình.","⇄");return;}
    if(Number(data.summary?.uncertain)>0){sourceNotice.replaceChildren(el("span","",`${data.summary.uncertain} yêu cầu cần đối soát kết quả cập nhật. `),button("Xem yêu cầu cần đối soát","text-button",()=>{$("tickets-status").value="uncertain";loadTickets(1).catch(issueError);}));sourceNotice.hidden=false;}
    if(!data.items?.length)empty(target,"Chưa có yêu cầu phù hợp","Thử đổi trạng thái hoặc từ khóa tìm kiếm. Yêu cầu chờ người đổi cùng xác nhận chưa thể duyệt.","⇄");
    else {
      const table=el("table","tickets-table"),head=el("thead"),row=el("tr"),body=el("tbody");
      ["Yêu cầu / Lý do","Ca người đề nghị","Ca người đổi cùng","Trạng thái","Thời hạn",""].forEach(text=>row.append(el("th","",text)));head.append(row);
      for(const item of data.items){
        const tr=el("tr"),title=el("td","ticket-title-cell"),status=el("td"),times=el("td","ticket-time-cell"),action=el("td");
        title.append(button(item.id,"text-button person-name",()=>openTicket(item.id)),el("p","ticket-reason-preview",item.reason||"Chưa có lý do"));
        const shiftCell=shift=>{const cell=el("td","ticket-shift-cell");cell.append(el("strong","person-name",shift?.member_name||shift?.member_id||"Chưa xác định"),el("p","ticket-schedule",ticketSchedule(shift)),el("p","secondary-line",ticketLocation(shift)));return cell;};
        status.append(ticketStatusTag(item.status));times.append(el("p","",`Tạo: ${date(item.created_at)}`),el("p","secondary-line",`Hết hạn: ${date(item.expires_at)}`));action.append(button("Xem →","text-button",()=>openTicket(item.id)));
        tr.append(title,shiftCell(item.a),shiftCell(item.b),status,times,action);body.append(tr);
      }
      table.append(head,body);target.replaceChildren(table);
    }
    paging($("tickets-pagination"),data,p=>loadTickets(p).catch(issueError));
  }
  async function openTicket(id) { state.ticket={id};await loadTicket(id).catch(issueError); }
  async function loadTicket(id) {
    if(!can("tickets"))return;
    clearError();$("tickets-view").hidden=true;$("ticket-view").hidden=false;$("page-title").textContent="Chi tiết đổi ca";$("page-description").textContent="Đối chiếu hai ca và xem lịch sử xác nhận trước khi quyết định.";$("breadcrumb-current").textContent="Duyệt đổi ca / Chi tiết";
    const target=$("ticket-content"),load=beginLoad(target),data=await api(query("/api/ticket",{id}));
    if(!currentLoad(target,load)||$("ticket-view").hidden)return;
    if(data.enabled===false||!data.ticket){empty(target,"Yêu cầu chưa khả dụng",data.message||"Chức năng đổi ca chưa được bật.");return;}
    state.ticket=data.ticket;state.ticketRefreshRequired=null;renderTicket();refreshTicketCount();
  }
  function syncTicketControls() {
    document.querySelectorAll("[data-ticket-control]").forEach(node=>{node.disabled=state.ticketBusy||state.ticketRefreshRequired===state.ticket?.id;});
    $("ticket-content").setAttribute("aria-busy",String(state.ticketBusy));
  }
  function renderTicket() {
    const ticket=state.ticket,target=$("ticket-content"),heading=el("article","panel detail-panel ticket-heading"),tags=el("div","profile-tags");
    tags.append(tag(ticket.id,"subtle"),ticketStatusTag(ticket.status));heading.append(tags,el("h2","","Đề nghị đổi ca"),el("p","secondary-line",`Tạo lúc ${date(ticket.created_at)} · Hết hạn ${date(ticket.expires_at)} · Phiên bản ${textValue(ticket.version)}`),el("h3","ticket-reason-heading","Lý do đổi ca"),el("p","issue-body",ticket.reason||"Chưa có lý do"));
    const shifts=el("div","detail-grid ticket-shifts");
    for(const [label,shift,receiver] of [["Ca người đề nghị",ticket.a,ticket.b],["Ca người đổi cùng",ticket.b,ticket.a]]){
      const card=detailPanel(label,[["Người phụ trách ban đầu",shift?.member_name||shift?.member_id],["Bắt đầu",date(shift?.start)],["Kết thúc",date(shift?.end)],["Môn học",shift?.course],["Phòng",shift?.room],["Tiết học",shift?.period]]);
      card.append(el("p","ticket-receiving",`Sau khi đổi: ${receiver?.member_name||receiver?.member_id||"Chưa xác định"} nhận ca này.`));shifts.append(card);
    }
    const grid=el("div","ticket-detail-grid"),decision=el("article","panel detail-panel ticket-decision"),audit=el("article","panel detail-panel");decision.append(el("h2","","Xử lý yêu cầu"));
    const statusHelp={pending_peer:"Đang chờ người đổi cùng xác nhận. Chỉ duyệt sau khi có đủ xác nhận.",pending_manager:"Người đổi cùng đã đồng ý. Kiểm tra cả hai ca và lý do trước khi duyệt.",applying:"Hệ thống đang cập nhật lịch. Làm mới để kiểm tra kết quả trước khi thao tác tiếp.",uncertain:"Chưa xác định được kết quả lần cập nhật trước. Chỉ đối soát bằng cách đọc lại lịch; không tự ghi lại Google Sheets.",completed:"Lịch đã cập nhật và được xác minh.",rejected:"Yêu cầu đã bị từ chối.",cancelled:"Người đề nghị đã hủy yêu cầu.",expired:"Yêu cầu đã hết hạn xác nhận.",stale:"Lịch hoặc quyền đã thay đổi. Cần tạo yêu cầu mới từ lịch hiện tại."};
    decision.append(el("p",`notice ${ticket.status==="uncertain"?"warning":"info"}`,statusHelp[ticket.status]||ticket.status_label||"Xem lịch sử xử lý bên cạnh."));
    if(ticket.message)decision.append(el("p","issue-body ticket-latest",ticket.message));
    const available=Array.isArray(ticket.actions)?ticket.actions:[];
    const actions=available.filter(action=>["approve","reject","reconcile"].includes(action)&&(ticket.status!=="uncertain"||action==="reconcile"));
    const controls=el("div","stack");
    if(actions.includes("approve")){const approve=button("Duyệt và cập nhật lịch","button primary",()=>ticketMutation("approve"));approve.dataset.ticketControl="true";controls.append(approve);}
    if(actions.includes("reconcile")){const reconcile=button("Đối soát kết quả","button secondary",()=>ticketMutation("reconcile"));reconcile.dataset.ticketControl="true";controls.append(reconcile);}
    if(actions.includes("reject")){
      const form=el("form","stack ticket-reject-form"),reason=labeledInput("Lý do từ chối","textarea","",true);reason.input.maxLength=500;reason.input.dataset.ticketControl="true";reason.input.placeholder="Nêu lý do để hai thành viên hiểu quyết định.";
      const reject=submitButton("Từ chối yêu cầu");reject.className="button danger";reject.dataset.ticketControl="true";form.append(reason.wrap,reject);
      form.addEventListener("submit",event=>{event.preventDefault();const value=reason.input.value.trim();if(!value){reason.input.setCustomValidity("Vui lòng nhập lý do từ chối.");reason.input.reportValidity();return;}ticketMutation("reject",value);});reason.input.addEventListener("input",()=>reason.input.setCustomValidity(""));controls.append(form);
    }
    if(!actions.length)controls.append(el("p","muted","Không có thao tác duyệt khả dụng cho trạng thái này."));
    const error=el("div");error.id="ticket-action-error";error.hidden=true;error.setAttribute("role","alert");decision.append(controls,error);
    audit.append(el("h2","","Lịch sử xử lý"));
    for(const event of ticket.events||[]){const entry=el("article","issue-audit");entry.append(el("strong","",event.actor||"Hệ thống"),el("p","secondary-line",date(event.at)),el("p","",event.text||""));audit.append(entry);}
    if(!ticket.events?.length)audit.append(el("p","muted","Chưa có lịch sử xử lý."));
    grid.append(decision,audit);target.replaceChildren(heading,shifts,grid);syncTicketControls();
  }
  async function ticketMutation(action,reason="") {
    const ticket=state.ticket,user=state.user;
    if(!ticket||state.ticketBusy||state.ticketRefreshRequired===ticket.id||!can("tickets")||!ticket.actions?.includes(action))return;
    if(ticket.status==="uncertain"&&action!=="reconcile")return;
    state.ticketBusy=true;syncTicketControls();
    try{
      const questions={approve:["Duyệt và cập nhật lịch",`${ticket.id}\n${ticket.a?.member_name||"Người đề nghị"} ⇄ ${ticket.b?.member_name||"Người đổi cùng"}\n\nHệ thống sẽ kiểm tra lại lịch, quyền và xác nhận của hai thành viên. Nếu hợp lệ, thao tác này cập nhật lịch thật trên Google Sheets.`,"Xác nhận duyệt"],reject:["Từ chối yêu cầu đổi ca",`${ticket.id}\n\nLý do: ${reason}\n\nLý do sẽ được ghi vào lịch sử xử lý.`,"Xác nhận từ chối"],reconcile:["Đối soát kết quả cập nhật","Hệ thống chỉ đọc lại lịch để xác định kết quả lần cập nhật trước. Thao tác này không ghi lại Google Sheets.","Đối soát"]};
      const [title,message,label]=questions[action];
      if(!await confirmAction(title,message,label,action==="reject"))return;
      if(state.user!==user||state.ticket?.id!==ticket.id||state.ticket?.version!==ticket.version)return;
      clearError();const data=await api("/api/ticket/action",{id:ticket.id,action,expected_version:ticket.version,reason,confirm:true});
      if(state.user!==user)return;
      if(state.ticket?.id===ticket.id&&!$("ticket-view").hidden){if(data.ticket){state.ticket=data.ticket;state.ticketRefreshRequired=null;renderTicket();}else throw new Error("Chưa nhận được kết quả yêu cầu. Hãy tải lại để kiểm tra.");}
      toast(data.ticket?.status==="uncertain"?"Chưa xác định kết quả. Cần đối soát trước khi xử lý tiếp.":action==="reject"?"Đã từ chối yêu cầu.":action==="reconcile"?"Đã đối soát kết quả.":"Đã xử lý yêu cầu đổi ca.");refreshTicketCount();
    }catch(error){
      if(state.user!==user)return;
      state.ticketRefreshRequired=ticket.id;
      if(state.ticket?.id===ticket.id&&!$("ticket-view").hidden){
        const box=$("ticket-action-error");if(box){box.className="notice warning";box.hidden=false;box.replaceChildren(el("p","",`${messageOf(error)} ${error.status===409?"Yêu cầu đã thay đổi. Tải lại phiên bản mới trước khi quyết định.":"Tải lại yêu cầu để kiểm tra kết quả trước khi thao tác tiếp. Hệ thống không tự gửi lại lệnh duyệt."}`),button("Tải lại yêu cầu","button secondary small",()=>loadTicket(ticket.id).catch(issueError)));box.scrollIntoView({block:"nearest"});}
      }
    }finally{state.ticketBusy=false;syncTicketControls();}
  }

  const issueStatusNames={new:"Mới",in_progress:"Đang xử lý",waiting:"Chờ bổ sung",done:"Hoàn thành"};
  const issueKindNames={support:"Báo lỗi / hỗ trợ",feature:"Góp ý / tính năng"};
  const permissionNames={issues:"Issue",tickets:"Duyệt đổi ca",members:"Thành viên",conversations:"Hội thoại",memories:"Ghi nhớ"};
  const issueActionNames={notification_retry:"Thử gửi lại thông báo",create:"Tạo yêu cầu",created:"Tạo yêu cầu",claim:"Nhận xử lý",assign:"Phân công",status:"Đổi trạng thái",reopen:"Mở lại",comment:"Thêm phản hồi",plan:"Cập nhật kế hoạch"};
  const issueStatusTag=value=>tag(issueStatusNames[value]||value,value==="waiting"?"warning":value==="new"?"subtle":"");
  function labeledInput(label,type="text",value="",required=false) { const wrap=el("label","",label);const input=el(type==="textarea"?"textarea":"input");if(type!=="textarea")input.type=type;else input.rows=3;input.value=value??"";input.required=required;wrap.append(input);return {wrap,input}; }
  function labeledSelect(label,options,value="") { const wrap=el("label","",label);const input=el("select");for(const [key,text] of options){const item=el("option","",text);item.value=key;input.append(item);}input.value=value;if(input.selectedIndex<0&&input.options.length)input.selectedIndex=0;wrap.append(input);return {wrap,input}; }
  function submitButton(text) { const b=button(text,"button primary");b.type="submit";return b; }
  function issueError(error) { notice($("page-error"),error);$("page-error").scrollIntoView({block:"nearest"}); }
  async function loadOperators() {
    const user=state.user;const data=await api("/api/issue/operators");if(user!==state.user)return;
    state.operators=data.items||[];const select=$("issues-assignee"),value=select.value;
    select.replaceChildren();[["","Tất cả"],["unassigned","Chưa có người nhận"],...state.operators.map(x=>[x.username,x.display_name||x.username])].forEach(([key,name])=>{const option=el("option","",name);option.value=key;select.append(option);});
    select.value=value;if(select.selectedIndex<0)select.value="";
  }
  async function loadIssues(page=1) {
    const target=$("issues-content");if(!canView("issues")){noAccess(target);return;}state.issuePage=page;const load=beginLoad(target);
    await loadOperators();if(!currentLoad(target,load))return;
    const data=await api(query("/api/issues",{q:$("issues-q").value.trim(),kind:$("issues-kind").value,status:$("issues-status").value,member_id:$("issues-member").value.trim(),assignee:$("issues-assignee").value,page}));
    if(!currentLoad(target,load))return;$("issue-total").textContent=data.total??0;
    if(!data.items?.length)empty(target,"Chưa có yêu cầu phù hợp","Thành viên có thể gửi yêu cầu hỗ trợ bằng /issue trong tin nhắn riêng với bot.");
    else {const table=el("table"),head=el("thead"),row=el("tr"),body=el("tbody");["Yêu cầu","Người gửi","Trạng thái","Phụ trách","Cập nhật",""].forEach(t=>row.append(el("th","",t)));head.append(row);
      data.items.forEach(item=>{const tr=el("tr"),title=el("td","issue-title-cell"),status=el("td"),person=el("td"),action=el("td");title.append(button(item.title||item.id,"text-button person-name",()=>openIssue(item.id)),el("div","secondary-line",`${item.code||item.id} · ${issueKindNames[item.kind]||item.kind}`));person.append(el("div","",item.member_name||item.member_id),el("div","secondary-line",item.member_id));status.append(issueStatusTag(item.status));action.append(button("Xem →","text-button",()=>openIssue(item.id)));tr.append(title,person,status,el("td","",item.assignee_display_name||item.assignee||"Chưa có"),el("td","muted",date(item.updated_at??item.updated)),action);body.append(tr);});table.append(head,body);target.replaceChildren(table);}
    paging($("issues-pagination"),data,p=>loadIssues(p).catch(issueError));
  }
  async function openIssue(id) { state.issue={id};await loadIssue(id).catch(issueError); }
  async function loadIssue(id) {
    if(!canView("issues"))return;clearError();$("issues-view").hidden=true;$("issue-view").hidden=false;$("page-title").textContent="Chi tiết Issue";$("page-description").textContent="Thông tin, người phụ trách và tiến độ xử lý.";$("breadcrumb-current").textContent="Issue / Chi tiết";
    const target=$("issue-content"),load=beginLoad(target);const data=await api(query("/api/issue",{id}));if(!currentLoad(target,load)||$("issue-view").hidden)return;state.issue=data.issue||data;renderIssue(data);
  }
  async function issueMutation(action,values,node) {
    const issue=state.issue;if(!issue)return;clearError();
    await busy(node,async()=>{try {await api("/api/issue/action",{id:issue.id,action,revision:issue.revision,confirm:true,...values});toast("Đã cập nhật yêu cầu.");if(state.issue?.id===issue.id)await loadIssue(issue.id);}catch(error){issueError(error);}});
  }
  function renderIssue(data) {
    const issue=state.issue,target=$("issue-content"),editable=isFaculty()||issue.assignee===state.user.username;
    const heading=el("article","panel detail-panel issue-heading"),tags=el("div","profile-tags");tags.append(tag(issue.code||issue.id,"subtle"),issueStatusTag(issue.status),tag(issueKindNames[issue.kind]||issue.kind,"subtle"));
    heading.append(tags,el("h2","",issue.title),el("p","issue-body",issue.body||issue.description||""),el("p","secondary-line",`Người gửi: ${issue.member_name||issue.member_id} · ${issue.member_id} · ${date(issue.created_at??issue.created)}`));
    const owner=el("div","section-toolbar issue-owner");owner.append(el("p","",`Phụ trách: ${issue.assignee_display_name||issue.assignee||"Chưa có người nhận"}`));
    if(!issue.assignee&&issue.status==="new"){const claim=button("Nhận xử lý","button primary",async()=>{if(await confirmAction("Nhận xử lý yêu cầu",`Bạn sẽ là người phụ trách chính của ${issue.code||issue.id}.`,"Nhận xử lý"))await issueMutation("claim",{},claim);});owner.append(claim);}heading.append(owner);
    const grid=el("div","issue-detail-grid"),main=el("div","stack"),side=el("div","stack");
    const comments=el("article","panel detail-panel");comments.append(el("h2","","Trao đổi xử lý"));
    if(!data.comments?.length)comments.append(el("p","muted","Chưa có ghi chú hoặc phản hồi."));
    for(const item of data.comments||[]){const note=el("article",`issue-comment ${item.visibility==="public"?"public":"internal"}`),meta=el("div","history-header");meta.append(tag(item.visibility==="public"?"Phản hồi cho người gửi":"Ghi chú nội bộ",item.visibility==="public"?"":"warning"),el("strong","",item.actor||item.author||"Admin"),el("time","",date(item.created_at??item.created??item.at)));note.append(meta,el("p","issue-body",item.body||item.text||item.content));comments.append(note);}
    main.append(comments);
    const commentBox=el("article","panel detail-panel"),form=el("form","stack");commentBox.append(el("h2","","Thêm trao đổi"));
    const visibility=labeledSelect("Nơi hiển thị",editable?[["internal","Ghi chú nội bộ — chỉ đội hỗ trợ"],["public","Phản hồi cho người gửi — gửi qua Zalo"]]:[["internal","Ghi chú nội bộ — chỉ đội hỗ trợ"]],"internal");
    const content=labeledInput("Nội dung","textarea","",true);content.input.maxLength=1200;const add=submitButton("Lưu ghi chú");visibility.input.addEventListener("change",()=>{add.textContent=visibility.input.value==="public"?"Gửi phản hồi":"Lưu ghi chú";});form.append(visibility.wrap,content.wrap,add);
    form.addEventListener("submit",async event=>{event.preventDefault();if(!content.input.value.trim())return;if(visibility.input.value==="public"&&!await confirmAction("Gửi phản hồi cho thành viên",content.input.value.trim(),"Gửi phản hồi"))return;await issueMutation("comment",{text:content.input.value.trim(),visibility:visibility.input.value},form);});commentBox.append(form);main.append(commentBox);
    if(editable&&issue.status!=="new")side.append(issueStatusForm(issue));
    if(isFaculty())side.append(issueAssignForm(issue));
    if(issue.kind==="feature")side.append(issuePlanForm(issue,editable));
    if(data.notifications?.length){const notices=el("article","panel detail-panel");notices.append(el("h2","","Thông báo Zalo"));for(const item of data.notifications){const row=el("div","issue-notification");const labels={pending:"Chờ gửi",sending:"Đang gửi",sent:"Đã gửi",failed:"Gửi lỗi",uncertain:"Chưa rõ kết quả",blocked:"Đã chặn"};row.append(tag(labels[item.status]||item.status,item.status==="failed"?"danger":item.status==="uncertain"?"warning":"subtle"),el("span","secondary-line",date(item.updated_at??item.updated??item.created)));if(item.status==="failed"&&item.attempts<3){const retry=button("Thử gửi lại","text-button",async()=>{if(!await confirmAction("Gửi lại thông báo","Chỉ thử lại thông báo đã xác định chưa gửi thành công. Hệ thống sẽ kiểm tra quyền người nhận trước khi gửi.","Gửi lại"))return;await busy(retry,async()=>{try{await api("/api/issue/notification/retry",{id:item.id,confirm:true});toast("Đã đưa thông báo vào hàng chờ.");await loadIssue(issue.id);}catch(error){issueError(error);}});});row.append(retry);}notices.append(row);}side.append(notices);}
    const audit=el("article","panel detail-panel");audit.append(el("h2","","Lịch sử xử lý"));for(const item of data.audit||[]){const row=el("div","issue-audit");row.append(el("strong","",issueActionNames[item.action]||item.action),el("p","secondary-line",`${item.actor||"Hệ thống"} · ${date(item.created_at??item.created??item.at)}`));if(item.details?.reason||item.reason)row.append(el("p","",item.details?.reason||item.reason));if(item.details?.to)row.append(el("p","",`Chuyển tới: ${issueStatusNames[item.details.to]||item.details.to}`));audit.append(row);}if(!data.audit?.length)audit.append(el("p","muted","Chưa có lịch sử."));side.append(audit);
    grid.append(main,side);target.replaceChildren(heading,grid);
  }
  function issueStatusForm(issue) {
    const box=el("article","panel detail-panel"),form=el("form","stack");box.append(el("h2","",issue.status==="done"?"Mở lại yêu cầu":"Cập nhật trạng thái"));
    if(issue.status==="done"){const reason=labeledInput("Lý do mở lại","textarea","",true);reason.input.maxLength=500;form.append(reason.wrap,submitButton("Mở lại"));form.addEventListener("submit",async e=>{e.preventDefault();if(await confirmAction("Mở lại yêu cầu",reason.input.value.trim(),"Mở lại"))await issueMutation("reopen",{reason:reason.input.value.trim()},form);});}
    else {const choices=issue.status==="waiting"?[["in_progress","Đang xử lý"],["done","Hoàn thành"]]:[["waiting","Chờ bổ sung"],["done","Hoàn thành"]];const status=labeledSelect("Trạng thái mới",choices,choices[0][0]),reply=labeledInput("Phản hồi cho người gửi","textarea");reply.input.maxLength=1200;const help=el("p","muted","Khi chờ bổ sung hoặc hoàn thành, hãy viết phản hồi rõ ràng để bot gửi cho thành viên.");const sync=()=>{reply.input.required=["waiting","done"].includes(status.input.value);};status.input.addEventListener("change",sync);sync();form.append(status.wrap,reply.wrap,help,submitButton("Cập nhật"));form.addEventListener("submit",async e=>{e.preventDefault();if(await confirmAction("Cập nhật trạng thái",`Chuyển sang “${issueStatusNames[status.input.value]}”.${reply.input.value.trim()?"\n\nPhản hồi cho người gửi:\n"+reply.input.value.trim():""}`,"Xác nhận cập nhật"))await issueMutation("status",{status:status.input.value,public_reply:reply.input.value.trim()},form);});}
    box.append(form);return box;
  }
  function issueAssignForm(issue) {
    const box=el("article","panel detail-panel"),form=el("form","stack");box.append(el("h2","","Phân công / bàn giao"));
    const owner=labeledSelect("Người phụ trách",[["","Chọn người phụ trách"],...state.operators.map(x=>[x.username,x.display_name||x.username])],issue.assignee||""),reason=labeledInput("Lý do phân công","textarea","",true);owner.input.required=true;reason.input.maxLength=500;form.append(owner.wrap,reason.wrap,submitButton("Lưu phân công"));form.addEventListener("submit",async e=>{e.preventDefault();if(await confirmAction("Phân công yêu cầu",`Giao cho ${owner.input.selectedOptions[0].textContent}.\n${reason.input.value.trim()}`,"Xác nhận phân công"))await issueMutation("assign",{assignee:owner.input.value,reason:reason.input.value.trim()},form);});box.append(form);return box;
  }
  function issuePlanForm(issue,editable) {
    const box=el("article","panel detail-panel");box.append(el("h2","","Kế hoạch thực hiện"));
    const priorities={"":"Chưa đánh giá",low:"Thấp",normal:"Bình thường",high:"Cao",urgent:"Khẩn cấp"};
    if(!editable){box.append(el("p","",`Ưu tiên: ${priorities[issue.priority]||"Chưa đánh giá"}`),el("p","",`Estimate: ${textValue(issue.estimate)}`),el("p","",`Dự kiến xong: ${textValue(issue.due_date)}`));return box;}
    const form=el("form","stack"),priority=labeledSelect("Mức ưu tiên",Object.entries(priorities),issue.priority||""),estimate=labeledInput("Estimate (ví dụ: 2 ngày)","text",issue.estimate||""),due=labeledInput("Ngày dự kiến hoàn thành","date",issue.due_date||"");estimate.input.maxLength=100;form.append(priority.wrap,estimate.wrap,due.wrap,submitButton("Lưu kế hoạch"));form.addEventListener("submit",async e=>{e.preventDefault();await issueMutation("plan",{priority:priority.input.value,estimate:estimate.input.value.trim(),due_date:due.input.value},form);});box.append(form);return box;
  }
  async function loadAccounts(page=1) {
    const target=$("accounts-content");if(!isFaculty()){noAccess(target);return;}state.accountPage=page;const load=beginLoad(target);const data=await api(query("/api/admin/accounts",{q:$("accounts-q").value.trim(),page}));if(!currentLoad(target,load))return;
    if(!data.items?.length)empty(target,"Không tìm thấy tài khoản","Thử từ khóa khác hoặc tạo tài khoản Admin mới.");else {const table=el("table"),head=el("thead"),row=el("tr"),body=el("tbody");["Tài khoản","Vai trò","Quyền","Trạng thái","Issue đang xử lý",""].forEach(t=>row.append(el("th","",t)));head.append(row);data.items.forEach(item=>{const tr=el("tr"),name=el("td"),permissions=el("td","account-permission-cell"),status=el("td"),actions=el("td");name.append(button(item.display_name||item.username,"text-button person-name",()=>openAccount(item.username)),el("div","secondary-line",item.username));for(const permission of item.permissions||[])permissions.append(tag(permissionNames[permission]||permission,"subtle"));status.append(tag(item.active?"Hoạt động":"Đã khóa",item.active?"":"danger"));actions.append(button("Chỉnh sửa","text-button",()=>openAccount(item.username)));tr.append(name,el("td","",item.role==="faculty"?"Giảng viên":"Admin"),permissions,status,el("td","",item.open_issues??0),actions);body.append(tr);});table.append(head,body);target.replaceChildren(table);}paging($("accounts-pagination"),data,p=>loadAccounts(p).catch(issueError));
  }
  async function openAccount(username=null) {
    if(!isFaculty())return;const user=state.user;try{const data=username?await api(query("/api/admin/account",{username})):null;if(user!==state.user)return;const account=data?.account||data;state.editingAccount=account;$("account-form").reset();$("account-error").hidden=true;$("account-title").textContent=account?"Chỉnh sửa tài khoản":"Tạo Admin";$("account-username").value=account?.username||"";$("account-username").readOnly=Boolean(account);$("account-name").value=account?.display_name||"";$("account-password").value="";$("account-password").required=!account;$("account-password-label").firstChild.textContent=account?"Mật khẩu mới (để trống để giữ nguyên)":"Mật khẩu ban đầu";$("account-role").textContent=account?.role==="faculty"?"Vai trò: Giảng viên. Việc chỉ định Giảng viên được thực hiện bằng công cụ quản trị trên server.":"Vai trò: Admin. Tài khoản này không được cấp hoặc thay đổi quyền của Admin khác.";$("account-active").checked=account?Boolean(account.active):true;Object.keys(permissionNames).forEach(p=>{$(`account-${p}`).checked=account?Boolean(account.permissions?.includes(p)):p==="issues";});renderAccountAudit(account);$("account-dialog").showModal();(account?$("account-name"):$("account-username")).focus();}catch(error){issueError(error);}
  }
  function renderAccountAudit(account) {
    const target=$("account-audit");target.replaceChildren();target.hidden=!account;if(!account)return;
    target.append(el("h3","","Lịch sử tài khoản"),el("p","secondary-line",`Issue đang phụ trách: ${account.open_issues??0} · Cập nhật ${date(account.updated)}`));
    const names={account_created:"Tạo tài khoản",account_updated:"Cập nhật tài khoản",faculty_designated:"Chỉ định Giảng viên",created:"Tạo tài khoản",updated:"Cập nhật tài khoản",create:"Tạo tài khoản",update:"Cập nhật tài khoản",faculty_granted:"Chỉ định Giảng viên",password_reset:"Đặt lại mật khẩu",password_changed:"Tự đổi mật khẩu"};
    for(const item of account.audit||[]){const row=el("div","issue-audit");row.append(el("strong","",names[item.action]||item.action),el("p","secondary-line",`${item.actor||"Hệ thống"} · ${date(item.created_at??item.created??item.at)}`));if(item.reason)row.append(el("p","",item.reason));target.append(row);}
    if(!account.audit?.length)target.append(el("p","muted","Chưa có lịch sử thay đổi."));
  }
  $("tickets-filter").addEventListener("submit",event=>{event.preventDefault();clearError();loadTickets(1).catch(issueError);});
  $("ticket-back").addEventListener("click",()=>navigate("tickets"));
  $("issues-filter").addEventListener("submit",event=>{event.preventDefault();clearError();loadIssues(1).catch(issueError);});
  $("issue-back").addEventListener("click",()=>navigate("issues"));
  $("accounts-filter").addEventListener("submit",event=>{event.preventDefault();clearError();loadAccounts(1).catch(issueError);});
  $("new-account").addEventListener("click",()=>openAccount());
  $("account-dialog").addEventListener("close",()=>{$("account-password").value="";state.editingAccount=null;});
  $("account-conversations").addEventListener("change",()=>{if(!$("account-conversations").checked)$("account-memories").checked=false;});
  $("account-memories").addEventListener("change",()=>{if($("account-memories").checked)$("account-conversations").checked=true;});
  $("account-form").addEventListener("submit",event=>{event.preventDefault();$("account-error").hidden=true;busy(event.currentTarget,async()=>{try {const old=state.editingAccount;const payload={operation:old?"update":"create",username:$("account-username").value.trim(),display_name:$("account-name").value.trim(),permissions:Object.keys(permissionNames).filter(p=>$(`account-${p}`).checked),active:$("account-active").checked,confirm:true};if(old)payload.version=old.version;const password=$("account-password").value;if(password)payload.password=password;if(!await confirmAction(old?"Cập nhật tài khoản":"Tạo tài khoản Admin",`${payload.display_name} (${payload.username})\n${payload.active?"Hoạt động":"Khóa tài khoản"} · Quyền: ${payload.permissions.map(p=>permissionNames[p]).join(", ")||"Chưa cấp quyền"}${old?"\nCác phiên đăng nhập cũ sẽ bị thu hồi.":""}`,"Xác nhận lưu",!payload.active))return;await api("/api/admin/account/save",payload);$("account-password").value="";$("account-dialog").close();toast("Đã lưu tài khoản.");if(old?.username===state.user?.username){showLogin();return;}await loadAccounts(state.accountPage);}catch(error){$("account-password").value="";notice($("account-error"),error);}});});

  $("confirmation-cancel").addEventListener("click",()=>finishConfirmation(false));
  $("confirmation-accept").addEventListener("click",()=>finishConfirmation(true));
  $("confirmation-dialog").addEventListener("cancel",event=>{event.preventDefault();finishConfirmation(false);});
  $("confirmation-dialog").addEventListener("close",()=>{if(!$("confirmation-dialog").open)finishConfirmation(false);});
  $("login-form").addEventListener("submit",event=>{event.preventDefault();$("login-error").hidden=true;busy(event.currentTarget,async()=>{try{await api("/api/session");const data=await api("/api/login",{username:$("username").value.trim(),password:$("password").value});$("password").value="";const session=data.user?data:await api("/api/session");if(!session.user)throw new Error("Đăng nhập chưa hoàn tất. Vui lòng thử lại.");showApp(session.user);}catch(e){$("password").value="";notice($("login-error"),e);}});});
  $("logout").addEventListener("click",()=>busy($("logout"),async()=>{try{await api("/api/logout",{});showLogin();}catch(e){notice($("page-error"),e);}}));
  let passwordChangeBusy=false;
  function clearPasswordFields() { ["password-current","password-new","password-confirm"].forEach(id=>{$(id).value="";}); }
  $("change-password").addEventListener("click",()=>{if(!state.user)return;clearPasswordFields();$("password-error").hidden=true;$("password-dialog").showModal();$("password-current").focus();});
  $("password-dialog").addEventListener("close",clearPasswordFields);
  $("password-form").addEventListener("submit",async event=>{
    event.preventDefault();if(passwordChangeBusy||!state.user)return;passwordChangeBusy=true;$("password-error").hidden=true;
    try { await busy(event.currentTarget,async()=>{
      const payload={current_password:$("password-current").value,new_password:$("password-new").value,confirm_password:$("password-confirm").value};
      if(payload.new_password!==payload.confirm_password)throw new Error("Hai lần nhập mật khẩu mới chưa khớp.");
      await api("/api/password/change",payload);clearPasswordFields();$("password-dialog").close();state.csrf="";showLogin();toast("Đã đổi mật khẩu. Đăng nhập lại bằng mật khẩu mới.");
    }); } catch(error){notice(state.user?$("password-error"):$("login-error"),error);} finally {clearPasswordFields();passwordChangeBusy=false;}
  });
  $("brand-home").addEventListener("click",event=>{event.preventDefault();navigate(firstView());});
  document.querySelectorAll(".nav-item").forEach(node=>node.addEventListener("click",()=>navigate(node.dataset.view)));
  $("refresh-view").addEventListener("click",()=>busy($("refresh-view"),refresh));
  $("members-filter").addEventListener("submit",event=>{event.preventDefault();clearError();loadMembers(1).catch(e=>notice($("page-error"),e));});
  $("member-back").addEventListener("click",()=>navigate("members"));
  document.querySelectorAll("#member-tabs button").forEach(node=>node.addEventListener("click",()=>{state.tab=node.dataset.tab;renderMemberTab().catch(e=>notice($("page-error"),e));}));
  $("conversations-filter").addEventListener("submit",event=>{event.preventDefault();clearError();if($("history-start").value&&$("history-end").value&&$("history-start").value>$("history-end").value){notice($("page-error"),new Error("Ngày bắt đầu phải trước hoặc cùng ngày kết thúc."));return;}loadHistory($("conversations-content"),1,historyFilters()).catch(e=>notice($("page-error"),e));});
  $("memories-filter").addEventListener("submit",event=>{event.preventDefault();clearError();loadMemories($("memories-content"),1,$("memories-member").value.trim()).catch(e=>notice($("page-error"),e));});
  $("memory-session").addEventListener("input",()=>{$("memory-share").checked=false;memoryScope();});
  $("new-memory").addEventListener("click",()=>openMemory());$("memory-scope").addEventListener("change",()=>{$("memory-share").checked=false;memoryScope();});
  document.querySelectorAll("[data-close]").forEach(node=>node.addEventListener("click",()=>$(node.dataset.close).close()));
  $("memory-form").addEventListener("submit",event=>{event.preventDefault();$("memory-error").hidden=true;busy(event.currentTarget,async()=>{try{const old=state.editingMemory;const scope=$("memory-scope").value;const payload={content:$("memory-content").value.trim(),member_id:$("memory-member").value.trim()||null,scope,active:$("memory-active").checked,expires:$("memory-expires").value?`${$("memory-expires").value}T23:59:59+07:00`:null};if(!payload.content)throw new Error("Vui lòng nhập nội dung ghi nhớ.");if(old?.id){payload.id=old.id;payload.expected_revision=old.revision;}if(old?.source_message_id)payload.source_message_id=old.source_message_id;if(scope==="session"){payload.session_key=$("memory-session").value.trim();if(payload.session_key.toUpperCase().startsWith("ZALO:GROUP:"))payload.confirm_share=$("memory-share").checked;}if(scope==="group"){payload.chat_id=$("memory-chat").value.trim();payload.confirm_share=$("memory-share").checked;}await api("/api/memory/save",payload);$("memory-dialog").close();toast("Đã lưu ghi nhớ. Thay đổi áp dụng cho lượt trả lời tiếp theo.");if(state.view==="memories"||state.tab==="memories")await refreshMemories();}catch(e){notice($("memory-error"),e);}});});
  $("link-form").addEventListener("submit",event=>{event.preventDefault();$("link-error").hidden=true;busy(event.currentTarget,async()=>{try{if(!state.linkToken){const data=await api("/api/member/link/preview",{member_id:state.member.member_id,target_account_id:$("link-target").value.trim()});state.linkToken=data.token||data.preview_token;if(!state.linkToken)throw new Error("Chưa có xác nhận hợp lệ từ máy chủ.");previewLink(data);$("link-submit").textContent="Xác nhận thay đổi";}else{await api("/api/member/link/confirm",{token:state.linkToken,confirm:true});$("link-dialog").close();toast("Đã cập nhật liên kết thành viên.");await loadMember(state.member.member_id,true);}}catch(e){notice($("link-error"),e);}});});
  async function init() { try{const session=await api("/api/session");if(session.user)showApp(session.user);else showLogin();}catch(e){$("boot-state").hidden=true;showLogin();notice($("login-error"),e);} }
  init();
})();
