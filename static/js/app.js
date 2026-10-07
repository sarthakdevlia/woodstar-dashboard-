"use strict";

/* ================================================================
   WoodStar job cards — the browser side.
   The server decides everything that matters (who may tick what, who
   sees money); this file draws the screens and mirrors the rules only
   to grey out buttons. Categories, steps and standard wording arrive
   with the page so they live in one place (jobs/catalog.py, stages.py).
================================================================= */
const BOOT = JSON.parse(document.getElementById('bootstrap').textContent);
const CATEGORIES = BOOT.categories, STAGES = BOOT.stages, SHOP = BOOT.shop, TEMPLATE_DEFAULTS = BOOT.templateDefaults;
const FIELD_LABEL = {brand:'Brand', thickness:'Thickness', size:'Size', pack:'Pack size', qty:'Quantity'};
const REFRESH_MS = 25000;                  // how often the page checks for other people's ticks

let ME = Object.assign({duties:[]}, BOOT.me);
let DB = {jobs:[], workers:[], roster:{}, sent:{}, templates:{}, whatsapp:null, staff:null};
let loaded = false;

/* ================================================================
   Talking to the server
================================================================= */
const csrf = () => (document.cookie.match(/(?:^|; )csrftoken=([^;]+)/) || [])[1] || '';
async function api(method, path, body){
  let res;
  try {
    res = await fetch('/api/v1/' + path, {method, credentials:'same-origin',
      headers:{'Content-Type':'application/json', 'X-CSRFToken':csrf()}, body: body === undefined ? undefined : JSON.stringify(body)});
  } catch (e) { throw new Error('No connection. Check the internet and try again.'); }
  let data = null;
  try { data = await res.json(); } catch (e) {}
  // signed out, or the account was switched off: back to the sign-in page
  if (res.status === 401 || (res.status === 403 && data && /credentials/i.test(data.message || ''))) { location.href = '/login/?next=/'; throw new Error('Please sign in again.'); }
  if (!res.ok || !data || !data.success) {
    const err = new Error((data && data.message) || 'Something went wrong. Please try again.');
    err.errors = data && data.errors; throw err;
  }
  return data.data;
}
function firstError(err){
  const walk = v => typeof v === 'string' ? v : Array.isArray(v) ? v.map(walk).find(Boolean) : v && typeof v === 'object' ? Object.values(v).map(walk).find(Boolean) : '';
  return (err.errors && walk(err.errors)) || err.message;
}
function takeTeam(t){ DB.workers = t.workers; DB.roster = t.roster; DB.sent = t.sent; ME.duties = ME.is_owner ? STAGES.map(s => s.key) : (t.roster[ME.id] || []); }
function takeJob(j){ const i = DB.jobs.findIndex(x => x.no === j.no); if (i < 0) DB.jobs.unshift(j); else DB.jobs[i] = j; state.detail = j; }
async function refresh(quiet){
  try {
    const s = await api('GET', 'state/' + (state.all ? '?scope=all' : ''));
    ME = s.me; DB.jobs = s.jobs; DB.templates = s.templates; DB.whatsapp = s.whatsapp; takeTeam(s); loaded = true;
    // don't redraw under someone who is typing
    const typing = document.activeElement && /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName) && document.activeElement.id !== 'q';
    if (!quiet || !typing) render();
    if (quiet && state.view === 'job' && state.job) loadJob(state.job, true);   // someone else may have ticked: refresh its audit trail too
  } catch (e) { if (!quiet) toast(e.message, true); }
}

/* ================================================================
   Helpers
================================================================= */
const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const cat = k => CATEGORIES.find(c => c.key === k);
const stage = k => STAGES.find(s => s.key === k);
const doneCount = j => STAGES.filter(s => j.stages[s.key]).length;
const nextStage = j => STAGES.find(s => !j.stages[s.key]);                       // the step it is waiting for
const statusOf = j => STAGES[Math.max(0, doneCount(j) - 1)];                     // the step it has reached
const rupees = n => '₹' + Number(n || 0).toLocaleString('en-IN');
const phoneFmt = p => p ? `+91 ${p.slice(0, 5)} ${p.slice(5)}` : '';
const dayLabel = d => new Date(d).toLocaleDateString('en-IN', {weekday:'long', day:'numeric', month:'long'});
const shortDate = iso => new Date(iso).toLocaleDateString('en-IN', {day:'numeric', month:'short'});
function when(iso){
  const d = new Date(iso), diff = (Date.now() - d) / 36e5;
  const t = d.toLocaleTimeString('en-IN', {hour:'numeric', minute:'2-digit'});
  if (diff < 24 && d.getDate() === new Date().getDate()) return `Today, ${t}`;
  if (diff < 48) return `Yesterday, ${t}`;
  return d.toLocaleDateString('en-IN', {day:'numeric', month:'short'}) + ', ' + t;
}
const itemsShort = j => j.items.map(i => `${cat(i.cat).label} ${i.thickness || i.size || i.pack || ''} × ${i.qty}`).join(', ');
function toast(msg, bad){ const t = $('#toast'); t.textContent = msg; t.className = 'show' + (bad ? ' bad' : ''); clearTimeout(t._h); t._h = setTimeout(() => t.className = '', 3600); }
const trackUrl = j => location.origin + j.track;
const fill = (text, values) => text.replace(/\{(\w+)\}/g, (m, k) => values[k] ?? m);

/* who is signed in, and what they may tick today */
const state = {view:'board', job:null, detail:null, q:'', all:false, draft:null, tplLang:{work:'en', thanks:'en', update:'en'}, tplDraft:{}};
const isOwner = () => ME.is_owner;
const duties = id => DB.roster[id] || [];
const myDuties = () => ME.duties || [];
const onDuty = key => DB.workers.filter(w => duties(w.id).includes(key));
const waitingFor = keys => DB.jobs.filter(j => { const n = nextStage(j); return n && keys.includes(n.key); });
const wording = (key, lang) => (DB.templates[key] || TEMPLATE_DEFAULTS[key])[lang];

function customerMessage(j){
  const lang = j.lang === 'hi' ? 'hi' : 'en', st = statusOf(j);
  return fill(wording('update', lang), {name:j.customer.name, job:j.no, status:lang === 'hi' ? st.labelHi : st.label, link:trackUrl(j), shop:SHOP.name});
}
const waCustomer = j => `https://wa.me/91${j.customer.phone}?text=${encodeURIComponent(customerMessage(j))}`;
/* The WhatsApp button on a job card. With the shop's own number connected it sends the update
   from that number; until then it opens WhatsApp on this phone with the message written. */
const waReady = () => !!(DB.whatsapp && DB.whatsapp.ready);
const waButton = j => waReady()
  ? '<button class="btn btn--wa" data-wasend="update">Send update on WhatsApp</button>'
  : `<a class="btn btn--wa" href="${waCustomer(j)}" target="_blank" rel="noopener">Send update on WhatsApp</a>`;
