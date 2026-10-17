"use strict";

// All fetches are relative to the page, which the app serves at its own origin root.
const API = {
  status:   () => fetch("api/status").then(r => r.json()),
  list:     () => fetch("api/contracts").then(r => r.json()),
  get:      (id) => fetch("api/contracts/" + id).then(r => r.json()),
  patch:    (id, body) => fetch("api/contracts/" + id, {method:"PATCH", headers:json(), body:JSON.stringify(body)}).then(r=>r.json()),
  del:      (id) => fetch("api/contracts/" + id, {method:"DELETE"}).then(r=>r.json()),
  patchDoc: (id, body) => fetch("api/documents/" + id, {method:"PATCH", headers:json(), body:JSON.stringify(body)}).then(r=>r.json()),
  addNote:  (id, body) => fetch("api/contracts/" + id + "/notes", {method:"POST", headers:json(), body:JSON.stringify(body)}).then(r=>r.json()),
  delNote:  (id, noteId) => fetch("api/contracts/" + id + "/notes/" + noteId, {method:"DELETE"}).then(r=>r.json()),
  addAttachment: (id, body) => fetch("api/contracts/" + id + "/attachments", {method:"POST", headers:json(), body:JSON.stringify(body)}).then(r=>r.json()),
  getTags:  () => fetch("api/tags").then(r=>r.json()),
  setTags:  (id, tags) => fetch("api/contracts/" + id + "/tags", {method:"PUT", headers:json(), body:JSON.stringify({tags})}).then(r=>r.json()),
  analyze:  (body) => fetch("api/analyze", {method:"POST", headers:json(), body:JSON.stringify(body)}).then(r=>r.json()),
  commit:   (body) => fetch("api/commit", {method:"POST", headers:json(), body:JSON.stringify(body)}).then(r=>r.json()),
  syncNotion: () => fetch("api/notion/sync", {method:"POST"}).then(r=>r.json()),
};
function json(){ return {"Content-Type":"application/json"}; }
function icons(){ if(window.lucide) lucide.createIcons(); }
function esc(s){ return (s==null?"":String(s)).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }

let STATUS = {departments:[], dropbox:{configured:false}, notion:{configured:false}};
let AVAILABLE_TAGS = [];
let CONTRACTS = [];
let CURRENT = null;         // loaded profile {contract, documents, notes}
let ACTIVE_DOC = null;      // open doc id in split view
let STAGED = null;          // {analysis_id, parsed, is_followon, matches} + chosen match

// ---- boot ----------------------------------------------------------------

async function boot(){
  ensureName();
  STATUS = await API.status();
  AVAILABLE_TAGS = (await API.getTags()).tags || [];
  fillDepartments();
  renderUploadTags();
  updateStorageHint();
  updateFoot();
  await loadContracts();
  icons();
}

function fillDepartments(){
  const opts = STATUS.departments.map(d=>`<option>${esc(d)}</option>`).join("");
  document.getElementById("fDept").insertAdjacentHTML("beforeend", opts);
  document.getElementById("uDept").insertAdjacentHTML("beforeend", opts);
}
function updateStorageHint(){
  const el = document.getElementById("driveHint");
  if(STATUS.dropbox && STATUS.dropbox.configured){
    el.textContent = "A copy of each contract is saved to your Dropbox, and the original is also kept in the system and viewable anytime.";
  } else {
    el.textContent = "The original document is kept in the system and viewable anytime.";
  }
}
function updateFoot(){
  const n = CONTRACTS.length;
  const dbx = (STATUS.dropbox && STATUS.dropbox.configured) ? "Dropbox connected" : "Dropbox not set up";
  const notion = STATUS.notion.configured ? "Notion connected" : "Notion not set up";
  document.getElementById("foot").textContent = `${n} contract${n===1?"":"s"} · ${dbx} · ${notion}`;
}

// ---- who am I (note attribution, no accounts) ---------------------------

function ensureName(){ updateWhoami(); }
function updateWhoami(){
  const n = localStorage.getItem("contracts_user");
  document.getElementById("whoami").textContent = n ? ("You: " + n) : "Set your name";
}
function setName(after){
  promptModal({
    title: "Your name",
    body: "Shown on notes you add, so your team knows who wrote them.",
    value: localStorage.getItem("contracts_user") || "",
    placeholder: "e.g. Cathy Zhao",
    confirmText: "Save",
    onConfirm: (v) => {
      v = (v || "").trim();
      if(v) localStorage.setItem("contracts_user", v);
      updateWhoami();
      if(v && typeof after === "function") after();
    },
  });
}
function myName(){ return localStorage.getItem("contracts_user") || "Anonymous"; }

// ---- search --------------------------------------------------------------

let SELECTED_OWNERS = new Set();

async function loadContracts(){
  const data = await API.list();
  CONTRACTS = data.contracts || [];
  // Drop any selected owners that no longer exist.
  const present = new Set(CONTRACTS.map(c => c.owner || ""));
  SELECTED_OWNERS = new Set([...SELECTED_OWNERS].filter(o => present.has(o)));
  updateFoot();
  renderOwnerFilter();
  renderTagFilter();
  renderRows();
}

