"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const state = {user:null,csrf:"",view:"members",member:null,tab:"info",memberPage:1,historyPage:1,memoryPage:1,issuePage:1,ticketPage:1,accountPage:1,issue:null,ticket:null,ticketBusy:false,ticketRefreshRequired:null,editingAccount:null,operators:[],sessionKey:"",editingMemory:null,linkToken:null,request:0};
  let pendingConfirmation=null;
  let registrationRepair=null,registrationRequestBusy=false;
  let registrationReset=null,registrationResetBusy=false;
  function finishConfirmation(accepted=false) {
    const resolve=pendingConfirmation;
    pendingConfirmation=null;
    if($("confirmation-dialog").open)$("confirmation-dialog").close();
    if(resolve)resolve(accepted===true);
  }
  function confirmAction(title,message,label="Confirm",danger=false) {
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
    issues:["TEAM SUPPORT","Issue","Receive, assign and respond to member requests."],
    tickets:["CONFIRMED SHIFT EXCHANGES","Shift approval","Compare both shifts, review the reason and decide on the exchange."],
    accounts:["EXPLICIT PERMISSIONS","Administrators","Manage administrator accounts and support work."],
    members:["MEMBER MANAGEMENT","Member","Review member access and schedule links."],
    conversations:["CONVERSATION INSIGHT","Sessions","Review stored member messages and assistant replies."],
    memories:["CURATED MEMORY","Managed memory","Curate information for future conversations."]
  };
  const statusNames = {approved:"Approved",pending:"Pending",rejected:"Rejected",revoked:"Revoked",reset:"Registration reset",locked:"Locked",sent:"Sent",received:"Received",failed:"Delivery failed",uncertain:"Uncertain outcome",linked:"Linked",missing:"Not linked",conflict:"Conflict",disabled:"Not enabled"};
  const actionNames = {locked:"Lock member",unlocked:"Restore member access",tickets_link:"Update Tickets link",context_rebuilt:"Rebuild context",memory_created:"Create memory",memory_updated:"Edit memory",memory_stopped:"Deactivate memory",member_lock:"Lock member",member_unlock:"Restore member access",member_link:"Update link",lock:"Lock member",unlock:"Restore member access",link_confirm:"Update link",context_reset:"Reset context",context_rebuild:"Rebuild context",memory_create:"Create memory",memory_update:"Edit memory",memory_stop:"Deactivate memory",memory_save:"Save memory"};
  Object.assign(actionNames,{identity_corrected_from:"Revoke incorrect registration identity",identity_corrected_to:"Link verified identity",registration_context_detached:"Reset context after registration correction",memory_stopped_registration_correction:"Deactivate incorrect identity memory"});
  Object.assign(actionNames,{registration_reset:"Reset registration",identity_reset_to:"Register again after reset",memory_stopped_registration_reset:"Deactivate personal memory after registration reset"});
  const isGroup = (value) => String(value || "").toUpperCase() === "GROUP";
  const scopeNames = {personal:"Personal",session:"Conversation",group:"Group"};
  const fieldNames = {member_id:"Member ID",user_id:"User ID",zalo_user_id:"ID Zalo",chat_id:"Conversation ID",private_chat_id:"Direct chat ID",group_id:"Group ID",account_id:"Account ID",id:"ID",name:"Name",role:"Role",status:"Status",locked:"Locked",revision:"Revision",content:"Content",scope:"Scope",active:"Active",session_key:"Session ID",expires:"Expires",source_message_id:"Source message ID",create_account:"Create link",phone_masked:"Redacted phone"};
  function el(tag, className, text) { const node=document.createElement(tag); if(className) node.className=className; if(text!==undefined && text!==null) node.textContent=String(text); return node; }
  function button(text, className, handler) { const b=el("button",className,text); b.type="button"; if(handler)b.addEventListener("click",handler); return b; }
  function can(permission) { return Boolean(state.user && Array.isArray(state.user.permissions) && state.user.permissions.includes(permission) && (permission!=="memories" || state.user.permissions.includes("conversations"))); }
  function isFaculty() { return state.user?.role==="faculty"; }
  function canView(view) { return view==="accounts"?isFaculty():view==="issues"?isFaculty()||can("issues"):can(view); }
  function firstView() { return ["issues","tickets","members","conversations","memories","accounts"].find(canView)||"members"; }
  function textValue(value, fallback="—") { return value===undefined || value===null || value==="" ? fallback : String(value); }
  function date(value) { if(value===undefined || value===null || value==="")return "—"; const v=typeof value==="number" ? new Date(value<1e12?value*1000:value) : new Date(value); return Number.isNaN(v.getTime())?"—":new Intl.DateTimeFormat("en-US",{dateStyle:"short",timeStyle:"short",timeZone:"Asia/Ho_Chi_Minh"}).format(v); }
  function isoDay(value) { if(!value)return ""; const d=typeof value==="number"?new Date(value*1000):new Date(value); if(Number.isNaN(d.getTime()))return ""; return new Intl.DateTimeFormat("en-CA",{year:"numeric",month:"2-digit",day:"2-digit",timeZone:"Asia/Ho_Chi_Minh"}).format(d); }
  function initials(name) { return String(name||"TV").trim().split(/\s+/).slice(-2).map(x=>x[0]||"").join("").toLocaleUpperCase("en-US"); }
  function tag(text,kind="") { return el("span",`tag ${kind}`.trim(),text); }
  function statusTag(status) { return tag(statusNames[status]||textValue(status),["rejected","revoked","locked","failed","conflict"].includes(status)?"danger":["pending","missing","uncertain"].includes(status)?"warning":["disabled"].includes(status)?"subtle":""); }
  function messageOf(error) { return error instanceof Error?error.message:"The action could not be completed. Please try again."; }
  function notice(target,error) { target.textContent=messageOf(error); target.hidden=false; }
  function clearError() { $("page-error").hidden=true; $("page-error").textContent=""; }
  let toastTimer;
  function toast(text) { $("toast").textContent=text; $("toast").hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>{$("toast").hidden=true;},4500); }
  function loading(target,text="Loading records…") { target.replaceChildren(el("div","loading-state",text)); }
  function empty(target,title,description="",symbol="◇") { const box=el("div","empty-state"); box.append(el("span","empty-symbol",symbol),el("strong","",title)); if(description)box.append(el("p","",description)); target.replaceChildren(box); }
  function noAccess(target) { empty(target,"You do not have access to this section","Private conversation access is granted separately from member management.","⊘"); }
  function panel() { return el("div","panel"); }
  async function api(path,body) {
    const options={method:body===undefined?"GET":"POST",credentials:"same-origin",cache:"no-store",headers:{Accept:"application/json"}};
    if(body!==undefined){options.headers["Content-Type"]="application/json";options.headers["X-CSRF-Token"]=state.csrf;options.body=JSON.stringify(body);}
    let response;
    try{response=await fetch(path,options);}catch(_error){throw new Error("Cannot reach the server. Check your connection and try again.");}
    let data;
    try{data=await response.json();}catch(_error){throw new Error("The server returned invalid data. Please try again.");}
    if(data.csrf)state.csrf=data.csrf;
    if(data.mode)$("mode-banner").hidden=data.mode!=="demo";
    if(!response.ok){if(response.status===401 && path!=="/api/login")showLogin();const error=new Error(typeof data.error==="string"?data.error:"This action could not be completed.");error.status=response.status;throw error;}
    return data;
  }
  function query(path,values) { const params=new URLSearchParams(); Object.entries(values).forEach(([key,value])=>{if(value!==undefined && value!==null && value!=="")params.set(key,String(value));});return `${path}?${params}`; }
  async function busy(node,task) { const controls=node.matches("button")?[node]:Array.from(node.querySelectorAll("button[type=submit]"));controls.forEach(b=>{b.disabled=true;});try{return await task();}finally{controls.forEach(b=>{b.disabled=false;});} }
  function paging(target,data,callback) {
    target.replaceChildren(); target.classList.add("pagination"); const page=Number(data.page)||1;const limit=Number(data.page_size??data.limit)||20;const total=Number(data.total)||0;const pages=Math.max(1,Math.ceil(total/limit));
    target.append(el("span","",total?`${(page-1)*limit+1}–${Math.min(page*limit,total)} / ${total} results`:"0 results"));
    const controls=el("div","pagination-controls");const previous=button("←","",()=>callback(page-1));previous.disabled=page<=1;previous.setAttribute("aria-label","Previous page");const next=button("→","",()=>callback(page+1));next.disabled=page>=pages;next.setAttribute("aria-label","Trang sau");controls.append(previous,el("span","",`Trang ${page} / ${pages}`),next);target.append(controls);
  }
  function pagedShell(target,data,render,callback) { const stack=el("div","stack"); if(data.items && data.items.length)data.items.forEach(item=>stack.append(render(item)));else empty(stack,"No matching records","Adjust the filters or check again later.");const pager=el("div","pagination");paging(pager,data,callback);target.replaceChildren(stack,pager); }
  function showLogin() { finishConfirmation(false);clearRegistrationRepair();clearRegistrationReset();state.user=null;state.member=null;state.issue=null;state.ticket=null;state.ticketBusy=false;state.ticketRefreshRequired=null;state.editingAccount=null;state.operators=[];state.sessionKey="";state.editingMemory=null;state.linkToken=null;state.request++;document.querySelectorAll("dialog[open]").forEach(d=>d.close());["members-content","member-profile","member-tab-content","conversations-content","memories-content","source-content","link-preview","memory-source","issues-content","issue-content","tickets-content","ticket-content","accounts-content","account-audit"].forEach(id=>$(id).replaceChildren());$("tickets-nav-count").hidden=true;$("memory-form").reset();$("account-form").reset();$("app-view").hidden=true;$("login-view").hidden=false;$("boot-state").hidden=true;$("password").value="";$("username").focus(); }
  function showApp(user) { state.user=user;$("admin-name").textContent=user.display_name||user.username||"Admin";$("admin-role").textContent=isFaculty()?"System owner":"Admin";$("login-view").hidden=true;$("app-view").hidden=false;$("boot-state").hidden=true;document.querySelectorAll(".nav-item").forEach(node=>{node.hidden=!canView(node.dataset.view);});navigate(firstView());if(firstView()!=="tickets")refreshTicketCount(); }
  async function navigate(view) { state.view=view;state.member=null;state.issue=null;state.ticket=null;state.sessionKey="";state.request++;clearError();["members","member","conversations","memories","issues","issue","tickets","ticket","accounts"].forEach(name=>{$(`${name}-view`).hidden=name!==view;});document.querySelectorAll(".nav-item").forEach(node=>node.classList.toggle("active",node.dataset.view===view));const info=viewInfo[view];$("page-eyebrow").textContent=info[0];$("page-title").textContent=info[1];$("page-description").textContent=info[2];$("breadcrumb-current").textContent=info[1];await refresh(); }
  async function refresh() { clearError();try{if(state.member)await loadMember(state.member.member_id,true);else if(state.issue)await loadIssue(state.issue.id);else if(state.ticket)await loadTicket(state.ticket.id);else if(state.view==="tickets")await loadTickets(state.ticketPage);else if(state.view==="issues")await loadIssues(state.issuePage);else if(state.view==="accounts")await loadAccounts(state.accountPage);else if(state.view==="members")await loadMembers(state.memberPage);else if(state.view==="conversations")await loadHistory($("conversations-content"),state.historyPage,historyFilters());else await loadMemories($("memories-content"),state.memoryPage,$("memories-member").value.trim());}catch(error){notice($("page-error"),error);} }

  async function loadMembers(page=1) {
    const target=$("members-content");if(!can("members")){noAccess(target);$("members-pagination").replaceChildren();return;}state.memberPage=page;const load=beginLoad(target);const data=await api(query("/api/members",{q:$("members-q").value.trim(),status:$("members-status").value,link:$("members-link").value,page}));if(!currentLoad(target,load))return;$("member-total").textContent=textValue(data.total,"0");
    if(!data.items?.length)empty(target,"No matching members","Members register in a direct message to the bot. Try adjusting the filters.","◫");else{const table=el("table");const head=el("thead");const row=el("tr");["Member","Status","Schedule link","Updated",""].forEach(t=>row.append(el("th","",t)));head.append(row);const body=el("tbody");data.items.forEach(member=>{const tr=el("tr");const person=el("td");const cell=el("div","person-cell");const nameBox=el("div");const name=button(member.name||member.member_id,"text-button person-name",()=>openMember(member.member_id));nameBox.append(name,el("div","secondary-line",member.member_id));cell.append(el("div","avatar",initials(member.name)),nameBox);person.append(cell);const status=el("td");status.append(statusTag(member.locked?"locked":member.status));const link=el("td");link.append(statusTag(member.link_status||"missing"));if(member.schedule_name)link.append(el("div","secondary-line",member.schedule_name));const action=el("td");action.append(button("Xem →","text-button",()=>openMember(member.member_id)));tr.append(person,status,link,el("td","muted",date(member.updated_at||member.updated)),action);body.append(tr);});table.append(head,body);target.replaceChildren(table);}
    paging($("members-pagination"),data,p=>loadMembers(p).catch(e=>notice($("page-error"),e)));
  }
  async function openMember(id) { state.tab="info";state.sessionKey="";await loadMember(id).catch(e=>notice($("page-error"),e)); }
  async function loadMember(id,preserve=false) {
    clearError();$("members-view").hidden=true;$("member-view").hidden=false;$("page-title").textContent="Member details";$("page-description").textContent="A member's identity, account links and conversation records.";$("breadcrumb-current").textContent="Members / Details";const target=$("member-profile");const load=beginLoad(target);$("member-tab-content").replaceChildren();const data=await api(query("/api/member",{id}));if(!currentLoad(target,load)||$("member-view").hidden)return;state.member=data.member||data;renderProfile();await renderMemberTab();
  }
  function renderProfile() { const member=state.member;const info=el("div");info.append(el("h2","",member.name||member.member_id),el("p","secondary-line",member.member_id));const tags=el("div","profile-tags");tags.append(statusTag(member.locked?"locked":member.status),statusTag(member.link_status||"missing"));info.append(tags);const actions=el("div","profile-actions");const lock=button(member.locked?"Restore access":"Lock access",`button ${member.locked?"secondary":"danger"}`,()=>changeLock(lock));if(can("members")){const reset=button("Reset registration","button danger",openRegistrationReset);reset.disabled=!canResetRegistration(member);if(member.locked)reset.title="Resolve the access lock before resetting this registration.";actions.append(button("Correct registration","button secondary",openRegistrationRepair),button("Resolve link","button secondary",openLink),lock,reset);}$("member-profile").replaceChildren(el("div","avatar large",initials(member.name)),info,actions);document.querySelectorAll("#member-tabs button").forEach(node=>{node.hidden=(node.dataset.tab==="history"||node.dataset.tab==="context")?!can("conversations"):node.dataset.tab==="memories"?!can("memories"):false;}); }
  function detailPanel(title,rows) { const box=el("article","panel detail-panel");box.append(el("h2","",title));const dl=el("dl","details-list");rows.forEach(([label,value])=>{const row=el("div","detail-row");row.append(el("dt","",label),el("dd","",textValue(value)));dl.append(row);});box.append(dl);return box; }
  async function renderMemberTab() {
    const target=$("member-tab-content");loads.set(target,{});clearError();document.querySelectorAll("#member-tabs button").forEach(node=>node.classList.toggle("active",node.dataset.tab===state.tab));const m=state.member;if(!m)return;
    if(state.tab==="info"){const grid=el("div","detail-grid");grid.append(detailPanel("Registration details",[["Member ID",m.member_id],["Full name",m.name],["Phone",m.phone_masked],["Status",statusNames[m.status]||m.status],["Registered",date(m.created_at||m.created)],["ID Zalo",m.zalo_user_id||m.user_id],["Direct chat ID",m.private_chat_id||m.chat_id]]),detailPanel("Schedule & Tickets links",[["Schedule name",m.schedule_name],["Link",statusNames[m.link_status]||m.link_status],["Details",m.link_reason],["Tickets account",m.tickets_account?.name],["Account ID",m.tickets_account?.id],["Tickets member ID",m.tickets_account?.member_id],["Group",m.group_note||"Group access is managed separately."]]));target.replaceChildren(grid);return;}
    if(state.tab==="history")return loadHistory(target,1,{member_id:m.member_id});
    if(state.tab==="context")return loadSessionsContext(target,m.member_id);
    if(state.tab==="memories")return loadMemories(target,1,m.member_id,true);
    return loadMemberAudit(target,m.member_id);
  }
  async function changeLock(node) { const m=state.member;const locked=!m.locked;const message=locked?`Lock access for ${m.name||m.member_id}? The member will lose access to internal features. Existing records and history are retained.`:`Restore access for ${m.name||m.member_id}? This does not grant administrator or new group permissions.`;if(!await confirmAction(locked?"Lock member access":"Restore member access",message,locked?"Confirm access lock":"Confirm access restoration",locked))return;await busy(node,async()=>{try{await api("/api/member/lock",{member_id:m.member_id,locked,expected_revision:m.revision,confirm:true});toast(locked?"Member access locked.":"Member access restored.");if(state.member?.member_id===m.member_id)await loadMember(m.member_id,true);}catch(e){notice($("page-error"),e);}}); }
  function openLink() { state.linkToken=null;$("link-error").hidden=true;$("link-preview").hidden=true;$("link-preview").replaceChildren();$("link-submit").textContent="Preview changes";const previous=$("link-target");let input;
    if(state.member.candidates?.length){input=el("select");input.append(el("option","","Select an account"));input.firstChild.value="";state.member.candidates.forEach(c=>{const option=el("option","",`${c.name||c.member_id} · ${c.account_id}${c.create_account?" · create new":""}`);option.value=c.account_id;input.append(option);});}else{input=el("input");input.maxLength=200;input.placeholder="Target account ID";}
    input.id="link-target";input.required=true;previous.replaceWith(input);$("link-dialog").showModal();input.focus();input.addEventListener("change",()=>{state.linkToken=null;$("link-submit").textContent="Preview changes";$("link-preview").hidden=true;}); }
  function pretty(value) { if(value===null||value===undefined)return "—";if(Array.isArray(value))return value.length?value.map(pretty).join("\n\n"):"None";if(typeof value==="object")return Object.entries(value).filter(([k])=>!/(token|password|secret|phone(?!_masked))/i.test(k)).map(([k,v])=>`${fieldNames[k]||k}: ${pretty(v)}`).join("\n");if(typeof value==="boolean")return value?"Yes":"No";return String(value); }
  function previewLink(data) { const box=el("div","preview-block");box.append(el("h3","","Change preview"));if(data.message)box.append(el("p","",data.message));box.append(el("h3","","Current"),el("pre","",pretty(data.before)),el("h3","","After confirmation"),el("pre","",pretty(data.after)));$("link-preview").replaceChildren(box);$("link-preview").hidden=false; }

  function clearRegistrationRepair() {
    registrationRepair=null;
    ["registration-repair-phone","registration-repair-reason"].forEach(id=>{$(id).value="";$(id).disabled=false;});
    $("registration-repair-verified").checked=false;$("registration-repair-verified").required=false;
    $("registration-repair-preview").replaceChildren();$("registration-repair-preview").hidden=true;
    $("registration-repair-fields").hidden=false;$("registration-repair-check-wrap").hidden=true;
    $("registration-repair-error").hidden=true;$("registration-repair-error").textContent="";
    $("registration-repair-reload").hidden=true;$("registration-repair-submit").hidden=false;
    $("registration-repair-submit").disabled=false;$("registration-repair-submit").textContent="Preview changes";
  }
  function openRegistrationRepair() {
    if(!can("members")||!state.member||registrationRequestBusy||registrationResetBusy)return;
    clearRegistrationRepair();registrationRepair={user:state.user,memberId:state.member.member_id,token:null};
    $("registration-repair-dialog").showModal();$("registration-repair-phone").focus();
  }
  function currentRegistrationRepair(operation) { return operation===registrationRepair&&state.user===operation.user&&state.member?.member_id===operation.memberId&&$("registration-repair-dialog").open; }
  function renderRegistrationPreview(data) {
    const grid=el("div","registration-repair-grid");
    for(const [title,value] of [["Current",data.before],["After correction",data.after]]){
      const identity=value||{};
      grid.append(detailPanel(title,[["Full name",identity.name],["Phone",identity.phone_masked],["Member ID",identity.member_id||"Assigned permanently on confirmation"],["ID Zalo",identity.zalo_user_id||data.before?.zalo_user_id],["Direct chat ID",identity.private_chat_id||data.before?.private_chat_id]]));
    }
    const target=$("registration-repair-preview");target.replaceChildren(grid);if(data.message)target.append(el("p","notice warning",data.message));target.hidden=false;
    $("registration-repair-fields").hidden=true;["registration-repair-phone","registration-repair-reason"].forEach(id=>{$(id).disabled=true;});
    $("registration-repair-check-wrap").hidden=false;$("registration-repair-verified").required=true;
    $("registration-repair-submit").textContent="Confirm correction";$("registration-repair-verified").focus();
  }
  async function submitRegistrationRepair(event) {
    event.preventDefault();const operation=registrationRepair;
    if(!operation||operation.failed||registrationRequestBusy||!can("members")||!currentRegistrationRepair(operation))return;
    registrationRequestBusy=true;$("registration-repair-submit").disabled=true;$("registration-repair-error").hidden=true;
    const confirming=Boolean(operation.token);
    try {
      if(!confirming){
        const payload={member_id:operation.memberId,phone:$("registration-repair-phone").value.trim(),reason:$("registration-repair-reason").value.trim()};
        $("registration-repair-phone").value="";
        ["registration-repair-phone","registration-repair-reason"].forEach(id=>{$(id).disabled=true;});
        if(!payload.phone||!payload.reason)throw new Error("Enter the correct Workload phone number and explain the correction.");
        const data=await api("/api/member/registration/preview",payload);
        if(!currentRegistrationRepair(operation))return;
        if(typeof data.preview_token!=="string"||!data.preview_token||!data.before||!data.after)throw new Error("A valid preview was not returned. Enter the details again to verify.");
        operation.token=data.preview_token;renderRegistrationPreview(data);
      }else{
        if(!$("registration-repair-verified").checked)throw new Error("Confirm that you have verified the Zalo account owner.");
        const preview_token=operation.token;operation.token=null;
        const data=await api("/api/member/registration/confirm",{preview_token,confirm:true});
        if(!currentRegistrationRepair(operation))return;
        const memberId=data.member_id||data.member?.member_id;
        if(!memberId)throw new Error("The updated registration record was not returned.");
        $("registration-repair-dialog").close();clearRegistrationRepair();
        toast(data.message||"Registration corrected. The member can use the bot with the verified identity.");
        await loadMember(memberId,true).catch(error=>notice($("page-error"),error));
      }
    }catch(error){
      if(!currentRegistrationRepair(operation))return;
      if(confirming&&operation.token===null){
        operation.failed=true;
        $("registration-repair-submit").hidden=true;$("registration-repair-reload").hidden=false;
        $("registration-repair-verified").checked=false;$("registration-repair-verified").required=false;
        notice($("registration-repair-error"),new Error(`${messageOf(error)} Reload the profile and check the result before trying again.`));
      }else notice($("registration-repair-error"),error);
    }finally{
      registrationRequestBusy=false;
      if(currentRegistrationRepair(operation)){$("registration-repair-phone").value="";$("registration-repair-submit").disabled=false;if(!confirming&&!operation.token)["registration-repair-phone","registration-repair-reason"].forEach(id=>{$(id).disabled=false;});}
    }
  }

  function canResetRegistration(member) { return Boolean(member&&!member.locked&&member.status!=="reset"&&member.request_id&&Number.isInteger(member.revision)); }
  function clearRegistrationReset() {
    registrationReset=null;$("registration-reset-identity").replaceChildren();
    $("registration-reset-reason").value="";$("registration-reset-reason").disabled=false;
    $("registration-reset-verified").checked=false;$("registration-reset-verified").disabled=false;
    $("registration-reset-error").hidden=true;$("registration-reset-error").textContent="";
    $("registration-reset-submit").disabled=false;$("registration-reset-submit").hidden=false;$("registration-reset-reload").hidden=true;
  }
  function openRegistrationReset() {
    if(!can("members")||!canResetRegistration(state.member)||registrationResetBusy||registrationRequestBusy)return;
    clearRegistrationReset();const member=state.member;
    registrationReset={user:state.user,memberId:member.member_id,requestId:member.request_id,revision:member.revision};
    $("registration-reset-identity").append(detailPanel("Registration to reset",[["Full name",member.name],["Phone",member.phone_masked],["Member ID",member.member_id]]));
    $("registration-reset-dialog").showModal();$("registration-reset-reason").focus();
  }
  function currentRegistrationReset(operation) { return operation===registrationReset&&state.user===operation.user&&state.member?.member_id===operation.memberId&&$("registration-reset-dialog").open; }
  async function submitRegistrationReset(event) {
    event.preventDefault();const operation=registrationReset;
    if(!operation||operation.failed||registrationResetBusy||!can("members")||!canResetRegistration(state.member)||!currentRegistrationReset(operation))return;
    const reason=$("registration-reset-reason").value.trim();$("registration-reset-error").hidden=true;
    if(reason.length<5||reason.length>300){notice($("registration-reset-error"),new Error("Enter a reason of 5–300 characters without including a phone number."));return;}
    if(!$("registration-reset-verified").checked){notice($("registration-reset-error"),new Error("Confirm that this is the member whose registration should be reset."));return;}
    registrationResetBusy=true;["registration-reset-submit","registration-reset-reason","registration-reset-verified"].forEach(id=>{$(id).disabled=true;});
    try {
      const data=await api("/api/member/registration/reset",{member_id:operation.memberId,request_id:operation.requestId,expected_revision:operation.revision,reason,confirm:true});
      if(!currentRegistrationReset(operation))return;
      if(data.ok!==true)throw new Error("The server has not confirmed the action.");
      $("registration-reset-dialog").close();clearRegistrationReset();
      toast(data.message||"Registration reset. The member can send /dangky PHONE_NUMBER to the bot to register again.");
      await loadMembers(state.memberPage).catch(error=>notice($("page-error"),error));
      if(state.user===operation.user&&state.member?.member_id===operation.memberId)await loadMember(operation.memberId,true).catch(error=>notice($("page-error"),error));
    }catch(error){
      if(!currentRegistrationReset(operation))return;
      operation.failed=true;$("registration-reset-submit").hidden=true;$("registration-reset-reload").hidden=false;
      notice($("registration-reset-error"),new Error(`${messageOf(error)} Reload the profile and check the result before trying again.`));
    }finally{
      registrationResetBusy=false;
      if(currentRegistrationReset(operation))$("registration-reset-submit").disabled=false;
    }
  }

  function historyFilters() { return {member_id:$("history-member").value.trim(),chat_id:$("history-chat").value.trim(),start:$("history-start").value,end:$("history-end").value}; }
  async function loadHistory(target,page=1,filters={}) { if(!can("conversations")){noAccess(target);return;}const load=beginLoad(target);if(target.id==="conversations-content")state.historyPage=page;const data=await api(query("/api/history",{...filters,page}));if(!currentLoad(target,load))return;pagedShell(target,data,historyCard,p=>loadHistory(target,p,filters).catch(e=>notice($("page-error"),e))); }
  function historyCard(item) { const isBot=item.role==="assistant"||item.role==="bot"||item.direction==="outgoing";const card=el("article",`panel history-item${isBot?" bot":""}`);const header=el("div","history-header");header.append(el("strong","",isBot?"LabCoach":item.member_name||item.member_id||"Member"),tag(isGroup(item.chat_type)?"Group":"Direct message","subtle"));if(item.status)header.append(statusTag(item.status));header.append(el("time","",date(item.created_at??item.created)));const footer=el("div","history-footer");footer.append(el("span","",`Conversation: ${textValue(item.chat_id)} · Message: ${textValue(item.id)}`));if(can("memories")&&item.member_id)footer.append(button("+ Create memory from this message","text-button",()=>openMemory(null,item)));card.append(header,el("div","history-body",item.content??item.text??item.body??"(No text content)"),footer);return card; }
  async function loadSessionsContext(target,memberId,page=1) { if(!can("conversations")){noAccess(target);return;}const load=beginLoad(target);const data=await api(query("/api/sessions",{member_id:memberId,page}));if(!currentLoad(target,load))return;if(!data.items?.length){empty(target,"No stored context","Context appears when the bot processes this member's conversations.");return;}
    const toolbar=el("div","section-toolbar");const label=el("label","","Select conversation");const select=el("select");data.items.forEach(session=>{const option=el("option","",`${isGroup(session.chat_type)?"Group":"Direct message"} · ${textValue(session.chat_id)} · ${date(session.updated_at??session.updated)}`);option.value=session.session_key;select.append(option);});if(data.items.some(x=>x.session_key===state.sessionKey))select.value=state.sessionKey;else state.sessionKey=select.value;label.append(select);toolbar.append(label);const contextTarget=el("div");const pager=el("div","pagination");paging(pager,data,p=>loadSessionsContext(target,memberId,p).catch(e=>notice($("page-error"),e)));target.replaceChildren(toolbar,contextTarget,pager);select.addEventListener("change",()=>{state.sessionKey=select.value;loadContext(contextTarget,select.value).catch(e=>notice($("page-error"),e));});await loadContext(contextTarget,select.value);
  }
  async function loadContext(target,sessionKey) {
    const load=beginLoad(target);const data=await api(query("/api/context",{session_key:sessionKey}));if(!currentLoad(target,load))return;const box=el("div","panel detail-panel");const heading=el("div","section-toolbar");heading.append(el("h2","","Session context"));const controls=el("div","profile-actions");const rebuild=button("Rebuild context","button secondary small",()=>changeContext("rebuild",rebuild,target,sessionKey,data.revision));const reset=button("Reset context","button danger small",()=>changeContext("reset",reset,target,sessionKey,data.revision));controls.append(rebuild,reset);heading.append(controls);box.append(heading,el("p","muted",`Revision ${textValue(data.revision,"0")} · Applies only to the selected conversation.`));if(data.truncated)box.append(el("p","notice info","Context was shortened for processing. The excerpts below are the selected context; full content remains in conversation history."));const summary=el("section","context-section");summary.append(el("h3","","Summary"),el("p","context-summary",data.summary||"No summary yet. The bot may use the recent messages below."));box.append(summary);const messages=el("section","context-section");messages.append(el("h3","","Recent messages selected for the next turn"));if(data.messages?.length)data.messages.forEach(m=>{const part=el("div","mini-message");part.append(el("strong","",m.role==="assistant"||m.direction==="outgoing"?"LabCoach":"Member"),el("span","",m.content??m.body??m.text??""));messages.append(part);});else messages.append(el("p","muted","No messages in context."));box.append(messages);const memories=el("section","context-section");memories.append(el("h3","","Selected memories for this session"));if(data.memories_redacted)memories.append(el("p","muted","Memory is hidden because this account does not have permission to view it."));else if(data.memories?.length)data.memories.forEach(m=>{const part=el("div","mini-message");part.append(el("strong","",scopeNames[m.scope]||"Memory"),el("span","",m.content));memories.append(part);});else memories.append(el("p","muted","No matching active memories."));box.append(memories);if(data.last_prepared){const p=el("p","secondary-line",`Last context preparation: ${date(data.last_prepared.prepared_at??data.last_prepared.created_at??data.last_prepared.created??data.last_prepared.at)}. The list above is the current context for the next turn.`);box.append(p);}target.replaceChildren(box);
  }
  async function changeContext(action,node,target,key,revision) { const message=action==="reset"?"Reset this session's context? Conversation history and long-term memory remain. The change applies to the next reply.":"Rebuild context from permitted messages? The previous summary will be replaced.";if(!await confirmAction(action==="reset"?"Reset context":"Rebuild context",message,action==="reset"?"Confirm context reset":"Confirm rebuild",action==="reset"))return;await busy(node,async()=>{try{await api(`/api/context/${action}`,{session_key:key,expected_revision:revision,confirm:true});toast(action==="reset"?"Session context reset.":"Context rebuilt.");await loadContext(target,key);}catch(e){notice($("page-error"),e);}}); }

  async function loadMemories(target,page=1,memberId="",toolbar=false) { if(!can("memories")){noAccess(target);return;}const load=beginLoad(target);if(target.id==="memories-content")state.memoryPage=page;const data=await api(query("/api/memories",{member_id:memberId,page}));if(!currentLoad(target,load))return;const content=el("div");pagedShell(content,data,memoryCard,p=>loadMemories(target,p,memberId,toolbar).catch(e=>notice($("page-error"),e)));if(toolbar){const controls=el("div","section-toolbar");controls.append(el("p","muted","Active and inactive memories."),button("+ Add memory","button primary",()=>openMemory()));target.replaceChildren(controls,content);}else target.replaceChildren(content); }
  function memoryCard(memory) { const active=Boolean(memory.active);const expiry=memory.expires?(typeof memory.expires==="number"?memory.expires*1000:Date.parse(memory.expires)):null;const expired=Boolean(expiry&&expiry<=Date.now());const card=el("article",`panel memory-card${active&&!expired?"":" inactive"}`);const header=el("div","memory-header");header.append(tag(scopeNames[memory.scope]||memory.scope,"subtle"),tag(expired?"Expired":active?"Active":"Inactive",active&&!expired?"":"warning"),el("time","",`Updated ${date(memory.updated_at??memory.updated??memory.created_at??memory.created)}`));card.append(header,el("p","memory-content",memory.content));const meta=el("div","memory-meta");meta.append(el("span","",`Member: ${textValue(memory.member_id)}`));if(memory.chat_id||memory.group_id)meta.append(el("span","",`Group: ${memory.chat_id||memory.group_id}`));if(memory.expires)meta.append(el("span","",`Expires: ${date(memory.expires)}`));meta.append(el("span","",`Revision ${textValue(memory.revision,"1")}`));card.append(meta);const actions=el("div","memory-actions");if(memory.source_message_id)actions.append(button(memory.source_expired?"Source retention expired":"View source message","text-button",()=>showSource(memory)));actions.append(button("Revision history","text-button",()=>showMemoryVersions(memory)),button("Edit","text-button",()=>openMemory(memory)));if(active){const stop=button("Stop using","text-button",()=>stopMemory(memory,stop));actions.append(stop);}card.append(actions);return card; }
  function openMemory(memory=null,source=null) {
    state.editingMemory=memory?{...memory}:source?{source_message_id:source.id,session_key:source.session_key,chat_id:source.chat_id,member_id:source.member_id}:null;
    $("memory-form").reset();$("memory-active").checked=memory?Boolean(memory.active):true;$("memory-error").hidden=true;$("memory-dialog-title").textContent=memory?"Edit memory":"Add memory";$("memory-content").value=memory?.content||"";$("memory-member").value=memory?.member_id||source?.member_id||state.member?.member_id||$("memories-member").value.trim();$("memory-scope").value=memory?.scope||"personal";$("memory-session").value=memory?.session_key||source?.session_key||"";$("memory-chat").value=memory?.chat_id||memory?.group_id||(isGroup(source?.chat_type)?source.chat_id:"")||"";$("memory-expires").value=isoDay(memory?.expires);const sourceId=memory?.source_message_id||source?.id;$("memory-source").hidden=!sourceId;if(sourceId)$("memory-source").textContent=`Source message: ${sourceId}${source?". Summarize useful information in the content field; do not copy the entire message.":""}`;memoryScope();$("memory-dialog").showModal();$("memory-content").focus();
  }
  function memoryScope() { const scope=$("memory-scope").value;const shared=scope==="group" || (scope==="session" && $("memory-session").value.trim().toUpperCase().startsWith("ZALO:GROUP:"));$("memory-member").required=scope==="personal";$("memory-session-wrap").hidden=scope!=="session";$("memory-chat-wrap").hidden=scope!=="group";$("memory-session").required=scope==="session";$("memory-chat").required=scope==="group";$("memory-share-warning").hidden=!shared;$("memory-share-wrap").hidden=!shared;$("memory-share").required=shared; }
  async function refreshMemories() { if(state.member)await renderMemberTab();else await loadMemories($("memories-content"),state.memoryPage,$("memories-member").value.trim()); }
  async function stopMemory(memory,node) { if(!await confirmAction("Deactivate memory","The bot will stop selecting this memory for future replies. Change history is retained.","Confirm deactivation",true))return;await busy(node,async()=>{try{await api("/api/memory/stop",{id:memory.id,expected_revision:memory.revision,confirm:true});toast("Memory deactivated.");await refreshMemories();}catch(e){notice($("page-error"),e);}}); }
  async function showSource(memory) { if(!can("conversations")){toast("Conversation permission is required to view the source message.");return;}$("source-dialog").querySelector("h2").textContent="Source message";$("source-dialog").showModal();loading($("source-content"));try{const data=await api(query("/api/history",{message_id:memory.source_message_id,page:1}));const source=data.items?.find(item=>String(item.id)===String(memory.source_message_id));if(source)$("source-content").replaceChildren(historyCard(source));else empty($("source-content"),"Source message unavailable","The source may have expired or may be outside your access scope.");}catch(e){empty($("source-content"),"Cannot view source message",messageOf(e));} }
  async function showMemoryVersions(memory) { $("source-dialog").querySelector("h2").textContent="Memory change history";$("source-dialog").showModal();loading($("source-content"));try{const data=await api(query("/api/memory/versions",{id:memory.id}));if(!data.items?.length){empty($("source-content"),"No change history yet");return;}const stack=el("div","stack");data.items.forEach(version=>{const box=el("article","panel detail-panel");box.append(el("h3","",`Revision ${version.revision}`),el("p","secondary-line",`${textValue(version.actor)} · ${date(version.created_at??version.created)}`),el("pre","audit-detail",pretty(version.snapshot??version)));stack.append(box);});$("source-content").replaceChildren(stack);}catch(e){empty($("source-content"),"Cannot load history",messageOf(e));} }
  async function loadMemberAudit(target,memberId) {
    const controls=el("div","section-toolbar");const label=el("label","","Change type");const select=el("select");const memberOption=el("option","","Member access & links");memberOption.value="members";select.append(memberOption);
    if(can("memories")){const conversationOption=el("option","","Context & memory");conversationOption.value="conversations";select.append(conversationOption);}
    label.append(select);controls.append(label);const content=el("div");target.replaceChildren(controls,content);const load=()=>loadAudit(content,1,{member_id:memberId,kind:select.value});select.addEventListener("change",()=>load().catch(e=>notice($("page-error"),e)));await load();
  }
  async function loadAudit(target,page=1,filters={}) { const load=beginLoad(target);const data=await api(query("/api/audit",{...filters,page}));if(!currentLoad(target,load))return;const box=panel();if(data.items?.length)data.items.forEach(item=>{const row=el("article","audit-entry");const header=el("div","audit-header");header.append(el("strong","",actionNames[item.action]||item.action),el("time","",date(item.created_at??item.created??item.at)));row.append(header,el("p","",`Actor: ${textValue(item.actor)}`));if(item.before||item.after)row.append(el("div","audit-detail",`Before: ${pretty(item.before)}\n\nAfter: ${pretty(item.after)}`));else if(item.detail||item.details)row.append(el("div","audit-detail",pretty(item.detail||item.details)));box.append(row);});else empty(box,"No changes yet","Administrator actions will be recorded here.");const pager=el("div","pagination");paging(pager,data,p=>loadAudit(target,p,filters).catch(e=>notice($("page-error"),e)));target.replaceChildren(box,pager); }

  const ticketStatusNames={pending_peer:"Awaiting partner",pending_manager:"Awaiting approval",applying:"Updating schedule",uncertain:"Needs reconciliation",completed:"Exchanged",rejected:"Rejected",cancelled:"Canceled",expired:"Expired",stale:"Schedule or access changed"};
  function ticketStatusTag(value) { return tag(ticketStatusNames[value]||textValue(value),["pending_peer","pending_manager","applying","uncertain"].includes(value)?"warning":["rejected","stale"].includes(value)?"danger":["expired","cancelled"].includes(value)?"subtle":""); }
  function ticketSchedule(shift) { return shift?`${date(shift.start)} – ${date(shift.end)}`:"Shift details unavailable"; }
  function ticketLocation(shift) { return [shift?.course,shift?.room&&`Room ${shift.room}`,shift?.period].filter(Boolean).join(" · ")||"Subject and room unavailable"; }
  function renderTicketCount(data) {
    const count=Number(data.summary?.pending_manager)||0;
    $("ticket-pending-total").textContent=String(count);
    $("tickets-nav-count").textContent=String(count);
    $("tickets-nav-count").hidden=!count||data.enabled===false;
    $("tickets-nav-count").setAttribute("aria-label",`${count} requests awaiting approval`);
  }
  async function refreshTicketCount() {
    if(!can("tickets"))return;
    const user=state.user;
    try{const data=await api(query("/api/tickets",{status:"pending_manager",page:1}));if(state.user===user)renderTicketCount(data);}
    catch(_error){if(state.user===user)$("tickets-nav-count").hidden=true;}
  }
  async function loadTickets(page=1) {
    const target=$("tickets-content");if(!can("tickets")){empty(target,"You cannot approve shift exchanges","The system owner must explicitly grant shift-exchange approval permission to this account.","⊘");return;}
    state.ticketPage=page;const load=beginLoad(target);$("tickets-source-notice").hidden=true;$("tickets-pagination").replaceChildren();
    const data=await api(query("/api/tickets",{q:$("tickets-q").value.trim(),status:$("tickets-status").value,page}));
    if(!currentLoad(target,load)||$("tickets-view").hidden)return;
    renderTicketCount(data);$("ticket-total").textContent=`${data.total??0} requests`;
    const sourceNotice=$("tickets-source-notice");
    if(data.enabled===false){sourceNotice.textContent=data.message||"Shift exchanges are not enabled.";sourceNotice.hidden=false;empty(target,"Shift exchanges disabled","Contact the system operator to check the configuration.","⇄");return;}
    if(Number(data.summary?.uncertain)>0){sourceNotice.replaceChildren(el("span","",`${data.summary.uncertain} requests need update reconciliation. `),button("View uncertain requests","text-button",()=>{$("tickets-status").value="uncertain";loadTickets(1).catch(issueError);}));sourceNotice.hidden=false;}
    if(!data.items?.length)empty(target,"No matching requests","Try a different status or search. Requests awaiting the exchange partner cannot be approved yet.","⇄");
    else {
      const table=el("table","tickets-table"),head=el("thead"),row=el("tr"),body=el("tbody");
      ["Request / Reason","Requester's shift","Partner's shift","Status","Deadline",""].forEach(text=>row.append(el("th","",text)));head.append(row);
      for(const item of data.items){
        const tr=el("tr"),title=el("td","ticket-title-cell"),status=el("td"),times=el("td","ticket-time-cell"),action=el("td");
        title.append(button(item.id,"text-button person-name",()=>openTicket(item.id)),el("p","ticket-reason-preview",item.reason||"No reason provided"));
        const shiftCell=shift=>{const cell=el("td","ticket-shift-cell");cell.append(el("strong","person-name",shift?.member_name||shift?.member_id||"Unknown"),el("p","ticket-schedule",ticketSchedule(shift)),el("p","secondary-line",ticketLocation(shift)));return cell;};
        status.append(ticketStatusTag(item.status));times.append(el("p","",`Created: ${date(item.created_at)}`),el("p","secondary-line",`Expires: ${date(item.expires_at)}`));action.append(button("Xem →","text-button",()=>openTicket(item.id)));
        tr.append(title,shiftCell(item.a),shiftCell(item.b),status,times,action);body.append(tr);
      }
      table.append(head,body);target.replaceChildren(table);
    }
    paging($("tickets-pagination"),data,p=>loadTickets(p).catch(issueError));
  }
  async function openTicket(id) { state.ticket={id};await loadTicket(id).catch(issueError); }
  async function loadTicket(id) {
    if(!can("tickets"))return;
    clearError();$("tickets-view").hidden=true;$("ticket-view").hidden=false;$("page-title").textContent="Exchange details";$("page-description").textContent="Compare both shifts and review confirmation history before deciding.";$("breadcrumb-current").textContent="Shift approval / Details";
    const target=$("ticket-content"),load=beginLoad(target),data=await api(query("/api/ticket",{id}));
    if(!currentLoad(target,load)||$("ticket-view").hidden)return;
    if(data.enabled===false||!data.ticket){empty(target,"Request unavailable",data.message||"Shift exchanges are not enabled.");return;}
    state.ticket=data.ticket;state.ticketRefreshRequired=null;renderTicket();refreshTicketCount();
  }
  function syncTicketControls() {
    document.querySelectorAll("[data-ticket-control]").forEach(node=>{node.disabled=state.ticketBusy||state.ticketRefreshRequired===state.ticket?.id;});
    $("ticket-content").setAttribute("aria-busy",String(state.ticketBusy));
  }
  function renderTicket() {
    const ticket=state.ticket,target=$("ticket-content"),heading=el("article","panel detail-panel ticket-heading"),tags=el("div","profile-tags");
    tags.append(tag(ticket.id,"subtle"),ticketStatusTag(ticket.status));heading.append(tags,el("h2","","Exchange request"),el("p","secondary-line",`Created ${date(ticket.created_at)} · Expires ${date(ticket.expires_at)} · Revision ${textValue(ticket.version)}`),el("h3","ticket-reason-heading","Exchange reason"),el("p","issue-body",ticket.reason||"No reason provided"));
    const shifts=el("div","detail-grid ticket-shifts");
    for(const [label,shift,receiver] of [["Requester's shift",ticket.a,ticket.b],["Partner's shift",ticket.b,ticket.a]]){
      const card=detailPanel(label,[["Original assignee",shift?.member_name||shift?.member_id],["Start",date(shift?.start)],["End",date(shift?.end)],["Subject",shift?.course],["Room",shift?.room],["Period",shift?.period]]);
      card.append(el("p","ticket-receiving",`After exchange: ${receiver?.member_name||receiver?.member_id||"Unknown"} receives this shift.`));shifts.append(card);
    }
    const grid=el("div","ticket-detail-grid"),decision=el("article","panel detail-panel ticket-decision"),audit=el("article","panel detail-panel");decision.append(el("h2","","Process request"));
    const statusHelp={pending_peer:"Waiting for the exchange partner. Approve only after all required confirmations.",pending_manager:"The exchange partner agreed. Check both shifts and the reason before approval.",applying:"The schedule is being updated. Refresh and check the result before another action.",uncertain:"The previous update has an uncertain outcome. Reconcile by reading the schedule; do not automatically write Google Sheets again.",completed:"The schedule update has been verified.",rejected:"The request was rejected.",cancelled:"The requester canceled this request.",expired:"The confirmation deadline has expired.",stale:"The schedule or permissions changed. Create a new request from the current schedule."};
    decision.append(el("p",`notice ${ticket.status==="uncertain"?"warning":"info"}`,statusHelp[ticket.status]||ticket.status_label||"See the processing history alongside this request."));
    if(ticket.message)decision.append(el("p","issue-body ticket-latest",ticket.message));
    const available=Array.isArray(ticket.actions)?ticket.actions:[];
    const actions=available.filter(action=>["approve","reject","reconcile"].includes(action)&&(ticket.status!=="uncertain"||action==="reconcile"));
    const controls=el("div","stack");
    if(actions.includes("approve")){const approve=button("Approve and update schedule","button primary",()=>ticketMutation("approve"));approve.dataset.ticketControl="true";controls.append(approve);}
    if(actions.includes("reconcile")){const reconcile=button("Reconcile outcome","button secondary",()=>ticketMutation("reconcile"));reconcile.dataset.ticketControl="true";controls.append(reconcile);}
    if(actions.includes("reject")){
      const form=el("form","stack ticket-reject-form"),reason=labeledInput("Rejection reason","textarea","",true);reason.input.maxLength=500;reason.input.dataset.ticketControl="true";reason.input.placeholder="Explain the decision to both members.";
      const reject=submitButton("Reject request");reject.className="button danger";reject.dataset.ticketControl="true";form.append(reason.wrap,reject);
      form.addEventListener("submit",event=>{event.preventDefault();const value=reason.input.value.trim();if(!value){reason.input.setCustomValidity("Enter a rejection reason.");reason.input.reportValidity();return;}ticketMutation("reject",value);});reason.input.addEventListener("input",()=>reason.input.setCustomValidity(""));controls.append(form);
    }
    if(!actions.length)controls.append(el("p","muted","No approval action is available in this state."));
    const error=el("div");error.id="ticket-action-error";error.hidden=true;error.setAttribute("role","alert");decision.append(controls,error);
    audit.append(el("h2","","Processing history"));
    for(const event of ticket.events||[]){const entry=el("article","issue-audit");entry.append(el("strong","",event.actor||"System"),el("p","secondary-line",date(event.at)),el("p","",event.text||""));audit.append(entry);}
    if(!ticket.events?.length)audit.append(el("p","muted","No processing history yet."));
    grid.append(decision,audit);target.replaceChildren(heading,shifts,grid);syncTicketControls();
  }
  async function ticketMutation(action,reason="") {
    const ticket=state.ticket,user=state.user;
    if(!ticket||state.ticketBusy||state.ticketRefreshRequired===ticket.id||!can("tickets")||!ticket.actions?.includes(action))return;
    if(ticket.status==="uncertain"&&action!=="reconcile")return;
    state.ticketBusy=true;syncTicketControls();
    try{
      const questions={approve:["Approve and update schedule",`${ticket.id}\n${ticket.a?.member_name||"Requester"} ⇄ ${ticket.b?.member_name||"Exchange partner"}\n\nThe system will recheck the schedule, permissions and both confirmations. If valid, this updates the real Google Sheets schedule.`,"Confirm approval"],reject:["Reject exchange request",`${ticket.id}\n\nReason: ${reason}\n\nThe reason will be recorded in processing history.`,"Confirm rejection"],reconcile:["Reconcile update outcome","The system only reads the schedule to determine the previous update's result. This does not write Google Sheets again.","Reconcile"]};
      const [title,message,label]=questions[action];
      if(!await confirmAction(title,message,label,action==="reject"))return;
      if(state.user!==user||state.ticket?.id!==ticket.id||state.ticket?.version!==ticket.version)return;
      clearError();const data=await api("/api/ticket/action",{id:ticket.id,action,expected_version:ticket.version,reason,confirm:true});
      if(state.user!==user)return;
      if(state.ticket?.id===ticket.id&&!$("ticket-view").hidden){if(data.ticket){state.ticket=data.ticket;state.ticketRefreshRequired=null;renderTicket();}else throw new Error("The result was not returned. Reload the request to check.");}
      toast(data.ticket?.status==="uncertain"?"The outcome is uncertain. Reconcile before any further action.":action==="reject"?"Request rejected.":action==="reconcile"?"Outcome reconciled.":"Exchange request processed.");refreshTicketCount();
    }catch(error){
      if(state.user!==user)return;
      state.ticketRefreshRequired=ticket.id;
      if(state.ticket?.id===ticket.id&&!$("ticket-view").hidden){
        const box=$("ticket-action-error");if(box){box.className="notice warning";box.hidden=false;box.replaceChildren(el("p","",`${messageOf(error)} ${error.status===409?"The request changed. Reload the latest version before deciding.":"Reload the request and check the outcome before another action. Approval commands are not retried automatically."}`),button("Reload request","button secondary small",()=>loadTicket(ticket.id).catch(issueError)));box.scrollIntoView({block:"nearest"});}
      }
    }finally{state.ticketBusy=false;syncTicketControls();}
  }

  const issueStatusNames={new:"New",in_progress:"In progress",waiting:"Waiting for information",done:"Done"};
  const issueKindNames={support:"Bug / support",feature:"Feedback / feature"};
  const permissionNames={issues:"Issue",tickets:"Shift approval",members:"Member",conversations:"Sessions",memories:"Memory"};
  const issueActionNames={notification_retry:"Retry notification",create:"Create request",created:"Create request",claim:"Take ownership",assign:"Assign",status:"Change status",reopen:"Reopen",comment:"Add reply",plan:"Update plan"};
  const issueStatusTag=value=>tag(issueStatusNames[value]||value,value==="waiting"?"warning":value==="new"?"subtle":"");
  function labeledInput(label,type="text",value="",required=false) { const wrap=el("label","",label);const input=el(type==="textarea"?"textarea":"input");if(type!=="textarea")input.type=type;else input.rows=3;input.value=value??"";input.required=required;wrap.append(input);return {wrap,input}; }
  function labeledSelect(label,options,value="") { const wrap=el("label","",label);const input=el("select");for(const [key,text] of options){const item=el("option","",text);item.value=key;input.append(item);}input.value=value;if(input.selectedIndex<0&&input.options.length)input.selectedIndex=0;wrap.append(input);return {wrap,input}; }
  function submitButton(text) { const b=button(text,"button primary");b.type="submit";return b; }
  function issueError(error) { notice($("page-error"),error);$("page-error").scrollIntoView({block:"nearest"}); }
  async function loadOperators() {
    const user=state.user;const data=await api("/api/issue/operators");if(user!==state.user)return;
    state.operators=data.items||[];const select=$("issues-assignee"),value=select.value;
    select.replaceChildren();[["","All"],["unassigned","Unassigned"],...state.operators.map(x=>[x.username,x.display_name||x.username])].forEach(([key,name])=>{const option=el("option","",name);option.value=key;select.append(option);});
    select.value=value;if(select.selectedIndex<0)select.value="";
  }
  async function loadIssues(page=1) {
    const target=$("issues-content");if(!canView("issues")){noAccess(target);return;}state.issuePage=page;const load=beginLoad(target);
    await loadOperators();if(!currentLoad(target,load))return;
    const data=await api(query("/api/issues",{q:$("issues-q").value.trim(),kind:$("issues-kind").value,status:$("issues-status").value,member_id:$("issues-member").value.trim(),assignee:$("issues-assignee").value,page}));
    if(!currentLoad(target,load))return;$("issue-total").textContent=data.total??0;
    if(!data.items?.length)empty(target,"No matching requests","Members can send support requests with /issue in a direct message to the bot.");
    else {const table=el("table"),head=el("thead"),row=el("tr"),body=el("tbody");["Request","Requester","Status","Assignee","Updated",""].forEach(t=>row.append(el("th","",t)));head.append(row);
      data.items.forEach(item=>{const tr=el("tr"),title=el("td","issue-title-cell"),status=el("td"),person=el("td"),action=el("td");title.append(button(item.title||item.id,"text-button person-name",()=>openIssue(item.id)),el("div","secondary-line",`${item.code||item.id} · ${issueKindNames[item.kind]||item.kind}`));person.append(el("div","",item.member_name||item.member_id),el("div","secondary-line",item.member_id));status.append(issueStatusTag(item.status));action.append(button("Xem →","text-button",()=>openIssue(item.id)));tr.append(title,person,status,el("td","",item.assignee_display_name||item.assignee||"None"),el("td","muted",date(item.updated_at??item.updated)),action);body.append(tr);});table.append(head,body);target.replaceChildren(table);}
    paging($("issues-pagination"),data,p=>loadIssues(p).catch(issueError));
  }
  async function openIssue(id) { state.issue={id};await loadIssue(id).catch(issueError); }
  async function loadIssue(id) {
    if(!canView("issues"))return;clearError();$("issues-view").hidden=true;$("issue-view").hidden=false;$("page-title").textContent="Issue details";$("page-description").textContent="Request details, assignment and progress.";$("breadcrumb-current").textContent="Issue / Details";
    const target=$("issue-content"),load=beginLoad(target);const data=await api(query("/api/issue",{id}));if(!currentLoad(target,load)||$("issue-view").hidden)return;state.issue=data.issue||data;renderIssue(data);
  }
  async function issueMutation(action,values,node) {
    const issue=state.issue;if(!issue)return;clearError();
    await busy(node,async()=>{try {await api("/api/issue/action",{id:issue.id,action,revision:issue.revision,confirm:true,...values});toast("Request updated.");if(state.issue?.id===issue.id)await loadIssue(issue.id);}catch(error){issueError(error);}});
  }
  function renderIssue(data) {
    const issue=state.issue,target=$("issue-content"),editable=isFaculty()||issue.assignee===state.user.username;
    const heading=el("article","panel detail-panel issue-heading"),tags=el("div","profile-tags");tags.append(tag(issue.code||issue.id,"subtle"),issueStatusTag(issue.status),tag(issueKindNames[issue.kind]||issue.kind,"subtle"));
    heading.append(tags,el("h2","",issue.title),el("p","issue-body",issue.body||issue.description||""),el("p","secondary-line",`Requester: ${issue.member_name||issue.member_id} · ${issue.member_id} · ${date(issue.created_at??issue.created)}`));
    const owner=el("div","section-toolbar issue-owner");owner.append(el("p","",`Assignee: ${issue.assignee_display_name||issue.assignee||"Unassigned"}`));
    if(!issue.assignee&&issue.status==="new"){const claim=button("Take ownership","button primary",async()=>{if(await confirmAction("Take ownership",`You will become the primary assignee for ${issue.code||issue.id}.`,"Take ownership"))await issueMutation("claim",{},claim);});owner.append(claim);}heading.append(owner);
    const grid=el("div","issue-detail-grid"),main=el("div","stack"),side=el("div","stack");
    const comments=el("article","panel detail-panel");comments.append(el("h2","","Discussion"));
    if(!data.comments?.length)comments.append(el("p","muted","No notes or replies yet."));
    for(const item of data.comments||[]){const note=el("article",`issue-comment ${item.visibility==="public"?"public":"internal"}`),meta=el("div","history-header");meta.append(tag(item.visibility==="public"?"Reply to requester":"Internal note",item.visibility==="public"?"":"warning"),el("strong","",item.actor||item.author||"Admin"),el("time","",date(item.created_at??item.created??item.at)));note.append(meta,el("p","issue-body",item.body||item.text||item.content));comments.append(note);}
    main.append(comments);
    const commentBox=el("article","panel detail-panel"),form=el("form","stack");commentBox.append(el("h2","","Add discussion"));
    const visibility=labeledSelect("Visibility",editable?[["internal","Internal note — support team only"],["public","Reply to requester — delivered through Zalo"]]:[["internal","Internal note — support team only"]],"internal");
    const content=labeledInput("Content","textarea","",true);content.input.maxLength=1200;const add=submitButton("Save note");visibility.input.addEventListener("change",()=>{add.textContent=visibility.input.value==="public"?"Send reply":"Save note";});form.append(visibility.wrap,content.wrap,add);
    form.addEventListener("submit",async event=>{event.preventDefault();if(!content.input.value.trim())return;if(visibility.input.value==="public"&&!await confirmAction("Send reply to member",content.input.value.trim(),"Send reply"))return;await issueMutation("comment",{text:content.input.value.trim(),visibility:visibility.input.value},form);});commentBox.append(form);main.append(commentBox);
    if(editable&&issue.status!=="new")side.append(issueStatusForm(issue));
    if(isFaculty())side.append(issueAssignForm(issue));
    if(issue.kind==="feature")side.append(issuePlanForm(issue,editable));
    if(data.notifications?.length){const notices=el("article","panel detail-panel");notices.append(el("h2","","Zalo notifications"));for(const item of data.notifications){const row=el("div","issue-notification");const labels={pending:"Queued",sending:"Sending",sent:"Sent",failed:"Delivery failed",uncertain:"Uncertain outcome",blocked:"Blocked"};row.append(tag(labels[item.status]||item.status,item.status==="failed"?"danger":item.status==="uncertain"?"warning":"subtle"),el("span","secondary-line",date(item.updated_at??item.updated??item.created)));if(item.status==="failed"&&item.attempts<3){const retry=button("Retry","text-button",async()=>{if(!await confirmAction("Retry notification","Retry only notifications confirmed not to have been delivered. Recipient access will be checked before sending.","Retry"))return;await busy(retry,async()=>{try{await api("/api/issue/notification/retry",{id:item.id,confirm:true});toast("Notification queued.");await loadIssue(issue.id);}catch(error){issueError(error);}});});row.append(retry);}notices.append(row);}side.append(notices);}
    const audit=el("article","panel detail-panel");audit.append(el("h2","","Processing history"));for(const item of data.audit||[]){const row=el("div","issue-audit");row.append(el("strong","",issueActionNames[item.action]||item.action),el("p","secondary-line",`${item.actor||"System"} · ${date(item.created_at??item.created??item.at)}`));if(item.details?.reason||item.reason)row.append(el("p","",item.details?.reason||item.reason));if(item.details?.to)row.append(el("p","",`Changed to: ${issueStatusNames[item.details.to]||item.details.to}`));audit.append(row);}if(!data.audit?.length)audit.append(el("p","muted","No history yet."));side.append(audit);
    grid.append(main,side);target.replaceChildren(heading,grid);
  }
  function issueStatusForm(issue) {
    const box=el("article","panel detail-panel"),form=el("form","stack");box.append(el("h2","",issue.status==="done"?"Reopen request":"Update status"));
    if(issue.status==="done"){const reason=labeledInput("Reopening reason","textarea","",true);reason.input.maxLength=500;form.append(reason.wrap,submitButton("Reopen"));form.addEventListener("submit",async e=>{e.preventDefault();if(await confirmAction("Reopen request",reason.input.value.trim(),"Reopen"))await issueMutation("reopen",{reason:reason.input.value.trim()},form);});}
    else {const choices=issue.status==="waiting"?[["in_progress","In progress"],["done","Done"]]:[["waiting","Waiting for information"],["done","Done"]];const status=labeledSelect("New status",choices,choices[0][0]),reply=labeledInput("Reply to requester","textarea");reply.input.maxLength=1200;const help=el("p","muted","When waiting for information or completing a request, write a clear reply for the bot to send to the member.");const sync=()=>{reply.input.required=["waiting","done"].includes(status.input.value);};status.input.addEventListener("change",sync);sync();form.append(status.wrap,reply.wrap,help,submitButton("Updated"));form.addEventListener("submit",async e=>{e.preventDefault();if(await confirmAction("Update status",`Change to “${issueStatusNames[status.input.value]}”.${reply.input.value.trim()?"\n\nReply to requester:\n"+reply.input.value.trim():""}`,"Confirm update"))await issueMutation("status",{status:status.input.value,public_reply:reply.input.value.trim()},form);});}
    box.append(form);return box;
  }
  function issueAssignForm(issue) {
    const box=el("article","panel detail-panel"),form=el("form","stack");box.append(el("h2","","Assign / hand off"));
    const owner=labeledSelect("Assignee",[["","Select an assignee"],...state.operators.map(x=>[x.username,x.display_name||x.username])],issue.assignee||""),reason=labeledInput("Assignment reason","textarea","",true);owner.input.required=true;reason.input.maxLength=500;form.append(owner.wrap,reason.wrap,submitButton("Save assignment"));form.addEventListener("submit",async e=>{e.preventDefault();if(await confirmAction("Assign request",`Giao cho ${owner.input.selectedOptions[0].textContent}.\n${reason.input.value.trim()}`,"Confirm assignment"))await issueMutation("assign",{assignee:owner.input.value,reason:reason.input.value.trim()},form);});box.append(form);return box;
  }
  function issuePlanForm(issue,editable) {
    const box=el("article","panel detail-panel");box.append(el("h2","","Work plan"));
    const priorities={"":"Not assessed",low:"Low",normal:"Normal",high:"Cao",urgent:"Urgent"};
    if(!editable){box.append(el("p","",`Priority: ${priorities[issue.priority]||"Not assessed"}`),el("p","",`Estimate: ${textValue(issue.estimate)}`),el("p","",`Due: ${textValue(issue.due_date)}`));return box;}
    const form=el("form","stack"),priority=labeledSelect("Priority",Object.entries(priorities),issue.priority||""),estimate=labeledInput("Estimate (for example: 2 days)","text",issue.estimate||""),due=labeledInput("Due date","date",issue.due_date||"");estimate.input.maxLength=100;form.append(priority.wrap,estimate.wrap,due.wrap,submitButton("Save plan"));form.addEventListener("submit",async e=>{e.preventDefault();await issueMutation("plan",{priority:priority.input.value,estimate:estimate.input.value.trim(),due_date:due.input.value},form);});box.append(form);return box;
  }
  async function loadAccounts(page=1) {
    const target=$("accounts-content");if(!isFaculty()){noAccess(target);return;}state.accountPage=page;const load=beginLoad(target);const data=await api(query("/api/admin/accounts",{q:$("accounts-q").value.trim(),page}));if(!currentLoad(target,load))return;
    if(!data.items?.length)empty(target,"Account not found","Try a different search or create an administrator account.");else {const table=el("table"),head=el("thead"),row=el("tr"),body=el("tbody");["Account","Role","Permissions","Status","Open issues",""].forEach(t=>row.append(el("th","",t)));head.append(row);data.items.forEach(item=>{const tr=el("tr"),name=el("td"),permissions=el("td","account-permission-cell"),status=el("td"),actions=el("td");name.append(button(item.display_name||item.username,"text-button person-name",()=>openAccount(item.username)),el("div","secondary-line",item.username));for(const permission of item.permissions||[])permissions.append(tag(permissionNames[permission]||permission,"subtle"));status.append(tag(item.active?"Activity":"Locked",item.active?"":"danger"));actions.append(button("Edit","text-button",()=>openAccount(item.username)));tr.append(name,el("td","",item.role==="faculty"?"System owner":"Admin"),permissions,status,el("td","",item.open_issues??0),actions);body.append(tr);});table.append(head,body);target.replaceChildren(table);}paging($("accounts-pagination"),data,p=>loadAccounts(p).catch(issueError));
  }
  async function openAccount(username=null) {
    if(!isFaculty())return;const user=state.user;try{const data=username?await api(query("/api/admin/account",{username})):null;if(user!==state.user)return;const account=data?.account||data;state.editingAccount=account;$("account-form").reset();$("account-error").hidden=true;$("account-title").textContent=account?"Edit account":"Create administrator";$("account-username").value=account?.username||"";$("account-username").readOnly=Boolean(account);$("account-name").value=account?.display_name||"";$("account-password").value="";$("account-password").required=!account;$("account-password-label").firstChild.textContent=account?"New password (leave blank to keep current)":"Initial password";$("account-role").textContent=account?.role==="faculty"?"Role: system owner. Owner designation is performed through the server administration tool.":"Role: administrator. This account cannot grant or change other administrators' permissions.";$("account-active").checked=account?Boolean(account.active):true;Object.keys(permissionNames).forEach(p=>{$(`account-${p}`).checked=account?Boolean(account.permissions?.includes(p)):p==="issues";});renderAccountAudit(account);$("account-dialog").showModal();(account?$("account-name"):$("account-username")).focus();}catch(error){issueError(error);}
  }
  function renderAccountAudit(account) {
    const target=$("account-audit");target.replaceChildren();target.hidden=!account;if(!account)return;
    target.append(el("h3","","Account audit history"),el("p","secondary-line",`Assigned issues: ${account.open_issues??0} · Updated ${date(account.updated)}`));
    const names={account_created:"Create account",account_updated:"Update account",faculty_designated:"Designate owner",created:"Create account",updated:"Update account",create:"Create account",update:"Update account",faculty_granted:"Designate owner",password_reset:"Reset password",password_changed:"Change own password"};
    for(const item of account.audit||[]){const row=el("div","issue-audit");row.append(el("strong","",names[item.action]||item.action),el("p","secondary-line",`${item.actor||"System"} · ${date(item.created_at??item.created??item.at)}`));if(item.reason)row.append(el("p","",item.reason));target.append(row);}
    if(!account.audit?.length)target.append(el("p","muted","No change history yet."));
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
  $("account-form").addEventListener("submit",event=>{event.preventDefault();$("account-error").hidden=true;busy(event.currentTarget,async()=>{try {const old=state.editingAccount;const payload={operation:old?"update":"create",username:$("account-username").value.trim(),display_name:$("account-name").value.trim(),permissions:Object.keys(permissionNames).filter(p=>$(`account-${p}`).checked),active:$("account-active").checked,confirm:true};if(old)payload.version=old.version;const password=$("account-password").value;if(password)payload.password=password;if(!await confirmAction(old?"Update account":"Create administrator account",`${payload.display_name} (${payload.username})\n${payload.active?"Activity":"Lock account"} · Permissions: ${payload.permissions.map(p=>permissionNames[p]).join(", ")||"No permissions"}${old?"\nExisting sessions will be revoked.":""}`,"Confirm save",!payload.active))return;await api("/api/admin/account/save",payload);$("account-password").value="";$("account-dialog").close();toast("Account saved.");if(old?.username===state.user?.username){showLogin();return;}await loadAccounts(state.accountPage);}catch(error){$("account-password").value="";notice($("account-error"),error);}});});

  $("confirmation-cancel").addEventListener("click",()=>finishConfirmation(false));
  $("confirmation-accept").addEventListener("click",()=>finishConfirmation(true));
  $("confirmation-dialog").addEventListener("cancel",event=>{event.preventDefault();finishConfirmation(false);});
  $("confirmation-dialog").addEventListener("close",()=>{if(!$("confirmation-dialog").open)finishConfirmation(false);});
  $("login-form").addEventListener("submit",event=>{event.preventDefault();$("login-error").hidden=true;busy(event.currentTarget,async()=>{try{await api("/api/session");const data=await api("/api/login",{username:$("username").value.trim(),password:$("password").value});$("password").value="";const session=data.user?data:await api("/api/session");if(!session.user)throw new Error("Sign-in did not complete. Please try again.");showApp(session.user);}catch(e){$("password").value="";notice($("login-error"),e);}});});
  $("logout").addEventListener("click",()=>busy($("logout"),async()=>{try{await api("/api/logout",{});showLogin();}catch(e){notice($("page-error"),e);}}));
  let passwordChangeBusy=false;
  function clearPasswordFields() { ["password-current","password-new","password-confirm"].forEach(id=>{$(id).value="";}); }
  $("change-password").addEventListener("click",()=>{if(!state.user)return;clearPasswordFields();$("password-error").hidden=true;$("password-dialog").showModal();$("password-current").focus();});
  $("password-dialog").addEventListener("close",clearPasswordFields);
  $("password-form").addEventListener("submit",async event=>{
    event.preventDefault();if(passwordChangeBusy||!state.user)return;passwordChangeBusy=true;$("password-error").hidden=true;
    try { await busy(event.currentTarget,async()=>{
      const payload={current_password:$("password-current").value,new_password:$("password-new").value,confirm_password:$("password-confirm").value};
      if(payload.new_password!==payload.confirm_password)throw new Error("The new passwords do not match.");
      await api("/api/password/change",payload);clearPasswordFields();$("password-dialog").close();state.csrf="";showLogin();toast("Password changed. Sign in with the new password.");
    }); } catch(error){notice(state.user?$("password-error"):$("login-error"),error);} finally {clearPasswordFields();passwordChangeBusy=false;}
  });
  $("brand-home").addEventListener("click",event=>{event.preventDefault();navigate(firstView());});
  document.querySelectorAll(".nav-item").forEach(node=>node.addEventListener("click",()=>navigate(node.dataset.view)));
  $("refresh-view").addEventListener("click",()=>busy($("refresh-view"),refresh));
  $("members-filter").addEventListener("submit",event=>{event.preventDefault();clearError();loadMembers(1).catch(e=>notice($("page-error"),e));});
  $("member-back").addEventListener("click",()=>navigate("members"));
  $("registration-repair-dialog").addEventListener("close",clearRegistrationRepair);
  $("registration-repair-form").addEventListener("submit",submitRegistrationRepair);
  $("registration-repair-reload").addEventListener("click",()=>{const memberId=registrationRepair?.memberId;if(!memberId||registrationRequestBusy)return;$("registration-repair-dialog").close();clearRegistrationRepair();openMember(memberId);});
  $("registration-reset-dialog").addEventListener("close",clearRegistrationReset);
  $("registration-reset-form").addEventListener("submit",submitRegistrationReset);
  $("registration-reset-reload").addEventListener("click",()=>{const memberId=registrationReset?.memberId;if(!memberId||registrationResetBusy)return;$("registration-reset-dialog").close();clearRegistrationReset();openMember(memberId);});
  document.querySelectorAll("#member-tabs button").forEach(node=>node.addEventListener("click",()=>{state.tab=node.dataset.tab;renderMemberTab().catch(e=>notice($("page-error"),e));}));
  $("conversations-filter").addEventListener("submit",event=>{event.preventDefault();clearError();if($("history-start").value&&$("history-end").value&&$("history-start").value>$("history-end").value){notice($("page-error"),new Error("The start date must be on or before the end date."));return;}loadHistory($("conversations-content"),1,historyFilters()).catch(e=>notice($("page-error"),e));});
  $("memories-filter").addEventListener("submit",event=>{event.preventDefault();clearError();loadMemories($("memories-content"),1,$("memories-member").value.trim()).catch(e=>notice($("page-error"),e));});
  $("memory-session").addEventListener("input",()=>{$("memory-share").checked=false;memoryScope();});
  $("new-memory").addEventListener("click",()=>openMemory());$("memory-scope").addEventListener("change",()=>{$("memory-share").checked=false;memoryScope();});
  document.querySelectorAll("[data-close]").forEach(node=>node.addEventListener("click",()=>$(node.dataset.close).close()));
  $("memory-form").addEventListener("submit",event=>{event.preventDefault();$("memory-error").hidden=true;busy(event.currentTarget,async()=>{try{const old=state.editingMemory;const scope=$("memory-scope").value;const payload={content:$("memory-content").value.trim(),member_id:$("memory-member").value.trim()||null,scope,active:$("memory-active").checked,expires:$("memory-expires").value?`${$("memory-expires").value}T23:59:59+07:00`:null};if(!payload.content)throw new Error("Enter the memory content.");if(old?.id){payload.id=old.id;payload.expected_revision=old.revision;}if(old?.source_message_id)payload.source_message_id=old.source_message_id;if(scope==="session"){payload.session_key=$("memory-session").value.trim();if(payload.session_key.toUpperCase().startsWith("ZALO:GROUP:"))payload.confirm_share=$("memory-share").checked;}if(scope==="group"){payload.chat_id=$("memory-chat").value.trim();payload.confirm_share=$("memory-share").checked;}await api("/api/memory/save",payload);$("memory-dialog").close();toast("Memory saved. Changes apply to the next reply.");if(state.view==="memories"||state.tab==="memories")await refreshMemories();}catch(e){notice($("memory-error"),e);}});});
  $("link-form").addEventListener("submit",event=>{event.preventDefault();$("link-error").hidden=true;busy(event.currentTarget,async()=>{try{if(!state.linkToken){const data=await api("/api/member/link/preview",{member_id:state.member.member_id,target_account_id:$("link-target").value.trim()});state.linkToken=data.token||data.preview_token;if(!state.linkToken)throw new Error("No valid confirmation was returned by the server.");previewLink(data);$("link-submit").textContent="Confirm change";}else{await api("/api/member/link/confirm",{token:state.linkToken,confirm:true});$("link-dialog").close();toast("Member link updated.");await loadMember(state.member.member_id,true);}}catch(e){notice($("link-error"),e);}});});
  async function init() { try{const session=await api("/api/session");if(session.user)showApp(session.user);else showLogin();}catch(e){$("boot-state").hidden=true;showLogin();notice($("login-error"),e);} }
  init();
})();