function workMessage(w){
  const d = duties(w.id), hi = w.lang === 'hi';
  // one line each: a template blank cannot hold line breaks
  const duty = d.map(k => hi ? stage(k).dutyHi : stage(k).duty).join(hi ? ' और ' : ' and ') || (hi ? 'आज कोई काम नहीं दिया गया' : 'no duty set for today');
  const jobs = waitingFor(d.filter(k => k !== 'received')).map(j => `${j.no} ${j.customer.name}`).join('; ') || (hi ? 'अभी कोई नहीं' : 'none yet');
  return fill(wording('work', w.lang) || wording('work', 'en'), {name:w.name, date:new Date().toLocaleDateString(hi ? 'hi-IN' : 'en-IN', {day:'numeric', month:'long'}), duty, jobs, shop:SHOP.name});
}

/* ================================================================
   Rules (mirrored from jobs/permissions.py — the server has the last word)
   - a worker ticks only the steps of the duty they were given today
   - steps go in order
   - only the owner un-ticks, changes duties, and sees money
================================================================= */
function can(j, key){
  const idx = STAGES.findIndex(s => s.key === key), st = STAGES[idx], done = !!j.stages[key];
  if (!myDuties().includes(key)) { const who = onDuty(key).map(w => w.name).join(' or '); return {ok:false, msg:`"${st.label}" is ${who ? who + "'s" : "nobody's"} duty today, not yours.`}; }
  if (done && !isOwner()) return {ok:false, msg:'Only the owner can undo a completed step.'};
  if (!done && idx > 0 && !j.stages[STAGES[idx - 1].key]) return {ok:false, msg:`"${STAGES[idx - 1].label}" has to be done first.`};
  if (done && STAGES.slice(idx + 1).some(s => j.stages[s.key])) return {ok:false, msg:'Undo the later steps first.'};
  return {ok:true};
}
async function toggle(j, key){
  const v = can(j, key); if (!v.ok) return toast(v.msg, true);
  const st = stage(key), undo = !!j.stages[key];
  try {
    takeJob(await api(undo ? 'DELETE' : 'PUT', `jobs/${j.number}/stages/${key}/`));
    toast(undo ? `${st.label} undone` : `${j.no}: ${st.label} ✓ — the customer's tracking page is updated`);
  } catch (e) { toast(e.message, true); }
  render();
}

/* ================================================================
   Views
================================================================= */
const VIEWS = [
  {k:'board', t:'Floor board'}, {k:'list', t:'All job cards'}, {k:'new', t:'+ New job card'}, {k:'team', t:"Today's team"},
  {k:'messages', t:'WhatsApp messages', owner:true}, {k:'staff', t:'Staff & logins', owner:true},
];
function nav(){
  $('#nav').innerHTML = VIEWS.filter(v => !v.owner || isOwner()).map(v => `<button data-view="${v.k}" class="${state.view === v.k || (v.k === 'list' && state.view === 'job') ? 'on' : ''}">${v.t}</button>`).join('');
  $('#who-duty').textContent = isOwner() ? 'Owner' : (myDuties().map(k => stage(k).label).join(' + ') || 'no duty today');
}
function go(view, job){
  state.view = view; state.job = job || null; location.hash = job ? `${view}/${job}` : view;
  if (view === 'job') loadJob(job);
  if (view === 'staff') loadStaff();
  render(); scrollTo(0, 0);
}
async function loadJob(no, again){
  if (!again) { if (state.detail && state.detail.no === no && state.detail.log) return; state.detail = null; }
  try {
    const j = await api('GET', `jobs/${no.replace(/\D/g, '')}/`); if (state.job !== no) return;
    const typing = document.activeElement && /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName);
    takeJob(j); if (!again || !typing) render();
  }
  catch (e) { if (state.job === no) { state.detail = {no, missing:true}; render(); } }
}
async function loadStaff(){
  if (!isOwner()) return;
  try { DB.staff = (await api('GET', 'staff/')).results; if (state.view === 'staff') render(); } catch (e) { toast(e.message, true); }
}
function filtered(){
  const q = state.q.trim().toLowerCase();
  return q ? DB.jobs.filter(j => [j.no, j.customer.name, j.customer.phone, j.site].join(' ').toLowerCase().includes(q)) : DB.jobs;
}

function viewBoard(){
  const jobs = filtered(), mine = myDuties();
  const open = jobs.filter(j => doneCount(j) < STAGES.length);
  const awaiting = jobs.filter(j => j.stages.ordered && !j.stages.arrived);
  const ready = jobs.filter(j => j.stages.arrived && !j.stages.dispatch);
  const late = open.filter(j => new Date(j.due) < new Date(new Date().toDateString()));
  const due = jobs.reduce((a, j) => a + Math.max(0, (j.amount || 0) - (j.advance || 0)), 0);
  const cols = STAGES.map(s => {
    const here = jobs.filter(j => statusOf(j).key === s.key).sort((a, b) => a.number - b.number);   // a card sits in the step it has reached
    return `<div class="col"><h3>${esc(s.label)} <span>${here.length}</span></h3>
      ${here.map(j => cardHTML(j, mine)).join('') || '<p class="muted" style="font-size:12px;margin:6px 4px">Nothing here.</p>'}</div>`;
  }).join('');
  const mineCount = isOwner() ? 0 : waitingFor(mine).length;
  return `<div class="row between"><div><h1 class="h1">Floor board</h1><p class="sub">${isOwner() ? 'Every order, in the step it has reached. Click a card to open it.'
      : mine.length ? `Your duty today: <b>${mine.map(k => stage(k).label).join(' and ')}</b>. ${mineCount} card${mineCount === 1 ? ' is' : 's are'} waiting for you (outlined in gold).` : 'You have no duty set for today — ask the owner.'}</p></div>
    ${myDuties().includes('received') ? '<button class="btn btn--gold" data-view="new">+ New job card</button>' : ''}</div>
    <div class="kpis">
      <div class="card kpi"><small>Open job cards</small><b>${open.length}</b></div>
      <div class="card kpi"><small>Waiting for material to arrive</small><b>${awaiting.length}</b></div>
      <div class="card kpi"><small>Material in, ready to dispatch</small><b>${ready.length}</b></div>
      ${isOwner() ? `<div class="card kpi"><small>Balance due (cards shown)</small><b class="mono">${rupees(due)}</b></div>` : `<div class="card kpi"><small>Past promised date</small><b>${late.length}</b></div>`}
    </div>
    ${jobs.length ? `<div class="board">${cols}</div>` : `<div class="card sec"><b>No job cards yet.</b><p class="sub">${state.q ? 'Nothing matches that search.' : 'The first one starts with "+ New job card".'}</p></div>`}`;
}
function cardHTML(j, mine){
  const n = nextStage(j), late = n && new Date(j.due) < new Date(new Date().toDateString());
  const forMe = n && !isOwner() && mine.includes(n.key);
  return `<div class="jc ${forMe ? 'mine' : ''}" data-open="${j.no}">
    <div class="no">${j.no}</div><div class="nm">${esc(j.customer.name)}</div>
    <div class="it">${esc(itemsShort(j))}</div>
    <div class="nx">${n ? `Next: <b>${esc(n.label)}</b>${onDuty(n.key).length ? ' · ' + esc(onDuty(n.key).map(w => w.name).join(', ')) : ''}` : '<span class="pill ok">Completed</span>'}</div>
    <div class="ft"><span>${esc(j.mode)} · due ${shortDate(j.due)}</span>${late ? '<span class="pill bad">Late</span>' : ''}</div></div>`;
}

