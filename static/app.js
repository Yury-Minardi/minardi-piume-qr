'use strict';
const state={csrf:'', qrs:[], dashboard:null, view:'dashboard', editSlug:null, forcePasswordChange:false};
const $=s=>document.querySelector(s);
const $$=s=>[...document.querySelectorAll(s)];
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num=n=>new Intl.NumberFormat('it-IT').format(n||0);
let toastTimer;
function toast(s){const t=$('#toast');t.textContent=s;t.classList.add('show');clearTimeout(toastTimer);toastTimer=setTimeout(()=>t.classList.remove('show'),3500)}
async function api(url,method='GET',payload){const opts={method,headers:{}};if(payload!==undefined){opts.headers['Content-Type']='application/json';opts.body=JSON.stringify(payload)}if(method!=='GET')opts.headers['X-CSRF-Token']=state.csrf;let r=await fetch(url,opts);if(r.status===401){location.href='/login';throw Error('Sessione scaduta')}if(!r.ok){let err;try{err=(await r.json()).detail}catch{err='Errore server'}throw Error(typeof err==='string'?err:'Richiesta non valida')}return r.json()}
function query(){let p=$('#period-filter').value;let q=new URLSearchParams({slug:$('#qr-filter').value});if(p==='custom'){q.set('start',$('#from').value);q.set('end',$('#to').value)}else q.set('period',p);return q.toString()}
function view(name){state.view=name;$('#dashboard-view').hidden=name!=='dashboard';$('#qrs-view').hidden=name!=='qrs';$('#top-location').textContent=name==='dashboard'?'Panoramica':'I miei QR Code';$$('.nav-item[data-view]').forEach(n=>n.classList.toggle('active',n.dataset.view===name));$('#sidebar').classList.remove('open');if(name==='qrs')renderQRs();window.scrollTo({top:0,behavior:'smooth'})}
async function loadQRs(){state.qrs=await api('/api/qrs');const select=$('#qr-filter'),old=select.value;select.innerHTML='<option value="all">Tutti i QR code</option>'+state.qrs.map(q=>`<option value="${esc(q.slug)}">${esc(q.name)}</option>`).join('');if(state.qrs.some(q=>q.slug===old))select.value=old;renderQRs()}
async function loadDashboard(){try{let d=await api('/api/dashboard?'+query());state.dashboard=d;$('#kpi-qrs').textContent=num(d.total_qrs);$('#kpi-scans').textContent=num(d.total_scans);$('#kpi-unique').textContent=num(d.unique_scans);$('#kpi-avg').textContent=num(d.total_qrs?Number((d.total_scans/d.total_qrs).toFixed(1)):0);$('#demo-banner').hidden=!d.demo_scans;$('#demo-count').textContent=num(d.demo_scans);renderChart(d.series);renderBars('#os-list',d.systems);renderTable('#countries',d.countries);renderTable('#cities',d.cities);renderQRStats(d.qr_scans);renderRecent(d.recent)}catch(e){toast('Impossibile caricare le statistiche: '+e.message)}}
function renderChart(series){const el=$('#chart');if(!series.length||!series.some(x=>x.scans)){el.innerHTML='<div class="empty-chart">Nessuna scansione nel periodo selezionato.</div>';return}
 const w=700,h=208,L=35,R=13,T=17,B=30;const cw=w-L-R,ch=h-T-B;
 const values=series.map(s=>s.scans),unique=series.map(s=>s.unique),max=Math.max(4,Math.ceil(Math.max(...values)/4)*4);
 const x=i=>L+(series.length===1?cw/2:cw*i/(series.length-1));const y=v=>T+ch-(v/max)*ch;
 const points=(arr)=>arr.map((v,i)=>`${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
 const ticks=[0,1,2,3,4].map(i=>{let val=Math.round(max*i/4),yy=y(max*i/4);return `<line x1="${L}" y1="${yy}" x2="${w-R}" y2="${yy}" stroke="#ecf1f2"/><text x="${L-12}" y="${yy+4}" font-size="10" text-anchor="end" fill="#9eabb1">${val}</text>`}).join('');
 const fmt=s=>{const parts=s.split('-');return parts[2]+'/'+parts[1]};let n=series.length,step=Math.max(1,Math.ceil(n/6));let labels=series.map((v,i)=>(i%step===0||i===n-1)?`<text x="${x(i)}" y="${h-9}" text-anchor="middle" font-size="10" fill="#9cacb3">${fmt(v.date)}</text>`:'').join('');
 const last=series.length-1;el.innerHTML=`<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Grafico scansioni giornaliere"><defs><linearGradient id="areaGradient" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#22b18e" stop-opacity=".20"/><stop offset="1" stop-color="#22b18e" stop-opacity="0"/></linearGradient></defs>${ticks}<polygon points="${x(0)},${y(0)} ${points(values)} ${x(last)},${y(0)}" fill="url(#areaGradient)"/><polyline fill="none" stroke="#abd5d1" stroke-width="2.2" stroke-dasharray="5 5" stroke-linejoin="round" points="${points(unique)}"/><polyline fill="none" stroke="#1cad8d" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" points="${points(values)}"/>${labels}</svg>`;
}
function renderBars(selector,rows){let el=$(selector);if(!rows.length){el.innerHTML='<div class="empty">Nessuna scansione registrata.</div>';return}el.innerHTML=rows.map(x=>`<div class="bar-item"><div class="bar-item-top"><span>${esc(x.name)}</span><b>${num(x.count)} <span style="color:#a9b8be">(${x.percent}%)</span></b></div><div class="bar-bg"><div class="bar-val" style="width:${x.percent}%"></div></div></div>`).join('')}
function renderTable(selector,rows){$(selector).innerHTML=rows.length?rows.map(x=>`<div class="table-row"><span>${esc(x.name==='IT'?'🇮🇹 Italia':x.name==='DE'?'🇩🇪 Germania':x.name)}</span><span class="num">${num(x.count)}</span><span class="pct">${x.percent}%</span></div>`).join(''):'<div class="empty">Nessun dato disponibile.</div>'}
function renderQRStats(rows){$('#qr-stats').innerHTML=rows.length?rows.map(x=>`<div class="table-row"><span>${esc(x.name)}</span><span class="num">${num(x.count)}</span><span class="pct">${x.percent}%</span></div>`).join(''):'<div class="empty">Ancora nessuna scansione. Condividi il QR tracciato per cominciare.</div>'}
function renderRecent(rows){$('#recent').innerHTML=rows.length?rows.map(r=>`<tr><td>${esc(r.time)}</td><td>${esc(r.name)}</td><td>${esc(r.os)}</td><td>${esc(r.browser)}</td><td>${esc(r.country)}</td><td>${r.demo?'<span class="type-demo">Demo</span>':'<span class="type-live">Reale</span>'}</td></tr>`).join(''):'<tr><td colspan="6">Nessuna scansione nel periodo selezionato.</td></tr>'}
function renderQRs(){const el=$('#qr-card-list');el.innerHTML=state.qrs.length?state.qrs.map(q=>`<article class="qr-card"><div class="qr-visual"><img src="/api/qrs/${encodeURIComponent(q.slug)}/image.png" alt="QR Code ${esc(q.name)}" loading="lazy"></div><div class="qr-main"><h2>${esc(q.name)}</h2><div class="qr-info">${esc(q.event||'Campagna senza evento')} · <span class="${q.active?'status-active':'status-inactive'}">${q.active?'● Attivo':'○ Disattivato'}</span></div><dl class="qr-meta"><dt>Link tracciato</dt><dd>${esc(q.tracked_url)}</dd><dt>Destinazione</dt><dd>${esc(q.target_url)}</dd></dl><div class="qr-tools"><button class="btn btn-primary" data-act="copy" data-slug="${q.slug}">⧉ Copia link</button><a class="btn btn-outline" href="/api/qrs/${encodeURIComponent(q.slug)}/image.png" download>↓ QR PNG</a><a class="btn btn-outline" href="/api/qrs/${encodeURIComponent(q.slug)}/poster.pdf" download>↓ Locandina PDF</a><button class="btn btn-outline" data-act="edit" data-slug="${q.slug}">✎ Modifica</button><button class="btn btn-outline" data-act="toggle" data-slug="${q.slug}">${q.active?'Pausa':'Riattiva'}</button></div></div></article>`).join(''):'<div class="no-qrs">Non hai ancora creato codici QR.</div>'}
function openModal(slug=null){let q=state.qrs.find(q=>q.slug===slug);state.editSlug=q?.slug||null;$('#modal-title').textContent=q?'Modifica il QR code':'Crea un QR code';$('#modal-label').textContent=q?'MODIFICA CODICE':'NUOVO CODICE';$('#qr-name').value=q?.name||'';$('#qr-event').value=q?.event||'';$('#qr-target').value=q?.target_url||'https://www.minardipiume.it/index-english.html';$('#modal-save').textContent=q?'Salva modifiche':'Crea il QR Code →';$('#modal-error').textContent='';$('#modal').hidden=false;$('#qr-name').focus()}
function closeModal(){$('#modal').hidden=true;state.editSlug=null}
async function saveModal(e){e.preventDefault();const payload={name:$('#qr-name').value,event:$('#qr-event').value,target_url:$('#qr-target').value};let btn=$('#modal-save');btn.disabled=true;try{if(state.editSlug)await api('/api/qrs/'+encodeURIComponent(state.editSlug),'PUT',payload);else await api('/api/qrs','POST',payload);closeModal();await loadQRs();await loadDashboard();view('qrs');toast('Codice QR salvato correttamente')}catch(err){$('#modal-error').textContent=err.message}finally{btn.disabled=false}}
async function handleQRClick(e){const btn=e.target.closest('button[data-act]');if(!btn)return;const {slug,act}=btn.dataset;const q=state.qrs.find(q=>q.slug===slug);if(!q)return;if(act==='copy'){try{await navigator.clipboard.writeText(q.tracked_url);toast('Link tracciato copiato')}catch{window.prompt('Copia il link tracciato:',q.tracked_url)}}else if(act==='edit')openModal(slug);else if(act==='toggle'){if(!confirm(`${q.active?'Disattivare':'Riattivare'} il QR "${q.name}"?`))return;try{await api('/api/qrs/'+encodeURIComponent(slug)+'/status','POST');await loadQRs();toast('Stato del QR aggiornato')}catch(err){toast(err.message)}}}
async function downloadCsv(){const url='/api/export.csv?'+query();location.href=url;toast('Download CSV avviato')}
async function init(){try{let me=await api('/api/me');state.csrf=me.csrf;$('#user-email').textContent=me.email;state.forcePasswordChange=!!me.must_change_password;$('#local-warning').hidden=me.public_ready;$('#qr-local-warning').hidden=me.public_ready;await loadQRs();await loadDashboard();if(state.forcePasswordChange)openPasswordModal()}catch(err){toast(err.message)}}
$$('.nav-item[data-view]').forEach(btn=>btn.addEventListener('click',()=>view(btn.dataset.view)));
$('#nav-export').addEventListener('click',downloadCsv);$('#csv-top').addEventListener('click',downloadCsv);
$('#create-top').addEventListener('click',()=>openModal());$('#create-qr').addEventListener('click',()=>openModal());
$('#see-qrs').addEventListener('click',()=>view('qrs'));$('#close-modal').addEventListener('click',closeModal);$('#cancel-modal').addEventListener('click',closeModal);$('#modal').addEventListener('click',e=>{if(e.target.id==='modal')closeModal()});$('#qrform').addEventListener('submit',saveModal);$('#qr-card-list').addEventListener('click',handleQRClick);
$('#qr-filter').addEventListener('change',loadDashboard);$('#period-filter').addEventListener('change',()=>{$('#custom-dates').hidden=$('#period-filter').value!=='custom';if($('#period-filter').value!=='custom')loadDashboard()});$('#from').addEventListener('change',()=>{if($('#to').value)loadDashboard()});$('#to').addEventListener('change',()=>{if($('#from').value)loadDashboard()});
$('#logout').addEventListener('click',async()=>{try{await api('/api/logout','POST');location.href='/login'}catch(e){toast(e.message)}});$('#seed-demo').addEventListener('click',async()=>{if(!confirm('Inserire dati simulati per vedere come funziona la dashboard? Sono marcati come DEMO.'))return;try{const r=await api('/api/demo/populate','POST');await loadDashboard();toast(`Inserite ${r.inserted} scansioni dimostrative`)}catch(e){toast(e.message)}});
$('#clear-demo').addEventListener('click',async()=>{try{await api('/api/demo/clear','POST');await loadDashboard();toast('Dati dimostrativi rimossi')}catch(e){toast(e.message)}});
function openPasswordModal(){
  $('#password-modal').hidden=false;
  $('#password-error').textContent='';
  $('#first-access-hint').hidden=!state.forcePasswordChange;
  $('#close-password-modal').hidden=state.forcePasswordChange;
  $('#cancel-password-modal').hidden=state.forcePasswordChange;
  $('#password-modal-title').textContent=state.forcePasswordChange?'Primo accesso: cambia password':'Cambia la password';
  $('#current-password').focus();
}
function closePasswordModal(){if(state.forcePasswordChange)return;$('#password-modal').hidden=true;$('#password-form').reset()}
$('#account-settings').addEventListener('click',openPasswordModal);
$('#close-password-modal').addEventListener('click',closePasswordModal);
$('#cancel-password-modal').addEventListener('click',closePasswordModal);
$('#password-modal').addEventListener('click',e=>{if(e.target.id==='password-modal')closePasswordModal()});
$('#password-form').addEventListener('submit',async e=>{
  e.preventDefault();
  const current=$('#current-password').value,next=$('#new-password').value,confirmPw=$('#confirm-password').value;
  if(next!==confirmPw){$('#password-error').textContent='Le nuove password non coincidono.';return}
  const btn=$('#save-password');btn.disabled=true;
  try{
    const r=await api('/api/account/password','POST',{current_password:current,new_password:next});
    state.csrf=r.csrf;
    state.forcePasswordChange=false;
    $('#password-modal').hidden=true;
    $('#password-form').reset();
    toast('Password cambiata. Le altre sessioni sono state disconnesse.');
  }catch(err){$('#password-error').textContent=err.message}
  finally{btn.disabled=false}
});
$('#menu').addEventListener('click',()=>$('#sidebar').classList.toggle('open'));
init();