let SELECTED_TAGS = new Set();
function renderTagFilter(){
  const host = document.getElementById("tagFilter");
  if(!host) return;
  const present = [...new Set(CONTRACTS.flatMap(c => c.tags || []))].sort();
  SELECTED_TAGS = new Set([...SELECTED_TAGS].filter(t => present.includes(t)));
  const count = SELECTED_TAGS.size;
  const opts = present.length
    ? present.map(t => `<label class="opt"><input type="checkbox" ${SELECTED_TAGS.has(t)?"checked":""} onchange="toggleTagFilter('${encodeURIComponent(t)}', this.checked)"> ${esc(t)}</label>`).join("")
    : `<div class="opt muted">No tags in use yet</div>`;
  host.innerHTML = `<button onclick="toggleTagPop(event)"><i data-lucide="tag"></i> Tags${count?`<span class="cnt">${count}</span>`:""} <i data-lucide="chevron-down" style="width:14px;height:14px"></i></button><div class="pop" onclick="event.stopPropagation()">${opts}</div>`;
  icons();
}
function toggleTagPop(e){ e.stopPropagation(); document.getElementById("tagFilter").classList.toggle("open"); }
function toggleTagFilter(encoded, checked){
  const t = decodeURIComponent(encoded);
  if(checked) SELECTED_TAGS.add(t); else SELECTED_TAGS.delete(t);
  renderTagFilter();
  renderRows();
}

// Tags chosen on the upload page, applied to the contract once it's created.
let UPLOAD_TAGS = [];
function renderUploadTags(){
  const host = document.getElementById("uploadTags");
  if(!host) return;
  const chips = UPLOAD_TAGS.map(t =>
    `<span class="tagchip">${esc(t)} <button class="tagx" title="Remove tag" onclick="removeUploadTag('${encodeURIComponent(t)}')"><i data-lucide="x"></i></button></span>`
  ).join("");
  const options = AVAILABLE_TAGS.map(t => `<option value="${esc(t)}">`).join("");
  host.innerHTML = `${chips}<span class="tagadd"><input id="uploadTagInput" list="uploadTagOptions" placeholder="Add tag…" onkeydown="uploadTagKey(event)">
    <datalist id="uploadTagOptions">${options}</datalist></span>`;
  icons();
}
function uploadTagKey(e){
  if(e.key === "Enter"){ e.preventDefault(); const v = e.target.value.trim();
    if(v && !UPLOAD_TAGS.some(t => t.toLowerCase() === v.toLowerCase())){ UPLOAD_TAGS.push(v); renderUploadTags(); document.getElementById("uploadTagInput").focus(); } }
}
function removeUploadTag(encoded){
  const t = decodeURIComponent(encoded);
  UPLOAD_TAGS = UPLOAD_TAGS.filter(x => x !== t);
  renderUploadTags();
}

function renderOwnerFilter(){
  const host = document.getElementById("ownerFilter");
  if(!host) return;
  const owners = [...new Set(CONTRACTS.map(c => c.owner || ""))].sort();
  const count = SELECTED_OWNERS.size;
  const opts = owners.length
    ? owners.map(o => {
        const label = o || "(Unassigned)";
        return `<label class="opt"><input type="checkbox" ${SELECTED_OWNERS.has(o)?"checked":""} onchange="toggleOwner('${encodeURIComponent(o)}', this.checked)"> ${esc(label)}</label>`;
      }).join("")
    : `<div class="opt muted">No owners yet</div>`;
  host.innerHTML = `<button onclick="toggleOwnerPop(event)"><i data-lucide="users"></i> Owner${count?`<span class="cnt">${count}</span>`:""} <i data-lucide="chevron-down" style="width:14px;height:14px"></i></button><div class="pop" onclick="event.stopPropagation()">${opts}</div>`;
  icons();
}
function toggleOwnerPop(e){ e.stopPropagation(); document.getElementById("ownerFilter").classList.toggle("open"); }
function toggleOwner(encoded, checked){
  const o = decodeURIComponent(encoded);
  if(checked) SELECTED_OWNERS.add(o); else SELECTED_OWNERS.delete(o);
  renderOwnerFilter();
  renderRows();
}

function valFlag(isGuess, isManual){
  if(isManual)
    return '<i data-lucide="pencil" class="vf manual" title="Set manually by a user"></i>';
  return isGuess
    ? '<i data-lucide="sparkles" class="vf guess" title="AI best guess — no fixed amount stated in the contract"></i>'
    : '<i data-lucide="calculator" class="vf calc" title="Calculated from amounts stated in the contract"></i>';
}