function viewList(){
  const jobs = filtered().slice().sort((a, b) => b.number - a.number);
  return `<div class="row between"><div><h1 class="h1">All job cards</h1><p class="sub">${jobs.length} card${jobs.length === 1 ? '' : 's'}${state.q ? ` matching "${esc(state.q)}"` : ''}. ${state.all ? 'Showing every card.' : 'Cards delivered more than two weeks ago are hidden.'}
      <a href="#list" data-all>${state.all ? 'Hide old ones' : 'Show them'}</a></p></div>
    ${myDuties().includes('received') ? '<button class="btn btn--gold" data-view="new">+ New job card</button>' : ''}</div>
  <div class="card" style="margin-top:16px;overflow-x:auto"><table>
    <thead><tr><th>Job no.</th><th>Customer</th><th>Items</th><th>Progress</th><th>Now at</th><th>Due</th>${isOwner() ? '<th>Balance</th>' : ''}</tr></thead>
    <tbody>${jobs.map(j => { const n = doneCount(j), st = statusOf(j);
      return `<tr class="click" data-open="${j.no}"><td><b>${j.no}</b></td><td><b>${esc(j.customer.name)}</b><br><span class="muted">${phoneFmt(j.customer.phone)}</span></td>
      <td style="max-width:320px">${esc(itemsShort(j))}</td>
      <td><div class="prog">${STAGES.map(s => `<i class="${j.stages[s.key] ? 'on' : ''}"></i>`).join('')}</div></td>
      <td><span class="pill ${n === STAGES.length ? 'ok' : n >= 3 ? 'info' : ''}">${esc(st.label)}</span></td>
      <td class="mono">${shortDate(j.due)}</td>
      ${isOwner() ? `<td class="mono">${rupees(Math.max(0, j.amount - j.advance))}</td>` : ''}</tr>`; }).join('') || `<tr><td colspan="7" class="muted">No job cards match.</td></tr>`}</tbody></table></div>`;
}

const currentJob = () => DB.jobs.find(x => x.no === state.job) || (state.detail && state.detail.no === state.job && !state.detail.missing ? state.detail : null);
function viewJob(){
  const j = currentJob();
  if (!j) return state.detail && state.detail.missing ? `<p>Job card not found. <a href="#list" data-view="list">Back to all job cards</a></p>` : '<div class="loading">Opening the job card…</div>';
  const next = nextStage(j), log = state.detail && state.detail.no === j.no ? state.detail.log : null;
  const tiles = STAGES.map(s => {
    const d = j.stages[s.key], v = can(j, s.key), who = onDuty(s.key).map(w => w.name).join(', ');
    return `<div class="pstage ${d ? 'done' : ''} ${next && next.key === s.key ? 'next' : ''}">
      <div class="nm">${esc(s.label)}</div><div class="today">Today: ${esc(who || 'nobody assigned')}</div>
      <div class="who">${d ? `✓ ${esc(d.by)}<br>${when(d.at)}` : next && next.key === s.key ? 'Waiting for this step' : '—'}</div>
      <button class="btn btn--sm ${d ? '' : 'btn--dark'}" data-tick="${s.key}" ${v.ok ? '' : 'disabled'} title="${esc(v.ok ? '' : v.msg)}">${d ? 'Undo' : 'Mark done'}</button></div>`;
  }).join('');
  const rows = j.items.map(i => { const c = cat(i.cat); return `<tr><td>${c.label}</td><td>${esc(i.brand || '—')}</td><td>${esc(i.thickness || '—')}</td><td>${esc(i.size || i.pack || '—')}</td><td class="mono"><b>${i.qty}</b> ${c.unit}</td></tr>`; }).join('');
  return `<div class="row between"><div><p class="tag" style="margin:0"><a href="#list" data-view="list" style="text-decoration:none">← All job cards</a></p>
      <h1 class="h1">${j.no} · ${esc(j.customer.name)}</h1><p class="sub">Received ${when(j.createdAt)} · ${esc(j.mode)} · promised ${new Date(j.due).toLocaleDateString('en-IN', {weekday:'short', day:'numeric', month:'short'})}</p></div>
    <div class="row">${waButton(j)}<button class="btn" data-print="${j.no}">Print job card</button><a class="btn" href="${esc(j.track)}" target="_blank" rel="noopener">See what the customer sees</a></div></div>
  <div class="card sec" style="margin-top:16px"><div class="row between" style="margin-bottom:12px"><b>Process</b><span class="muted" style="font-size:12.5px">Each step is ticked by whoever has that duty today, in order. The customer's tracking page follows every tick.</span></div><div class="stages">${tiles}</div></div>
  <div class="detail">
    <div class="card"><div class="sec"><b>Items</b></div><div style="overflow-x:auto"><table class="items"><thead><tr><th>Category</th><th>Brand</th><th>Thickness</th><th>Size / pack</th><th>Quantity</th></tr></thead><tbody>${rows}</tbody></table></div>
      ${j.notes ? `<div class="sec"><span class="tag">Notes</span><p style="margin:6px 0 0">${esc(j.notes)}</p></div>` : ''}
      <div class="sec"><span class="tag">What the customer is sent</span><div class="bubble">${esc(customerMessage(j))}<small>WhatsApp · order update · ${j.lang === 'hi' ? 'Hindi' : 'English'}</small></div>
        ${waReady() ? `<p class="muted" style="font-size:12.5px;margin:10px 0 0">Goes from the shop's WhatsApp number when you press the green button. Nothing is sent by itself except the thank-you when the card is saved.</p>
        <div class="row" style="margin-top:10px"><button class="btn btn--sm" data-wasend="thanks">Send the thank-you again</button><a class="btn btn--sm btn--ghost" href="${waCustomer(j)}" target="_blank" rel="noopener">Open in my own WhatsApp instead</a></div>` : ''}</div></div>
    <div class="card">
      <div class="sec"><span class="tag">Customer</span><dl class="kv" style="margin:10px 0 0"><dt>Name</dt><dd>${esc(j.customer.name)}</dd><dt>Phone</dt><dd><a href="tel:+91${esc(j.customer.phone)}">${phoneFmt(j.customer.phone)}</a></dd>
        <dt>Site</dt><dd>${esc(j.site || '—')}</dd><dt>Carpenter / ref.</dt><dd>${esc(j.contractor || '—')}</dd></dl></div>
      ${isOwner() ? `<div class="sec"><span class="tag">Payment (owner only)</span><dl class="kv" style="margin:10px 0 0"><dt>Order value</dt><dd class="mono">${rupees(j.amount)}</dd><dt>Advance</dt><dd class="mono">${rupees(j.advance)}</dd><dt>Balance</dt><dd class="mono">${rupees(Math.max(0, j.amount - j.advance))} ${j.amount > 0 && j.amount - j.advance <= 0 ? '<span class="pill ok">Paid</span>' : ''}</dd></dl>
        <div class="pay"><label class="f">Order value (₹)<input id="p-amount" inputmode="numeric" value="${esc(j.amount || '')}"></label><label class="f">Received so far (₹)<input id="p-advance" inputmode="numeric" value="${esc(j.advance || '')}"></label><button class="btn btn--sm" data-pay style="margin-bottom:3px">Save</button></div></div>` : ''}
      <div class="sec"><span class="tag">Audit trail</span><ul class="audit">${log ? log.slice().reverse().map(l => `<li><span>${esc(l.txt)}</span><span>${when(l.at)}</span></li>`).join('') : '<li class="muted">Loading…</li>'}</ul></div>
    </div>
  </div>`;
}

