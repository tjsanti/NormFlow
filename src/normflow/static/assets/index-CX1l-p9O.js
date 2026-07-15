(function(){const n=document.createElement("link").relList;if(n&&n.supports&&n.supports("modulepreload"))return;for(const t of document.querySelectorAll('link[rel="modulepreload"]'))i(t);new MutationObserver(t=>{for(const a of t)if(a.type==="childList")for(const d of a.addedNodes)d.tagName==="LINK"&&d.rel==="modulepreload"&&i(d)}).observe(document,{childList:!0,subtree:!0});function r(t){const a={};return t.integrity&&(a.integrity=t.integrity),t.referrerPolicy&&(a.referrerPolicy=t.referrerPolicy),t.crossOrigin==="use-credentials"?a.credentials="include":t.crossOrigin==="anonymous"?a.credentials="omit":a.credentials="same-origin",a}function i(t){if(t.ep)return;t.ep=!0;const a=r(t);fetch(t.href,a)}})();function L(e){return new Promise((n,r)=>{const i=new FileReader;i.addEventListener("load",()=>n(String(i.result??""))),i.addEventListener("error",()=>r(new Error("Could not read the selected CSV."))),i.readAsText(e)})}function j(e){const n=[];let r="",i=!1;for(let t=0;t<e.length;t+=1){const a=e[t];if(i)a==='"'&&e[t+1]==='"'?(r+='"',t+=1):a==='"'?i=!1:r+=a;else if(a==='"'&&r==="")i=!0;else if(a===",")n.push(r),r="";else{if(a===`
`||a==="\r")return n.push(r),n;r+=a}}if(i)throw new Error("The CSV header row has an unterminated quoted field.");return n.push(r),n}async function P(e){const n=await L(e);if(!n)throw new Error("The CSV is empty and has no header row.");const r=j(n);if(r[0]=r[0].replace(/^\uFEFF/,""),!r.some(i=>i!==""))throw new Error("The CSV is empty and has no header row.");return r}let E;function x(e){E&&window.removeEventListener("focus",E),E=e,E&&window.addEventListener("focus",E)}function T(e){return e.split(/[\\/]/).filter(Boolean).at(-1)??e}async function I(){const e=await fetch("/project/info");if(!e.ok){const n=await e.json();throw new Error(n.detail??`Could not open Project (${e.status}).`)}return e.json()}async function A(){const e=await fetch("/review-items");if(!e.ok){const n=await e.json();throw new Error(n.detail??`Could not load Review Items (${e.status}).`)}return e.json()}function f(e,n,r=!1){const i=e.querySelector("#notices");i.innerHTML="";const t=document.createElement("p");t.role=r?"alert":"status",t.textContent=n,i.append(t)}function k(e,n){e.querySelector("#mapping-count").textContent=String(n.mappings),e.querySelector("#review-item-count").textContent=String(n.review_items);const r=e.querySelector("#semantic-index-status");r.textContent=n.semantic_index_warning??"",n.semantic_index_warning?r.role="status":r.removeAttribute("role")}async function $(e,n,r){const i=r.querySelector("button");i.disabled=!0;try{const t=await fetch(`/review-items/${n.id}/accept`,{method:"POST"});if(!t.ok){const a=await t.json();throw Object.assign(new Error(a.detail??`Could not accept Review Item (${t.status}).`),{stale:t.status===409})}r.remove(),f(e,`Review Item ${n.id} accepted.`),await g(e)}catch(t){const a=t instanceof Error&&"stale"in t&&t.stale===!0;f(e,t instanceof Error?t.message:"Could not accept Review Item.",!0),a?await g(e):i.disabled=!1}}function R(e,n){const r=e.querySelector("#review-queue");if(!n.length){r.innerHTML='<p class="empty-state" role="status">No pending Review Items.</p>';return}r.innerHTML=`
    <div class="bulk-actions">
      <button type="button" id="accept-selected" disabled>Accept selected (0)</button>
    </div>
    <table class="review-table">
      <thead><tr>
        <th scope="col">
          <input type="checkbox" aria-label="Select all eligible Review Items">
          <span>Checkbox</span>
        </th>
        <th scope="col">ID</th>
        <th scope="col">Raw Text</th>
        <th scope="col">Suggestion</th>
        <th scope="col">Actions</th>
      </tr></thead>
      <tbody></tbody>
    </table>
  `;const i=r.querySelector("tbody"),t=new Set,a=n.filter(o=>!!o.suggested_text.trim()),d=r.querySelector("#accept-selected"),u=r.querySelector('input[aria-label="Select all eligible Review Items"]');function w(){d.textContent=`Accept selected (${t.size})`,d.disabled=t.size===0,u.checked=a.length>0&&t.size===a.length,u.indeterminate=t.size>0&&t.size<a.length,u.disabled=a.length===0}u.addEventListener("change",()=>{t.clear(),u.checked&&a.forEach(o=>t.add(o.id)),r.querySelectorAll('tbody input[type="checkbox"]').forEach(o=>{o.checked=t.has(Number(o.dataset.reviewItemId))}),w()}),d.addEventListener("click",async()=>{const o=n.map(s=>s.id).filter(s=>t.has(s));if(window.confirm(`Accept ${o.length} selected Review Items?`)){d.disabled=!0;try{const s=await fetch("/review-items/bulk-accept",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({review_item_ids:o})});if(!s.ok){const l=await s.json();throw new Error(l.detail??`Could not accept selected Review Items (${s.status}).`)}const p=await s.json();f(e,`Accepted ${p.accepted} Review Items.`),await g(e)}catch(s){f(e,s instanceof Error?s.message:"Could not accept selected Review Items.",!0),w()}}});function m(o,s,p){r.querySelectorAll('button[data-action="edit"]').forEach(c=>{c.disabled=!0});const l=document.createElement("input");l.type="text",l.value=o.suggested_text,l.setAttribute("aria-label",`Normalized text for Review Item ${o.id}`),s.replaceChildren(l);const h=document.createElement("button");h.type="button",h.textContent="Save and Accept";const v=document.createElement("button");v.type="button",v.textContent="Cancel";const y=()=>R(e,n),C=async()=>{if(!l.value.trim()){f(e,"Normalized text must not be blank.",!0);return}h.disabled=!0,v.disabled=!0;try{const c=await fetch(`/review-items/${o.id}/accept`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({normalized_text:l.value})});if(!c.ok){const b=await c.json();throw Object.assign(new Error(b.detail??`Could not accept Review Item (${c.status}).`),{stale:c.status===409})}f(e,`Review Item ${o.id} accepted.`),await g(e)}catch(c){const b=c instanceof Error&&"stale"in c&&c.stale===!0;f(e,c instanceof Error?c.message:"Could not accept Review Item.",!0),b?await g(e):(h.disabled=!1,v.disabled=!1)}};h.addEventListener("click",()=>void C()),v.addEventListener("click",y),l.addEventListener("keydown",c=>{c.key==="Escape"&&y(),c.key==="Enter"&&(c.preventDefault(),C())}),p.replaceChildren(h,v),l.focus()}for(const o of n){const s=document.createElement("tr");s.className="review-card";const p=document.createElement("td"),l=document.createElement("input");l.type="checkbox",l.setAttribute("aria-label",`Select Review Item ${o.id}`),l.dataset.reviewItemId=String(o.id),l.disabled=!o.suggested_text.trim(),l.addEventListener("change",()=>{l.checked?t.add(o.id):t.delete(o.id),w()}),p.append(l);const h=document.createElement("td");h.textContent=String(o.id);const v=document.createElement("td");v.textContent=o.raw_text;const y=document.createElement("td");y.textContent=o.suggested_text;const C=document.createElement("td"),c=document.createElement("button");c.type="button",c.textContent="Accept",c.disabled=!o.suggested_text.trim(),c.addEventListener("click",()=>void $(e,o,s));const b=document.createElement("button");b.type="button",b.dataset.action="edit",b.textContent="Edit",b.addEventListener("click",()=>m(o,y,C)),C.append(c,b),s.append(p,h,v,y,C),i.append(s)}w()}async function q(e){const n=e.querySelector("#review-queue");n.innerHTML='<p role="status">Loading Review Items…</p>';try{R(e,await A())}catch(r){n.innerHTML="";const i=document.createElement("p");i.role="alert",i.textContent=r instanceof Error?r.message:"Could not load Review Items.",n.append(i)}}async function g(e){try{const n=await I();k(e,n)}catch(n){f(e,n instanceof Error?n.message:"Could not refresh Project.",!0)}await q(e)}function S(e,n){e.querySelectorAll('[role="tab"]').forEach(r=>{const i=r.dataset.tab===n;r.setAttribute("aria-selected",String(i)),r.tabIndex=i?0:-1}),e.querySelector("#import-panel").hidden=n!=="import",e.querySelector("#review-panel").hidden=n!=="review"}function _(e){const n=e.querySelector("#mapping-import-form"),r=e.querySelector("#mapping-file"),i=e.querySelector("#mapping-source-column"),t=e.querySelector("#mapping-target-column"),a=n.querySelector('button[type="submit"]');let d=!1;function u(m,o,s){m.replaceChildren(new Option("Choose a header","")),o.forEach(p=>m.add(new Option(p,p))),m.value=o.includes(s)?s:"",m.disabled=!1}function w(){i.replaceChildren(new Option("Choose a header","")),t.replaceChildren(new Option("Choose a header","")),i.disabled=!0,t.disabled=!0,t.setCustomValidity("")}r.addEventListener("change",async()=>{var o;w();const m=(o=r.files)==null?void 0:o[0];if(m)try{const s=await P(m);u(i,s,"raw_text"),u(t,s,"normalized_text")}catch(s){f(e,s instanceof Error?s.message:"Could not read the selected CSV.",!0)}}),n.addEventListener("submit",async m=>{var o;if(m.preventDefault(),!d){if(!((o=r.files)!=null&&o[0])||!i.value||!t.value){f(e,"Choose a CSV file and both source and target headers.",!0);return}if(i.value===t.value){t.setCustomValidity("Source and target headers must differ."),f(e,"Source and target headers must differ.",!0),t.focus();return}t.setCustomValidity(""),d=!0,a.disabled=!0,a.textContent="Importing…";try{const s=new FormData;s.append("file",r.files[0]);const p=await fetch(`/import/mappings?source_column=${encodeURIComponent(i.value)}&target_column=${encodeURIComponent(t.value)}`,{method:"POST",body:s});if(!p.ok){const h=await p.json();throw new Error(h.detail??`Could not import Mappings (${p.status}).`)}const l=await p.json();n.reset(),w(),f(e,`Imported ${l.imported} Mappings; skipped ${l.skipped}.`),await g(e)}catch(s){f(e,s instanceof Error?s.message:"Could not import Mappings.",!0)}finally{d=!1,a.disabled=!1,a.textContent="Import Mappings"}}}),[i,t].forEach(m=>m.addEventListener("change",()=>{i.value!==t.value&&t.setCustomValidity("")}))}function O(e,n){const r=n.review_items>0?"review":"import";e.innerHTML=`
    <header>
      <div>
        <span class="eyebrow">Project</span>
        <h1></h1>
        <p class="project-path"></p>
      </div>
      <div class="counts">
        <div><strong id="mapping-count">${n.mappings}</strong> Mappings</div>
        <div><strong id="review-item-count">${n.review_items}</strong> pending Review Items</div>
      </div>
    </header>
    <div id="semantic-index-status" aria-live="polite"></div>
    <main class="review-project">
      <nav class="project-tabs" role="tablist" aria-label="Project workflows">
        <button type="button" role="tab" id="import-tab" data-tab="import"
          aria-controls="import-panel">Import</button>
        <button type="button" role="tab" id="review-tab" data-tab="review"
          aria-controls="review-panel">Review Items</button>
      </nav>
      <div id="notices" aria-live="polite"></div>
      <section id="import-panel" role="tabpanel" aria-labelledby="import-tab">
        <section class="import-workflow" aria-labelledby="mapping-import-heading">
          <div class="review-heading">
            <div>
              <span class="eyebrow">Optional workflow</span>
              <h2 id="mapping-import-heading">Mapping Import</h2>
            </div>
          </div>
          <p>Import already-approved source and target text pairs from a CSV.</p>
          <form id="mapping-import-form">
            <label for="mapping-file">CSV file</label>
            <input id="mapping-file" name="file" type="file" accept=".csv,text/csv" required>
            <div class="header-selectors">
              <div>
                <label for="mapping-source-column">Source header</label>
                <select id="mapping-source-column" required disabled>
                  <option value="">Choose a header</option>
                </select>
              </div>
              <div>
                <label for="mapping-target-column">Target header</label>
                <select id="mapping-target-column" required disabled>
                  <option value="">Choose a header</option>
                </select>
              </div>
            </div>
            <button type="submit">Import Mappings</button>
          </form>
        </section>
      </section>
      <section id="review-panel" role="tabpanel" aria-labelledby="review-tab">
        <div class="review-heading">
          <div><span class="eyebrow">Pending work</span><h2>Review Items</h2></div>
          <button type="button" id="refresh-review-items">Refresh</button>
        </div>
        <section id="review-queue" aria-label="Review Items"></section>
      </section>
    </main>
  `,e.querySelector("h1").textContent=T(n.project),e.querySelector(".project-path").textContent=n.project,k(e,n),S(e,r);const i=[...e.querySelectorAll('[role="tab"]')];i.forEach((t,a)=>{t.addEventListener("click",()=>{S(e,t.dataset.tab)}),t.addEventListener("keydown",d=>{let u;if(d.key==="ArrowRight"&&(u=(a+1)%i.length),d.key==="ArrowLeft"&&(u=(a-1+i.length)%i.length),d.key==="Home"&&(u=0),d.key==="End"&&(u=i.length-1),u===void 0)return;d.preventDefault();const w=i[u];S(e,w.dataset.tab),w.focus()})}),_(e),e.querySelector("#refresh-review-items").addEventListener("click",()=>void g(e)),x(()=>void g(e)),q(e)}async function M(e){x(),e.innerHTML='<main class="review-project"><p role="status">Loading Project…</p></main>';try{O(e,await I())}catch(n){const r=document.createElement("p");r.role="alert",r.textContent=n instanceof Error?n.message:"Could not load Project.",e.replaceChildren(r)}}function N(){const e=document.querySelector("#app");e&&M(e)}N();