function renderRows(){
  const q = (document.getElementById("q").value||"").toLowerCase();
  const dept = document.getElementById("fDept").value;
  const status = document.getElementById("fStatus").value;
  const rows = CONTRACTS.filter(c=>{
    if(dept && c.department !== dept) return false;
    if(status && (c.status||"Active") !== status) return false;
    if(SELECTED_OWNERS.size && !SELECTED_OWNERS.has(c.owner||"")) return false;
    if(SELECTED_TAGS.size && !(c.tags||[]).some(t => SELECTED_TAGS.has(t))) return false;
    if(q){
      const hay = (c.contracting_party+" "+c.description+" "+c.department+" "+c.agreement_type).toLowerCase();
      if(!hay.includes(q)) return false;
    }
    return true;
  });
  document.getElementById("count").textContent = `Showing ${rows.length} of ${CONTRACTS.length} contract${CONTRACTS.length===1?"":"s"}`;
  const host = document.getElementById("tableHost");
  if(CONTRACTS.length === 0){
    host.innerHTML = `<div class="empty"><i data-lucide="file-plus-2"></i><div class="big">No contracts yet</div><div>Upload your first contract to get started.</div><div style="margin-top:16px"><button class="btn primary" data-goto="upload"><i data-lucide="upload-cloud"></i> Upload a contract</button></div></div>`;
    icons(); return;
  }
  if(rows.length === 0){
    host.innerHTML = `<div class="empty"><i data-lucide="search-x"></i><div class="big">No matches</div><div>Try clearing filters or your search.</div></div>`;
    icons(); return;
  }
  host.innerHTML = `<div class="tablewrap"><table>
    <thead><tr><th>Contracting party</th><th>Department</th><th>Status</th><th>Execution</th><th>Termination</th><th>Est. value</th><th>Description</th><th>Agreement</th></tr></thead>
    <tbody>${rows.map(rowHtml).join("")}</tbody></table></div>`;
  icons();
}

function rowHtml(c){
  const term = c.termination_date ? `<span class="muted">${esc(fmtDate(c.termination_date))}</span>` : '<span class="dash">—</span>';
  const amend = (c.agreement_type && c.agreement_type!=="Agreement") ? `<span class="chip amend" style="margin-right:6px"><i data-lucide="git-branch" style="width:11px;height:11px"></i> ${esc(c.agreement_type)}</span>` : "";
  const drive = c.drive_link ? `<a class="drivelink" href="${esc(c.drive_link)}" target="_blank" onclick="event.stopPropagation()"><i data-lucide="external-link"></i> ${storageLabel(c.drive_link)}</a>` : "";
  return `<tr onclick="openProfile('${c.id}')">
    <td><div class="party"><i data-lucide="file-text" class="doc-ico" style="width:16px;height:16px"></i> ${esc(c.contracting_party||"Untitled")}</div></td>
    <td>${c.department?`<span class="chip dept">${esc(c.department)}</span>`:'<span class="dash">—</span>'}</td>
    <td>${statusChip(c.status)}</td>
    <td class="muted">${esc(fmtDate(c.execution_date))||'<span class="dash">—</span>'}</td>
    <td>${term}</td>
    <td><span class="val">${esc(c.est_value)||'—'} ${c.est_value?valFlag(c.est_value_is_guess,c.est_value_manual):''}</span></td>
    <td><div class="desc">${esc(trunc(c.description,150))}</div></td>
    <td>${amend}${drive}</td>
  </tr>`;
}

// ---- profile -------------------------------------------------------------

async function openProfile(id){
  goto("profile");
  ACTIVE_DOC = null;
  document.getElementById("profileHost").innerHTML = `<div style="padding:40px;text-align:center"><span class="spin dark"></span></div>`;
  CURRENT = await API.get(id);
  renderProfile();
}

function renderProfile(){
  const c = CURRENT.contract;
  const isFollowup = c.agreement_type && c.agreement_type !== "Agreement";
  const host = document.getElementById("profileHost");
  host.innerHTML = `
    <div class="profile-head">
      <div>
        <h2 class="ctitle">${esc(c.contracting_party||"Untitled contract")}</h2>
        <div class="ctags">
          ${c.department?`<span class="chip dept"><i data-lucide="building-2" style="width:12px;height:12px"></i> ${esc(c.department)}</span>`:""}
          <span class="chip"><i data-lucide="file-badge" style="width:12px;height:12px"></i> ${esc(c.agreement_type||"Agreement")}</span>
          ${statusChip(c.status)}
          ${tagsBarHtml()}
        </div>
      </div>
      <div style="display:flex;gap:10px;align-items:center">
        <select class="statussel" onchange="changeStatus(this.value)" title="Contract status">
          <option ${(c.status||"Active")==="Active"?"selected":""}>Active</option>
          <option ${c.status==="Terminated"?"selected":""}>Terminated</option>
        </select>
        ${c.drive_link?`<a class="btn" href="${esc(c.drive_link)}" target="_blank"><i data-lucide="external-link"></i> ${storageLabel(c.drive_link)}</a>`:""}
        <button class="btn" onclick="deleteContract()" title="Delete this contract" style="color:#B42318"><i data-lucide="trash-2"></i></button>
      </div>
    </div>
    <div class="split" id="split">
      <div class="left">
        <div class="card">
          <div class="hd"><i data-lucide="file-text"></i> Contract details</div>
          <div class="bd"><div class="fields">
            ${fieldRow("Contracting party","contracting_party",c.contracting_party)}
            ${fieldRow("Our entity","our_entity",c.our_entity)}
            ${fieldRow("Execution date","execution_date",c.execution_date)}
            ${fieldRow("Termination date","termination_date",c.termination_date)}
            ${fieldRow("Department","department",c.department, {badge:"manual"})}
            ${fieldRow("Owner","owner",c.owner, {badge:"manual"})}
            ${fieldRow("Estimated value","est_value",c.est_value, {flag:c.est_value_is_guess, manual:c.est_value_manual})}
            ${c.est_value_basis?`<div class="field full"><span class="k">How the value was determined</span><span class="v" style="font-weight:400;color:var(--muted)">${esc(c.est_value_basis)}</span></div>`:""}
            ${fieldRow("Brief description","description",c.description, {full:true, ai:true})}
            ${fieldRow("Termination terms","termination_terms",c.termination_terms, {full:true})}
          </div></div>
        </div>

        <div class="section-title"><i data-lucide="layers"></i> Statements of work, exhibits &amp; amendments</div>
        <div class="docgrid" id="docgrid">${docBoxes()}</div>

        <div class="section-title"><i data-lucide="paperclip"></i> Attachments
          <button class="btn sm" onclick="pickAttachment()" style="margin-left:auto"><i data-lucide="plus"></i> Add attachment</button>
        </div>
        <div id="attachments">${attachmentsHtml()}</div>
        <input type="file" id="attachInput" style="display:none">

        <div class="section-title"><i data-lucide="sticky-note"></i> Notes</div>
        <div id="notes">${notesHtml()}</div>
        <div class="noteform">
          <textarea id="noteInput" placeholder="Add a note… (saved with your name and a timestamp)"></textarea>
          <button class="btn primary" onclick="postNote()" style="align-self:flex-end"><i data-lucide="send"></i> Post</button>
        </div>
      </div>
      <div class="right" id="docpanel" style="display:none"></div>
    </div>`;
  icons();
  if(ACTIVE_DOC) openDoc(ACTIVE_DOC);
}