/* ---- new job card */
function blankDraft(){ return {name:'', phone:'', site:'', contractor:'', mode:'Pickup', lang:'en', due:new Date(Date.now() + 864e5).toISOString().slice(0, 10), notes:'', amount:'', advance:'', items:[]}; }
function viewNew(){
  if (!myDuties().includes('received')) return `<h1 class="h1">New job card</h1><p class="sub">Job cards are created by whoever has "Order received" as their duty today${onDuty('received').length ? ` (${esc(onDuty('received').map(w => w.name).join(', '))})` : ''}, or by the owner.</p>`;
  const d = state.draft || (state.draft = blankDraft());
  const lines = d.items.map((it, i) => { const c = cat(it.cat);
    return `<div class="line" data-line="${i}"><div class="cat">${c.label}<small>${c.fields.map(f => FIELD_LABEL[f]).join(', ')}</small></div>
      ${c.fields.map(f => f === 'qty'
        ? `<label>Quantity<span class="qty"><input type="number" min="1" inputmode="numeric" data-f="qty" value="${esc(it.qty || '')}" placeholder="0"><span>${c.unit}</span></span></label>`
        : `<label>${FIELD_LABEL[f]}<input list="dl-${c.key}-${f}" data-f="${f}" value="${esc(it[f] || '')}" placeholder="Choose or type" maxlength="60"><datalist id="dl-${c.key}-${f}">${c[f].map(b => `<option value="${esc(b)}">`).join('')}</datalist></label>`
      ).join('')}
      ${'<span></span>'.repeat(Math.max(0, 4 - c.fields.length))}
      <button class="x" data-remove="${i}" title="Remove this item">×</button></div>`; }).join('');
  return `<div class="row between"><div><h1 class="h1">New job card</h1><p class="sub">Fill in the customer, add what they're buying, save — the card goes onto the floor board as "Order received".</p></div></div>
  <div class="card sec" style="margin-top:16px"><div class="form">
    <label class="f">Customer name *<input id="f-name" value="${esc(d.name)}" placeholder="e.g. Rajesh Meena" autocomplete="off" maxlength="120"><span class="err" id="e-name"></span></label>
    <label class="f">Phone (WhatsApp) *<input id="f-phone" value="${esc(d.phone)}" inputmode="numeric" maxlength="10" placeholder="10-digit mobile number"><span class="err" id="e-phone"></span></label>
    <label class="f">Site / purpose<input id="f-site" value="${esc(d.site)}" placeholder="e.g. Kunhadi — kitchen" maxlength="120"></label>
    <label class="f">Carpenter / reference<input id="f-contractor" value="${esc(d.contractor)}" placeholder="optional" maxlength="80"></label>
    <label class="f">Pickup or delivery<select id="f-mode">${['Pickup', 'Delivery'].map(m => `<option ${m === d.mode ? 'selected' : ''}>${m}</option>`).join('')}</select></label>
    <label class="f">Promised date<input type="date" id="f-due" value="${esc(d.due)}"><span class="err" id="e-due"></span></label>
    <label class="f">WhatsApp messages in<select id="f-lang">${BOOT.langs.map(([k, t]) => `<option value="${k}" ${k === d.lang ? 'selected' : ''}>${t}</option>`).join('')}</select></label>
  </div></div>
  <div class="card sec" style="margin-top:14px">
    <div class="row between"><b>Items</b><span class="muted" style="font-size:12.5px">Tap a category to add a line. Brands and sizes suggest as you type; anything else can be typed in.</span></div>
    <div class="catbar" style="margin-top:12px">${CATEGORIES.map(c => `<button class="catbtn" data-add="${c.key}">+ ${c.label}</button>`).join('')}</div>
    ${lines || '<p class="muted" style="margin:14px 0 0">No items yet.</p>'}<span class="err" id="e-items"></span>
  </div>
  <div class="card sec" style="margin-top:14px"><div class="form">
    <label class="f">Notes for the team<textarea id="f-notes" rows="2" maxlength="500" placeholder="e.g. customer wants delivery before 11 am">${esc(d.notes)}</textarea></label>
    ${isOwner() ? `<div class="form" style="grid-template-columns:1fr 1fr"><label class="f">Order value (₹)<input id="f-amount" inputmode="numeric" value="${esc(d.amount)}" placeholder="optional"></label><label class="f">Advance received (₹)<input id="f-advance" inputmode="numeric" value="${esc(d.advance)}" placeholder="optional"></label></div>` : '<p class="muted" style="margin:0">Order value and advance are entered by the owner.</p>'}
  </div></div>
  <div class="row" style="margin-top:16px;justify-content:flex-end"><button class="btn" data-clear>Clear</button><button class="btn btn--gold" data-create>Save job card</button></div>`;
}
function readDraft(){
  const d = state.draft; if (!d || state.view !== 'new') return;
  ['name', 'phone', 'site', 'contractor', 'mode', 'lang', 'due', 'notes', 'amount', 'advance'].forEach(k => { const el = $('#f-' + k); if (el) d[k] = el.value; });
  document.querySelectorAll('[data-line]').forEach(row => { const it = d.items[+row.dataset.line]; row.querySelectorAll('[data-f]').forEach(inp => it[inp.dataset.f] = inp.value); });
}
async function createJob(btn){
  readDraft(); const d = state.draft; let bad = false;
  const set = (id, msg) => { $(id).textContent = msg; if (msg) bad = true; };
  set('#e-name', d.name.trim() ? '' : 'Enter the customer\'s name.');
  set('#e-phone', /^[6-9]\d{9}$/.test(d.phone.trim()) ? '' : 'Enter a 10-digit mobile number.');
  set('#e-due', d.due ? '' : 'Choose the promised date.');
  set('#e-items', !d.items.length ? 'Add at least one item.' : d.items.some(i => !(+i.qty > 0)) ? 'Every item needs a quantity.' : '');
  if (bad) return toast('Check the highlighted fields.', true);
  const body = {customer_name:d.name.trim(), customer_phone:d.phone.trim(), site:d.site.trim(), contractor:d.contractor.trim(), mode:d.mode, lang:d.lang, due:d.due, notes:d.notes.trim(),
    items:d.items.map(i => { const o = {category:i.cat}; cat(i.cat).fields.forEach(f => o[f] = f === 'qty' ? Math.round(+i.qty) : (i[f] || '').trim()); return o; })};
  if (isOwner()) { body.amount = +d.amount || 0; body.advance = +d.advance || 0; }
  btn.disabled = true;
  try {
    const j = await api('POST', 'jobs/', body);
    takeJob(j); state.draft = null;
    const th = j.thanks || {};
    toast(th.sent ? `${j.no} created — thank-you sent to ${j.customer.name} on WhatsApp` : waReady() ? `${j.no} created. Thank-you not sent: ${th.reason}` : `${j.no} created — send the customer their tracking link`, !th.sent && waReady());
    go('job', j.no);
  } catch (e) { btn.disabled = false; toast(firstError(e), true); }
}

/* ---- today's team: who does what today, and the message each one gets */
function viewTeam(){
  const uncovered = STAGES.filter(s => !onDuty(s.key).length);
  const cards = DB.workers.map(w => {
    const d = duties(w.id), work = waitingFor(d.filter(k => k !== 'received')), s = DB.sent[w.id];
    return `<div class="card sec wk">
      <div class="wk__head"><div><div class="wk__name">${esc(w.name)}</div><div class="muted">${w.phone ? phoneFmt(w.phone) : 'no WhatsApp number'} · ${w.lang === 'hi' ? 'Hindi' : 'English'}</div></div>
        ${s ? `<span class="pill ok">Sent ${when(s.at).replace('Today, ', '')}</span>` : '<span class="pill warn">Not sent yet</span>'}</div>
      <span class="tag" style="margin-top:12px">Duty today</span>
      <div class="duties">${STAGES.map(st => `<button class="duty ${d.includes(st.key) ? 'on' : ''}" data-duty="${w.id}:${st.key}" ${isOwner() ? '' : 'disabled'}>${esc(st.label)}</button>`).join('')}</div>
      <span class="tag" style="margin-top:14px">Job cards waiting for ${esc(w.name)} (${work.length})</span>
      <ul class="worklist">${work.map(j => `<li><span><a href="#job/${j.no}" data-open="${j.no}"><b>${j.no}</b></a> ${esc(j.customer.name)} — ${esc(nextStage(j).label)}</span><span class="muted">${esc(j.mode)}</span></li>`).join('') || '<li class="muted">Nothing waiting right now.</li>'}</ul>
      ${isOwner() || w.id === ME.id ? `<span class="tag" style="margin-top:14px">Message ${esc(w.name)} gets</span>
      <div class="bubble">${esc(workMessage(w))}<small>WhatsApp · daily work</small></div>` : ''}
      ${isOwner() ? `<div class="row" style="margin-top:12px">${w.phone ? `<a class="btn btn--wa btn--sm" href="https://wa.me/91${esc(w.phone)}?text=${encodeURIComponent(workMessage(w))}" target="_blank" rel="noopener" data-sentone="${w.id}">Send to ${esc(w.name)} on WhatsApp</a>` : '<span class="muted" style="font-size:12.5px">Add a WhatsApp number under Staff &amp; logins to send this.</span>'}</div>` : ''}
    </div>`; }).join('');
  return `<div class="row between"><div><h1 class="h1">Today's team</h1><p class="sub">${dayLabel(new Date())} · ${DB.workers.length} worker${DB.workers.length === 1 ? '' : 's'}. ${isOwner() ? 'Tap a duty to give it or take it away; each worker ticks only the steps of their duty. Tomorrow starts with today\'s duties.' : 'Your duty for today is set by the owner.'}</p></div>
    ${isOwner() ? '<button class="btn" data-view="staff">Staff &amp; logins</button>' : ''}</div>
  ${DB.workers.length && uncovered.length ? `<p class="note" style="margin:14px 0 0;background:var(--warn-bg);border-color:#f3d7a6;color:#7a4a00"><b>Nobody is on ${uncovered.map(s => s.label).join(', ')} today.</b> Cards waiting for ${uncovered.length === 1 ? 'that step' : 'those steps'} can only be ticked by the owner.</p>` : ''}
  ${isOwner() ? '<p class="note" style="margin:14px 0 0">"Send" opens WhatsApp with the message ready, one worker at a time. Messages that go out by themselves every morning need WoodStar\'s own WhatsApp Business number connected.</p>' : ''}
  ${DB.workers.length ? `<div class="team">${cards}</div>` : `<div class="card sec" style="margin-top:16px"><b>No workers yet.</b><p class="sub">${isOwner() ? 'Add each worker under Staff &amp; logins; they then appear here to be given a duty.' : ''}</p></div>`}`;
}

/* ---- WhatsApp wording */
function checkWording(key, text){
  const T = TEMPLATE_DEFAULTS[key], used = [...text.matchAll(/\{(\w+)\}/g)].map(m => m[1]);
  const unknown = used.filter(b => !T.blanks.includes(b));
  if (unknown.length) return `{${unknown[0]}} is not one of the blanks.`;
  const missing = T.required.filter(b => !used.includes(b));
  if (missing.length) return `It must include {${missing[0]}}.`;
  if (/^\s*\{/.test(text) || /\}\s*$/.test(text)) return 'It cannot begin or end with a blank — WhatsApp refuses that.';
  if (text.trim().length < 30) return 'Too short — at least 30 characters.';
  if (text.trim().length > 700) return 'Too long — at most 700 characters.';
  return '';
}
const tplSample = () => ({name:'Ramesh', date:new Date().toLocaleDateString('en-IN', {day:'numeric', month:'long'}), duty:'Order material from suppliers', jobs:'WS-1045 Priya Agarwal; WS-1046 Suresh Kumawat', shop:SHOP.name,
  job:'WS-1043', status:'Material received', link:location.origin + '/t/…'});