function fieldRow(label, key, value, opts={}){
  const badge = opts.badge?`<span class="manual-badge">${opts.badge}</span>`:"";
  const ai = opts.ai?`<span class="ai-badge"><i data-lucide="sparkles"></i> AI</span>`:"";
  const flag = (opts.flag!==undefined)?` ${valFlag(opts.flag, opts.manual)}`:"";
  const display = key.endsWith("_date") ? fmtDate(value) : value;
  const shown = esc(display) || '<span class="dash">—</span>';
  const cls = opts.full ? "field full" : "field";
  return `<div class="${cls}"><span class="k">${esc(label)} ${badge}${ai}</span>
    <span class="v" data-key="${key}" onclick="editField(this)">${shown}${flag} <i data-lucide="pencil" class="edit"></i></span></div>`;
}

function editField(el){
  if(el.dataset.editing) return;
  const key = el.dataset.key;
  const raw = CURRENT.contract[key] || "";
  el.dataset.editing = "1";
  el.classList.add("editing");
  el.setAttribute("contenteditable","true");
  el.textContent = raw;
  el.focus();
  const range = document.createRange(); range.selectNodeContents(el);
  const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range);
  const finish = async (save) => {
    el.removeAttribute("contenteditable");
    el.classList.remove("editing");
    delete el.dataset.editing;
    el.removeEventListener("blur", onBlur);
    el.removeEventListener("keydown", onKey);
    if(save){
      const val = el.textContent.trim();
      if(val !== raw){
        await API.patch(CURRENT.contract.id, {[key]: val});
        CURRENT = await API.get(CURRENT.contract.id);
        await loadContracts();
        toast("Saved");
      }
    }
    renderProfile();
  };
  const onBlur = () => finish(true);
  const onKey = (e) => {
    const multiline = el.closest(".full");
    if(e.key==="Enter" && !multiline){ e.preventDefault(); finish(true); }
    if(e.key==="Escape"){ e.preventDefault(); finish(false); }
  };
  el.addEventListener("blur", onBlur);
  el.addEventListener("keydown", onKey);
}

// ---- documents / split panel --------------------------------------------

// ---- tags ----------------------------------------------------------------

function tagsBarHtml(){
  const tags = CURRENT.contract.tags || [];
  const chips = tags.map(t =>
    `<span class="chip tagpill"><i data-lucide="tag" style="width:11px;height:11px"></i> ${esc(t)} <button class="tagx" title="Remove tag" onclick="removeTag('${encodeURIComponent(t)}')"><i data-lucide="x"></i></button></span>`
  ).join("");
  const options = AVAILABLE_TAGS.map(t => `<option value="${esc(t)}">`).join("");
  return `${chips}<span class="tagadd"><input id="tagInput" list="tagOptions" placeholder="+ tag" onkeydown="tagKey(event)">
    <datalist id="tagOptions">${options}</datalist></span>`;
}
function tagKey(e){
  if(e.key === "Enter"){ e.preventDefault(); const v = e.target.value.trim(); if(v) addTag(v); }
}
function addTag(value){
  const cur = CURRENT.contract.tags || [];
  if(cur.some(t => t.toLowerCase() === value.toLowerCase())) return; // already has it
  setContractTags([...cur, value]);
}
function removeTag(encoded){
  const t = decodeURIComponent(encoded);
  setContractTags((CURRENT.contract.tags || []).filter(x => x !== t));
}
async function setContractTags(tags){
  // Update optimistically so rapid successive edits accumulate even while the
  // save (which syncs to Notion/Dropbox) is still in flight.
  const id = CURRENT.contract.id;
  CURRENT.contract.tags = tags;
  renderProfile();
  const el = document.getElementById("tagInput"); if(el) el.focus();
  const res = await API.setTags(id, tags);
  if(res.error){
    toast(res.error);
    CURRENT = await API.get(id);   // revert to the server's truth
    renderProfile();
    return;
  }
  AVAILABLE_TAGS = res.available || AVAILABLE_TAGS;
  CURRENT.contract.tags = res.tags || tags;
  loadContracts();                 // refresh the search list's tags (no need to block)
  toast("Tags updated");
}

function docBoxes(){
  // Attachments are shown in their own section, not here.
  const docs = (CURRENT.documents || []).filter(d => d.doc_type !== "Attachment");
  if(docs.length===0) return `<div class="empty" style="padding:24px">No attached documents.</div>`;
  return docs.map(d=>`
    <div class="docbox" id="box-${d.id}" onclick="openDoc('${d.id}')">
      <div class="type">${esc(d.doc_type)}</div>
      <div class="nm">${esc(d.name||d.doc_type)}</div>
      <div class="mt">${esc(trunc(d.summary,80))||"View details"}</div>
      <div class="row"><span style="font-size:12px;color:var(--muted)">${d.source_filename?esc(trunc(d.source_filename,22)):""}</span><i data-lucide="chevron-right"></i></div>
    </div>`).join("");
}

function attachmentsHtml(){
  const atts = (CURRENT.documents || []).filter(d => d.doc_type === "Attachment");
  if(atts.length===0) return `<div class="empty" style="padding:18px">No attachments yet. Use "Add attachment" to file one with this contract.</div>`;
  return atts.map(a=>{
    const href = a.drive_link || (a.file_key ? ("file/" + a.file_key) : "");
    const where = a.drive_link ? "Open in Dropbox" : "Open";
    return `<div class="attach">
      <i data-lucide="file" class="af"></i>
      <div class="an"><div class="anm">${esc(a.name||a.source_filename||"Attachment")}</div>
        ${a.source_filename && a.source_filename!==a.name?`<div class="asrc">${esc(a.source_filename)}</div>`:""}</div>
      ${href?`<a class="btn sm" href="${esc(href)}" target="_blank"><i data-lucide="external-link"></i> ${where}</a>`:""}
    </div>`;
  }).join("");
}

function pickAttachment(){
  const input = document.getElementById("attachInput");
  input.value = "";
  input.onchange = () => { if(input.files[0]) uploadAttachment(input.files[0]); };
  input.click();
}
async function uploadAttachment(file){
  toast("Attaching " + trunc(file.name, 30) + "…");
  try{
    const content_base64 = await fileToBase64(file);
    const res = await API.addAttachment(CURRENT.contract.id, {filename:file.name, content_base64});
    if(res.error){ toast(res.error); return; }
    CURRENT = await API.get(CURRENT.contract.id);
    renderProfile();
    toast(res.dropbox && res.dropbox.saved ? "Attached and saved to Dropbox" : "Attached");
  }catch(e){ toast("Attach failed. Please try again."); }
}

function openDoc(id){
  const d = (CURRENT.documents||[]).find(x=>x.id===id);
  if(!d) return;
  ACTIVE_DOC = id;
  document.querySelectorAll(".docbox").forEach(b=>b.classList.remove("active"));
  const box = document.getElementById("box-"+id); if(box) box.classList.add("active");
  const details = d.details || {};
  let inner = "";
  if(details.changes) inner += kv("What changed", details.changes);
  if(details.payment_schedule && details.payment_schedule.length) inner += paymentTable(details.payment_schedule);
  if(details.full_text) inner += kv("Full text", details.full_text);
  if(!inner && d.summary) inner += kv("Summary", d.summary);
  if(!inner) inner = `<div class="kv"><div class="v muted">No extracted detail for this document.</div></div>`;

  const links = [];
  if(d.file_key) links.push(`<a class="rawbtn" href="file/${esc(d.file_key)}" target="_blank"><i data-lucide="file-text" style="width:14px;height:14px"></i> View original document</a>`);
  if(d.drive_link) links.push(`<a class="rawbtn" href="${esc(d.drive_link)}" target="_blank"><i data-lucide="external-link" style="width:14px;height:14px"></i> Open in ${storageLabel(d.drive_link)}</a>`);

  const panel = document.getElementById("docpanel");
  panel.style.display = "block";
  panel.innerHTML = `<div class="card docpanel">
    <div class="hd"><span style="display:flex;align-items:center;gap:9px"><i data-lucide="file-text"></i> ${esc(d.name||d.doc_type)}</span>
      <i data-lucide="x" class="x" onclick="closeDoc()"></i></div>
    <div class="bd">${inner}<div style="display:flex;gap:16px;flex-wrap:wrap">${links.join("")}</div></div>
  </div>`;
  document.getElementById("split").classList.add("open");
  icons();
}
function closeDoc(){
  ACTIVE_DOC = null;
  const p = document.getElementById("docpanel"); if(p) p.style.display="none";
  const s = document.getElementById("split"); if(s) s.classList.remove("open");
  document.querySelectorAll(".docbox").forEach(b=>b.classList.remove("active"));
}
function kv(k,v){ return `<div class="kv"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`; }
function paymentTable(rows){
  const body = rows.map((r,i)=>`<tr><td>${i+1}</td><td>${esc(r.milestone||r.description||r.name||"")}</td><td class="n">${esc(r.fee||r.amount||"")}</td></tr>`).join("");
  return `<div class="kv"><div class="k">Payment schedule</div><table class="pay"><thead><tr><th>#</th><th>Milestone</th><th style="text-align:right">Fee</th></tr></thead><tbody>${body}</tbody></table></div>`;
}