const SENT_BY_NUMBER = ['thanks', 'update'];             // these go from the shop's number, so WhatsApp must approve them
const APPROVAL = {not_submitted:['warn', 'Not sent for approval'], pending:['info', "Waiting for WhatsApp's approval"], approved:['ok', 'Approved by WhatsApp'],
  rejected:['bad', 'Rejected by WhatsApp'], failed:['bad', 'Could not be submitted']};
function viewMessages(){
  if (!isOwner()) return '<h1 class="h1">WhatsApp messages</h1><p class="sub">Only the owner can change the wording.</p>';
  const wa = DB.whatsapp || {messages:{}};
  const block = key => { const T = TEMPLATE_DEFAULTS[key], lang = state.tplLang[key], saved = wording(key, lang), text = state.tplDraft[key + lang] ?? saved, problem = checkWording(key, text);
    const a = SENT_BY_NUMBER.includes(key) && wa.messages[key] ? wa.messages[key][lang] : null;
    const canSubmit = a && wa.ready && text === saved && !problem && a.status !== 'pending' && (a.status === 'not_submitted' || a.status === 'failed' || a.edited);
    const approval = !a ? '' : `<div class="row" style="margin-top:14px;padding-top:12px;border-top:1px solid var(--line)">
        <span class="pill ${APPROVAL[a.status][0]}">${APPROVAL[a.status][1]}</span>
        ${a.edited && a.status !== 'not_submitted' ? '<span class="pill warn">Wording changed since — submit it again</span>' : ''}
        <button class="btn btn--wa btn--sm" data-wasubmit="${key}" ${canSubmit ? '' : 'disabled'}>Submit to WhatsApp for approval</button></div>
      ${a.reason ? `<p class="err" style="margin:8px 0 0">${esc(a.reason)}</p>` : ''}
      <p class="muted" style="font-size:12.5px;margin:8px 0 0">${!wa.ready ? "Approval starts once the shop's number is connected." : a.status === 'approved' && !a.edited ? 'This is the wording customers are sent.'
        : a.in_use ? 'The earlier approved wording keeps going out until this one is approved.' : 'Nothing can be sent in this language until WhatsApp approves it; the other language is used if that one is approved.'}</p>`;
    return `<div class="card sec" style="margin-top:16px"><div class="row between"><div><b style="font-size:16px">${T.title}</b><div class="muted">Goes to: ${T.to}</div></div>
      <div class="row"><div class="tabs"><button class="${lang === 'en' ? 'on' : ''}" data-tpllang="${key}:en">English</button><button class="${lang === 'hi' ? 'on' : ''}" data-tpllang="${key}:hi">Hindi</button></div>
        <span class="pill ${text === saved ? 'ok' : 'warn'}" data-tplstate="${key}">${text === saved ? (saved === T[lang] ? 'Standard wording' : 'Your wording') : 'Not saved'}</span></div></div>
      <div class="tpl" style="margin-top:14px"><div>
        <label class="f">Wording<textarea rows="5" data-tpl="${key}">${esc(text)}</textarea></label>
        <span class="err" data-tplerr="${key}">${esc(problem)}</span>
        <div class="tag" style="margin:4px 0 6px">Blanks you can use (tap to add)</div>
        <div class="blanks">${T.blanks.map(b => `<button class="blank" data-blank="${key}:${b}">{${b}}</button>`).join('')}</div>
        <div class="row" style="margin-top:12px"><button class="btn btn--dark btn--sm" data-tplsave="${key}">Save wording</button><button class="btn btn--sm" data-tplreset="${key}">Back to the standard wording</button></div>
        ${approval}
      </div><div><span class="tag">Preview</span><div class="bubble" data-tplprev="${key}">${esc(fill(text, tplSample()))}<small>WhatsApp</small></div>
        <p class="muted" style="font-size:12.5px;margin:10px 0 0">${a ? 'Sent from the shop\'s WhatsApp number. A changed wording has to be approved by WhatsApp again before it is used.' : "This wording fills the message when you press a worker's WhatsApp button in Today's team; it opens in your own WhatsApp."}</p></div></div></div>`; };
  const top = !wa.ready
    ? `<p class="note" style="margin:14px 0 0">Sending from the shop's own WhatsApp number is not switched on yet. Until it is, the WhatsApp buttons open WhatsApp on your own phone with the message written, and no thank-you goes by itself.</p>`
    : `<div class="card sec" style="margin-top:14px"><div class="row between"><div><b>The shop's WhatsApp number is connected</b><div class="muted">The thank-you goes by itself when a job card is saved. An order update goes only when someone presses the WhatsApp button on a job card.</div></div>
        <button class="btn btn--sm" data-warefresh>Check approvals now</button></div>
      <div class="row between" style="margin-top:12px;padding-top:12px;border-top:1px solid var(--line)"><div><b>Customers asking where their order is</b>
        <div class="muted">${wa.replies_on ? 'Anyone who messages the number is sent the step their order has reached, found by their phone number.' : 'Switch this on and anyone who messages the number is sent the step their order has reached, found by their phone number.'}</div></div>
        ${wa.replies_on ? '<span class="pill ok">On</span>' : `<button class="btn btn--dark btn--sm" data-wareplies ${wa.can_reply ? '' : 'disabled title="Needs WHATSAPP_WEBHOOK_SECRET in the service settings"'}>Switch on</button>`}</div></div>`;
  return `<h1 class="h1">WhatsApp messages</h1><p class="sub">The messages the dashboard writes. Words in {curly brackets} are filled in for each person.</p>
  ${top}
  ${block('thanks')}${block('update')}${block('work')}`;
}