// ---- notes ---------------------------------------------------------------

function notesHtml(){
  const notes = CURRENT.notes || [];
  if(notes.length===0) return `<div class="empty" style="padding:20px">No notes yet. Add the first one below.</div>`;
  return notes.map(n=>`<div class="note">
    <div class="meta"><span class="who"><i data-lucide="user-circle-2"></i>${esc(n.author||"Anonymous")}</span> · <span>${esc(fmtTime(n.created_at))}</span>
      <button class="notedel" title="Delete note" onclick="deleteNote('${n.id}')"><i data-lucide="trash-2"></i></button></div>
    <div class="body">${esc(n.body)}</div></div>`).join("");
}
function deleteNote(noteId){
  confirmModal({
    title: "Delete this note?",
    body: "This permanently removes the note. This cannot be undone.",
    confirmText: "Delete",
    danger: true,
    onConfirm: async () => {
      await API.delNote(CURRENT.contract.id, noteId);
      CURRENT = await API.get(CURRENT.contract.id);
      renderProfile();
      toast("Note deleted");
    },
  });
}
async function postNote(){
  const ta = document.getElementById("noteInput");
  const body = ta.value.trim();
  if(!body) return;
  // Make sure the note is attributed: if no name is set, ask for it first.
  if(!localStorage.getItem("contracts_user")){ setName(postNote); return; }
  await API.addNote(CURRENT.contract.id, {author:myName(), body});
  CURRENT = await API.get(CURRENT.contract.id);
  renderProfile();
  toast("Note added");
}

// ---- upload --------------------------------------------------------------

let CHOSEN_FILE = null;
function setupUpload(){
  const drop = document.getElementById("drop");
  const input = document.getElementById("fileInput");
  drop.addEventListener("click", ()=>input.click());
  input.addEventListener("change", ()=>{ if(input.files[0]) pickFile(input.files[0]); });
  ["dragover","dragenter"].forEach(ev=>drop.addEventListener(ev,e=>{e.preventDefault();drop.classList.add("drag");}));
  ["dragleave","drop"].forEach(ev=>drop.addEventListener(ev,e=>{e.preventDefault();drop.classList.remove("drag");}));
  drop.addEventListener("drop", e=>{ if(e.dataTransfer.files[0]) pickFile(e.dataTransfer.files[0]); });
}
function pickFile(f){
  CHOSEN_FILE = f;
  document.getElementById("fname").innerHTML = `<i data-lucide="paperclip" style="width:14px;height:14px;vertical-align:-2px"></i> ${esc(f.name)}`;
  icons();
}
function fileToBase64(file){
  return new Promise((resolve,reject)=>{
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(",")[1] || "");
    r.onerror = () => reject(r.error);
    r.readAsDataURL(file);
  });
}

async function runAnalyze(){
  if(!CHOSEN_FILE){ toast("Choose a file first"); return; }
  const btn = document.getElementById("analyzeBtn");
  btn.disabled = true; btn.innerHTML = `<span class="spin"></span> Analyzing…`;
  let res;
  try {
    const content_base64 = await fileToBase64(CHOSEN_FILE);
    res = await API.analyze({
      filename: CHOSEN_FILE.name,
      content_base64,
      owner: document.getElementById("owner").value,
      department: document.getElementById("uDept").value,
    });
  }
  catch(e){ res = {error:"Upload failed. Please try again."}; }
  btn.disabled = false; btn.innerHTML = `<i data-lucide="sparkles"></i> Analyze & add contract`;
  icons();
  if(res.error){ showResult(`<div class="detected" style="background:#FEF2F2;border-color:#FECACA"><i data-lucide="alert-triangle" style="color:#DC2626"></i><div>${esc(res.error)}</div></div>`); return; }
  STAGED = res; STAGED.chosen = (res.matches && res.matches[0]) ? res.matches[0].contract_id : null;
  renderAnalysis();
}

function renderAnalysis(){
  const p = STAGED.parsed;
  const isF = STAGED.is_followon;
  let html = `<div class="rh" style="border-bottom:1px solid var(--line);margin:-18px -18px 18px;padding:14px 18px"><i data-lucide="file-check-2"></i> ${esc(STAGED.source_filename)}</div>`;
  html += `<div class="detected"><i data-lucide="${isF?'git-branch':'file-plus-2'}"></i><div>Detected: <b>${esc(p.agreement_type||"Agreement")}</b>${isF?" — this document modifies an existing agreement.":" — a new agreement."}</div></div>`;

  if(isF){
    if(STAGED.matches && STAGED.matches.length){
      html += `<div class="match"><div class="q">We matched this to an existing contract</div>
        <div style="font-size:13px;color:var(--muted);margin-bottom:6px">Pick the right agreement to update, or choose a different one.</div>
        ${STAGED.matches.map(m=>matchRow(m)).join("")}
        <div class="matchbtns" style="margin-top:12px">
          <button class="btn primary" onclick="commit('followon')"><i data-lucide="check"></i> Update selected contract</button>
          <button class="btn" onclick="commit('new')"><i data-lucide="file-plus"></i> No — add as a new contract</button>
        </div></div>`;
    } else {
      html += `<div class="match"><div class="q">No matching original contract found</div>
        <div style="font-size:13px;color:var(--muted);margin:6px 0 12px">We couldn't find the original agreement this modifies. You can still add it as its own contract.</div>
        <button class="btn primary" onclick="commit('new')"><i data-lucide="file-plus"></i> Add as a new contract</button></div>`;
    }
  }

  html += `<div style="font-weight:600;margin:18px 0 8px;display:flex;align-items:center;gap:7px"><i data-lucide="sparkles" style="width:15px;height:15px;color:var(--orange)"></i> What we pulled from the document</div>`;
  html += `<div class="extracted">
    ${ex("Contracting party", p.contracting_party)}
    ${ex("Execution date", fmtDate(p.execution_date))}
    ${ex("Termination date", fmtDate(p.termination_date)||"—")}
    ${ex("Estimated value", (p.est_value||"—") + (p.est_value?(p.est_value_is_guess?" (best guess)":" (calculated)"):""))}
  </div>`;
  if(p.changes) html += kv("Changes this applies", p.changes);
  html += `<div class="hint" style="margin-top:14px"><i data-lucide="info"></i><span>${esc(p.description||"")}</span></div>`;

  if(!isF){
    html += `<div class="matchbtns" style="margin-top:16px"><button class="btn primary" onclick="commit('new')"><i data-lucide="check"></i> Add this contract</button></div>`;
  }
  html += `<div class="synced" id="synced"></div>`;
  showResult(html);
}