/* ---- staff & logins (owner) */
function viewStaff(){
  if (!isOwner()) return '<h1 class="h1">Staff &amp; logins</h1><p class="sub">Only the owner can manage accounts.</p>';
  const rows = (DB.staff || []).map(u => `<tr class="${u.is_active ? '' : 'off'}">
      <td><b>${esc(u.name)}</b><br><span class="muted">${esc(u.username)}</span></td>
      <td><select data-staff="${u.id}:role">${BOOT.roles.map(([k, t]) => `<option value="${k}" ${u.role === k ? 'selected' : ''}>${t}</option>`).join('')}</select></td>
      <td><input class="in" style="width:130px" inputmode="numeric" maxlength="10" value="${esc(u.phone)}" placeholder="10-digit number" data-staff="${u.id}:phone"></td>
      <td><select data-staff="${u.id}:lang">${BOOT.langs.map(([k, t]) => `<option value="${k}" ${u.lang === k ? 'selected' : ''}>${t}</option>`).join('')}</select></td>
      <td>${u.is_active ? '<span class="pill ok">Can sign in</span>' : '<span class="pill">Switched off</span>'}</td>
      <td style="white-space:nowrap"><button class="btn btn--sm" data-staffpass="${u.id}">New password</button> <button class="btn btn--sm btn--ghost" data-staffactive="${u.id}">${u.is_active ? 'Switch off' : 'Switch on'}</button></td></tr>`).join('');
  return `<div class="row between"><div><h1 class="h1">Staff &amp; logins</h1><p class="sub">Everyone who can sign in. An <b>Owner</b> sees money, sets duties and manages accounts; a <b>Worker</b> ticks the steps of the duty they are given in Today's team.</p></div>
    <button class="btn" data-view="team">Today's team</button></div>
  <div class="card" style="margin-top:16px;overflow-x:auto"><table class="staff">
    <thead><tr><th>Name / username</th><th>Role</th><th>WhatsApp number</th><th>Message language</th><th>Status</th><th></th></tr></thead>
    <tbody>${DB.staff ? rows : '<tr><td colspan="6" class="muted">Loading…</td></tr>'}</tbody></table></div>
  <div class="card sec" style="margin-top:16px"><b>Add a person</b>
    <div class="form" style="grid-template-columns:repeat(3,1fr);margin-top:12px">
      <label class="f">Name<input id="s-name" placeholder="e.g. Ramesh" maxlength="80" autocomplete="off"></label>
      <label class="f">Username (to sign in)<input id="s-username" placeholder="e.g. ramesh" autocapitalize="none" autocomplete="off" maxlength="30"></label>
      <label class="f">Password (8 or more characters)<input id="s-password" type="text" autocomplete="off" maxlength="128"></label>
      <label class="f">Role<select id="s-role">${BOOT.roles.slice().reverse().map(([k, t]) => `<option value="${k}">${t}</option>`).join('')}</select></label>
      <label class="f">WhatsApp number<input id="s-phone" inputmode="numeric" maxlength="10" placeholder="10-digit mobile number"></label>
      <label class="f">Message language<select id="s-lang">${BOOT.langs.slice().reverse().map(([k, t]) => `<option value="${k}">${t}</option>`).join('')}</select></label>
    </div>
    <div class="row" style="margin-top:12px"><button class="btn btn--dark" data-staffadd>Add and let them sign in</button><span class="err" id="s-err"></span></div>
    <p class="muted" style="font-size:12.5px;margin:10px 0 0">Tell them their username and password yourself. A person who leaves is switched off, not deleted, so the audit trail keeps their name.</p></div>`;
}
async function patchStaff(id, body, done){
  try { const u = await api('PATCH', `staff/${id}/`, body); DB.staff = DB.staff.map(x => x.id === u.id ? u : x); toast(done || 'Saved.'); await refresh(true); }
  catch (e) { toast(firstError(e), true); }
  render();
}

function printJob(no){
  const j = DB.jobs.find(x => x.no === no);
  $('#print').innerHTML = `<div class="pc"><div style="display:flex;justify-content:space-between"><div><h2>${esc(SHOP.name)}</h2><div>${esc(SHOP.address)} · ${esc(SHOP.phone)}</div></div><div style="text-align:right"><h2>${j.no}</h2><div>${new Date(j.createdAt).toLocaleDateString('en-IN')}</div></div></div>
    <p><b>Customer:</b> ${esc(j.customer.name)} · ${phoneFmt(j.customer.phone)}<br><b>Site:</b> ${esc(j.site || '—')} · <b>${esc(j.mode)}</b> · promised ${new Date(j.due).toLocaleDateString('en-IN')}</p>
    <table style="width:100%;border-collapse:collapse"><tr><th>Category</th><th>Brand</th><th>Thickness</th><th>Size / pack</th><th>Qty</th></tr>
    ${j.items.map(i => `<tr><td>${cat(i.cat).label}</td><td>${esc(i.brand || '—')}</td><td>${esc(i.thickness || '—')}</td><td>${esc(i.size || i.pack || '—')}</td><td>${i.qty} ${cat(i.cat).unit}</td></tr>`).join('')}</table>
    ${j.notes ? `<p><b>Notes:</b> ${esc(j.notes)}</p>` : ''}
    <table style="width:100%;border-collapse:collapse;margin-top:10px"><tr>${STAGES.map(s => `<th>${s.label}</th>`).join('')}</tr><tr>${STAGES.map(s => `<td style="height:34px">${j.stages[s.key] ? '✓ ' + esc(j.stages[s.key].by) : ''}</td>`).join('')}</tr></table>
    <p style="margin-top:10px">Track your order: ${esc(trackUrl(j))}</p></div>`;
  print();
}