function matchRow(m){
  const sel = (STAGED.chosen===m.contract_id) ? " sel" : "";
  const conf = Math.round((m.confidence||0)*100);
  return `<div class="m${sel}" onclick="chooseMatch('${m.contract_id}')"><i data-lucide="${STAGED.chosen===m.contract_id?'check-circle-2':'link-2'}"></i>
    <div><div style="font-weight:600">${esc(m.contracting_party)} — ${esc(m.agreement_type)}</div>
    <div class="mt">Executed ${esc(fmtDate(m.execution_date))||"—"} · ${conf}% match · ${esc(m.reason)}</div></div></div>`;
}
function chooseMatch(id){ STAGED.chosen = id; renderAnalysis(); }
function ex(label, value){
  return `<div class="ex"><span class="k">${esc(label)}</span><span class="v">${esc(value)||'—'}</span></div>`;
}

let COMMITTING = false;
async function commit(decision){
  if(COMMITTING) return;              // guard against double-submit
  COMMITTING = true;
  document.querySelectorAll("#result .matchbtns .btn").forEach(b => b.disabled = true);
  const body = {analysis_id:STAGED.analysis_id, decision};
  if(decision==="followon") body.target_contract_id = STAGED.chosen;
  let res;
  try { res = await API.commit(body); }
  finally { COMMITTING = false; }
  const synced = document.getElementById("synced");
  if(res.error){ synced.className="synced warn show"; synced.innerHTML=`<i data-lucide="alert-triangle"></i> ${esc(res.error)}`; icons(); return; }
  const notionMsg = notionResultMessage(res.notion);
  const dbx = res.dropbox || {};
  let dbxText = "";
  if(dbx.saved) dbxText = "Saved to Dropbox. ";
  else if(dbx.error) dbxText = "Dropbox save skipped (" + dbx.error.slice(0,80) + "). ";
  const warn = notionMsg.warn || (!!dbx.error);
  synced.className = "synced show" + (warn?" warn":"");
  // Apply any tags chosen on the upload page to the new contract.
  if(decision === "new" && UPLOAD_TAGS.length && res.contract_id){
    try {
      const tr = await API.setTags(res.contract_id, UPLOAD_TAGS);
      AVAILABLE_TAGS = tr.available || AVAILABLE_TAGS;
    } catch(e){ /* best-effort */ }
  }
  UPLOAD_TAGS = [];
  renderUploadTags();
  synced.innerHTML = `<i data-lucide="${warn?'alert-triangle':'check-circle-2'}"></i> ${decision==="followon"?"Contract updated":"Contract added"}. ${esc(dbxText)}${esc(notionMsg.text)}`;
  icons();
  await loadContracts();
  toast(decision==="followon"?"Contract updated":"Contract added");
  setTimeout(()=>openProfile(res.contract_id), 900);
}

function notionResultMessage(n){
  if(!n) return {text:"", warn:false};
  if(n.skipped==="not_configured") return {text:"Set up the Notion database to mirror contracts there.", warn:false};
  if(n.error) return {text:"Notion: "+n.error, warn:true};
  if(n.ok || n.page_id) return {text:"Recorded to the Notion database.", warn:false};
  return {text:"", warn:false};
}

function showResult(html){
  document.getElementById("resultBody").innerHTML = html;
  document.getElementById("result").classList.add("show");
  icons();
  document.getElementById("result").scrollIntoView({behavior:"smooth", block:"nearest"});
}

// ---- notion sync ---------------------------------------------------------

async function syncNotion(){
  toast("Syncing to Notion…");
  const res = await API.syncNotion();
  if(res.error){ toast("Notion: " + res.error); return; }
  STATUS = await API.status(); updateFoot();
  toast(`Synced ${res.synced||0} contract${res.synced===1?"":"s"} to Notion`);
}

// ---- nav / util ----------------------------------------------------------

function goto(v){
  document.querySelectorAll(".view").forEach(s=>s.classList.remove("active"));
  document.getElementById("view-"+v).classList.add("active");
  document.querySelectorAll(".nav[data-goto]").forEach(n=>n.classList.toggle("active", n.dataset.goto===v));
  const titles={search:"Search contracts",upload:"Upload contract",profile:"Contract profile"};
  document.getElementById("pageTitle").textContent = titles[v];
  document.querySelector(".content").scrollTop = 0;
}
document.addEventListener("click",e=>{
  const g=e.target.closest("[data-goto]");
  if(g){ goto(g.dataset.goto); if(g.dataset.goto==="search") loadContracts(); }
  // Close the owner popover when clicking outside it.
  const of=document.getElementById("ownerFilter");
  if(of && of.classList.contains("open") && !e.target.closest("#ownerFilter")) of.classList.remove("open");
  const tf=document.getElementById("tagFilter");
  if(tf && tf.classList.contains("open") && !e.target.closest("#tagFilter")) tf.classList.remove("open");
});

function storageLabel(link){ return (link||"").includes("dropbox.com") ? "Dropbox" : "Drive"; }
function statusChip(status){
  const s = status || "Active";
  const cls = s === "Terminated" ? "terminated" : "active";
  const icon = s === "Terminated" ? "circle-slash" : "circle-check";
  return `<span class="chip ${cls}"><i data-lucide="${icon}" style="width:12px;height:12px"></i> ${esc(s)}</span>`;
}
async function changeStatus(value){
  await API.patch(CURRENT.contract.id, {status: value});
  CURRENT = await API.get(CURRENT.contract.id);
  await loadContracts();
  renderProfile();
  toast(value === "Terminated" ? "Marked terminated" : "Marked active");
}
function deleteContract(){
  const c = CURRENT.contract;
  confirmModal({
    title: "Delete this contract?",
    body:
      `This permanently deletes the contract with ${c.contracting_party || "this party"}, ` +
      `including its documents and all notes, and removes the copy saved in Dropbox. This cannot be undone.\n\n` +
      `(The row in Notion isn't removed automatically — delete it there too if you want.)`,
    confirmText: "Delete permanently",
    danger: true,
    onConfirm: async () => {
      const res = await API.del(c.id);
      if(res.error){ toast(res.error); return; }
      toast("Contract deleted");
      goto("search");
      await loadContracts();
    },
  });
}

function promptModal(opts){
  const wrap = document.createElement("div");
  wrap.className = "modal-wrap";
  wrap.innerHTML = `<div class="modal">
    <div class="modal-title" style="color:var(--ink)"><i data-lucide="user-round" style="color:var(--orange)"></i> ${esc(opts.title)}</div>
    ${opts.body?`<div class="modal-body">${esc(opts.body)}</div>`:""}
    <input class="modal-input" type="text" value="${esc(opts.value||"")}" placeholder="${esc(opts.placeholder||"")}">
    <div class="modal-actions">
      <button class="btn" data-x>Cancel</button>
      <button class="btn primary" data-ok>${esc(opts.confirmText||"Save")}</button>
    </div></div>`;
  document.body.appendChild(wrap);
  const input = wrap.querySelector(".modal-input");
  const close = () => wrap.remove();
  const submit = () => { const v = input.value; close(); opts.onConfirm(v); };
  wrap.addEventListener("click", e => { if(e.target === wrap) close(); });
  wrap.querySelector("[data-x]").onclick = close;
  wrap.querySelector("[data-ok]").onclick = submit;
  input.addEventListener("keydown", e => {
    if(e.key === "Enter"){ e.preventDefault(); submit(); }
    if(e.key === "Escape"){ e.preventDefault(); close(); }
  });
  icons();
  input.focus(); input.select();
}

function confirmModal(opts){
  const wrap = document.createElement("div");
  wrap.className = "modal-wrap";
  wrap.innerHTML = `<div class="modal">
    <div class="modal-title"><i data-lucide="alert-triangle"></i> ${esc(opts.title)}</div>
    <div class="modal-body">${esc(opts.body)}</div>
    <div class="modal-actions">
      <button class="btn" data-x>Cancel</button>
      <button class="btn ${opts.danger?"danger":"primary"}" data-ok>${esc(opts.confirmText||"Confirm")}</button>
    </div></div>`;
  document.body.appendChild(wrap);
  const close = () => wrap.remove();
  wrap.addEventListener("click", e => { if(e.target === wrap) close(); });
  wrap.querySelector("[data-x]").onclick = close;
  wrap.querySelector("[data-ok]").onclick = async () => { close(); await opts.onConfirm(); };
  icons();
}
function trunc(s,n){ s=s||""; return s.length>n ? s.slice(0,n-1)+"…" : s; }
function fmtDate(s){
  if(!s) return "";
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s);
  if(!m) return s;
  const months=["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  return `${months[+m[2]-1]} ${+m[3]}, ${m[1]}`;
}
function fmtTime(ts){
  if(!ts) return "";
  const d = new Date(ts*1000);
  return d.toLocaleString(undefined,{month:"short",day:"numeric",year:"numeric",hour:"numeric",minute:"2-digit"});
}
let toastT;
function toast(msg){
  const el=document.getElementById("toast");
  document.getElementById("toastMsg").textContent=msg;
  el.classList.add("show"); clearTimeout(toastT);
  toastT=setTimeout(()=>el.classList.remove("show"),2400);
}

setupUpload();
boot();