/* ================================================================
   Router + events
================================================================= */
function render(){
  nav();
  if (!loaded) return;
  const v = state.view;
  $('#app').innerHTML = v === 'board' ? viewBoard() : v === 'list' ? viewList() : v === 'job' ? viewJob() : v === 'new' ? viewNew() : v === 'team' ? viewTeam() : v === 'messages' ? viewMessages() : viewStaff();
}
function fromHash(){
  const [v, j] = location.hash.replace('#', '').split('/');
  state.view = ['board', 'list', 'job', 'new', 'team', 'messages', 'staff'].includes(v) ? v : 'board';
  state.job = j || null;
  if (state.view === 'job' && state.job) loadJob(state.job);
  if (state.view === 'staff') loadStaff();
  render();
}
$('#q').addEventListener('input', e => { state.q = e.target.value; if (!['board', 'list'].includes(state.view)) state.view = 'list'; render(); });
document.addEventListener('input', e => {
  const key = e.target.dataset.tpl; if (!key) return;                       // live preview while the wording is typed
  const lang = state.tplLang[key], saved = wording(key, lang); state.tplDraft[key + lang] = e.target.value;
  $(`[data-tplerr="${key}"]`).textContent = checkWording(key, e.target.value);
  $(`[data-tplprev="${key}"]`).innerHTML = esc(fill(e.target.value, tplSample())) + '<small>WhatsApp</small>';
  const pill = $(`[data-tplstate="${key}"]`); pill.textContent = e.target.value === saved ? 'Saved' : 'Not saved'; pill.className = 'pill ' + (e.target.value === saved ? 'ok' : 'warn');
});
document.addEventListener('change', e => {
  const s = e.target.dataset.staff; if (!s) return;
  const [id, field] = s.split(':'), value = e.target.value.trim();
  if (field === 'phone' && value && !/^[6-9]\d{9}$/.test(value)) { toast('Enter a 10-digit mobile number.', true); return; }
  patchStaff(+id, {[field]:value});
});
document.addEventListener('click', async e => {
  const t = e.target.closest('[data-view],[data-open],[data-tick],[data-add],[data-remove],[data-create],[data-clear],[data-print],[data-all],[data-pay],[data-duty],[data-sentone],[data-tpllang],[data-blank],[data-tplsave],[data-tplreset],[data-wasend],[data-wasubmit],[data-warefresh],[data-wareplies],[data-staffadd],[data-staffpass],[data-staffactive]');
  if (!t) return;
  const d = t.dataset;
  if (d.view) { e.preventDefault(); readDraft(); return go(d.view); }
  if (d.open) { e.preventDefault(); return go('job', d.open); }
  if (d.tick) return toggle(currentJob(), d.tick);
  if (d.add) { readDraft(); const c = cat(d.add), it = {cat:c.key}; c.fields.forEach(f => it[f] = ''); state.draft.items.push(it); render();
    const rows = document.querySelectorAll('[data-line]'); rows[rows.length - 1].querySelector('input').focus(); return; }
  if (d.remove) { readDraft(); state.draft.items.splice(+d.remove, 1); return render(); }
  if ('create' in d) return createJob(t);
  if ('clear' in d) { state.draft = null; return render(); }
  if (d.print) return printJob(d.print);
  if ('all' in d) { e.preventDefault(); state.all = !state.all; return refresh(); }
  if ('pay' in d) {
    const amount = +$('#p-amount').value || 0, advance = +$('#p-advance').value || 0;
    if (amount < 0 || advance < 0) return toast('Amounts cannot be negative.', true);
    try { takeJob(await api('PATCH', `jobs/${state.job.replace(/\D/g, '')}/`, {amount, advance})); toast('Payment saved.'); } catch (err) { toast(firstError(err), true); }
    return render();
  }
  if (d.duty) {
    if (!isOwner()) return;
    const [id, key] = d.duty.split(':');
    try { takeTeam(await api('PUT', 'roster/', {user_id:+id, stage:key, on:!duties(id).includes(key)})); } catch (err) { toast(err.message, true); }
    return render();
  }
  if (d.sentone) {                                                          // the link itself opens WhatsApp
    try { takeTeam(await api('POST', 'roster/sent/', {user_ids:[+d.sentone]})); } catch (err) { toast(err.message, true); }
    return render();
  }
  if (d.wasend) {                                                           // from the shop's number, now
    const j = currentJob(); t.disabled = true;
    try { takeJob(await api('POST', `jobs/${j.number}/messages/`, {kind:d.wasend})); toast(d.wasend === 'thanks' ? `Thank-you sent to ${j.customer.name}` : `Update sent to ${j.customer.name}: ${statusOf(j).label}`); }
    catch (err) { toast(err.message, true); loadJob(j.no, true); }
    return render();
  }
  if (d.wasubmit || 'warefresh' in d || 'wareplies' in d) {
    if ('wareplies' in d && !confirm("Switch this on only if this WhatsApp number is used for WoodStar alone.\n\nEvery message sent to the number will be answered from here with the sender's order status. Continue?")) return;
    const path = d.wasubmit ? `whatsapp/templates/${d.wasubmit}/${state.tplLang[d.wasubmit]}/submit/` : 'warefresh' in d ? 'whatsapp/refresh/' : 'whatsapp/replies/';
    t.disabled = true;
    try { const r = await api('POST', path); DB.templates = r.templates; DB.whatsapp = r.whatsapp; toast(d.wasubmit ? 'Sent to WhatsApp for approval. It usually answers within a few hours.' : 'warefresh' in d ? 'Approvals checked.' : 'Replies are on.'); }
    catch (err) { toast(err.message, true); await refresh(true); }
    return render();
  }
  if (d.tpllang) { const [key, lang] = d.tpllang.split(':'); state.tplLang[key] = lang; return render(); }
  if (d.blank) { const [key, b] = d.blank.split(':'), ta = $(`[data-tpl="${key}"]`);
    const at = ta.selectionStart ?? ta.value.length; ta.value = ta.value.slice(0, at) + `{${b}}` + ta.value.slice(ta.selectionEnd ?? at);
    ta.dispatchEvent(new Event('input', {bubbles:true})); ta.focus(); return; }
  if (d.tplsave) {
    const key = d.tplsave, lang = state.tplLang[key], text = $(`[data-tpl="${key}"]`).value, problem = checkWording(key, text);
    if (problem) return toast(problem, true);
    try { DB.templates = await api('PUT', `templates/${key}/${lang}/`, {body:text}); delete state.tplDraft[key + lang]; toast('Wording saved.'); } catch (err) { toast(firstError(err), true); }
    return render();
  }
  if (d.tplreset) {
    const key = d.tplreset, lang = state.tplLang[key];
    try { DB.templates = await api('DELETE', `templates/${key}/${lang}/`); delete state.tplDraft[key + lang]; toast('Back to the standard wording.'); } catch (err) { toast(err.message, true); }
    return render();
  }
  if ('staffadd' in d) {
    const v = id => $(id).value.trim(), body = {name:v('#s-name'), username:v('#s-username'), password:$('#s-password').value, role:v('#s-role'), phone:v('#s-phone'), lang:v('#s-lang')};
    const problem = !body.name ? 'Enter their name.' : !/^[a-zA-Z0-9_.-]{3,30}$/.test(body.username) ? 'Username: 3–30 letters, numbers, dots, dashes or underscores.'
      : body.password.length < 8 ? 'The password needs 8 or more characters.' : body.phone && !/^[6-9]\d{9}$/.test(body.phone) ? 'Enter a 10-digit mobile number.' : '';
    if (problem) return $('#s-err').textContent = problem;
    try { const u = await api('POST', 'staff/', body); DB.staff = (DB.staff || []).concat(u).sort((a, b) => a.name.localeCompare(b.name)); toast(`${u.name} can now sign in.`); await refresh(true); render(); }
    catch (err) { $('#s-err').textContent = firstError(err); }
    return;
  }
  if (d.staffpass) {
    const u = DB.staff.find(x => x.id === +d.staffpass), pw = prompt(`New password for ${u.name} (8 or more characters):`);
    if (pw === null) return;
    if (pw.length < 8) return toast('The password needs 8 or more characters.', true);
    return patchStaff(u.id, {password:pw}, `Password changed for ${u.name}.`);
  }
  if (d.staffactive) {
    const u = DB.staff.find(x => x.id === +d.staffactive);
    if (u.is_active && !confirm(`Switch off ${u.name}'s login? They will be signed out and taken off the team.`)) return;
    return patchStaff(u.id, {is_active:!u.is_active}, u.is_active ? `${u.name} can no longer sign in.` : `${u.name} can sign in again.`);
  }
});
addEventListener('hashchange', () => { const want = location.hash.replace('#', ''); if (want !== `${state.view}${state.job ? '/' + state.job : ''}`) fromHash(); });
// stay in step with the other phones and the counter computer
setInterval(() => { if (document.visibilityState === 'visible') refresh(true); }, REFRESH_MS);
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') refresh(true); });

fromHash();
refresh().then(() => { if (state.view === 'job' && state.job) loadJob(state.job); });
