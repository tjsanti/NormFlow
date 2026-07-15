(function(){const t=document.createElement("link").relList;if(t&&t.supports&&t.supports("modulepreload"))return;for(const n of document.querySelectorAll('link[rel="modulepreload"]'))a(n);new MutationObserver(n=>{for(const i of n)if(i.type==="childList")for(const f of i.addedNodes)f.tagName==="LINK"&&f.rel==="modulepreload"&&a(f)}).observe(document,{childList:!0,subtree:!0});function r(n){const i={};return n.integrity&&(i.integrity=n.integrity),n.referrerPolicy&&(i.referrerPolicy=n.referrerPolicy),n.crossOrigin==="use-credentials"?i.credentials="include":n.crossOrigin==="anonymous"?i.credentials="omit":i.credentials="same-origin",i}function a(n){if(n.ep)return;n.ep=!0;const i=r(n);fetch(n.href,i)}})();function $(e){return new Promise((t,r)=>{const a=new FileReader;a.addEventListener("load",()=>t(String(a.result??""))),a.addEventListener("error",()=>r(new Error("Could not read the selected CSV."))),a.readAsText(e)})}function P(e){const t=[];let r="",a=!1;for(let n=0;n<e.length;n+=1){const i=e[n];if(a)i==='"'&&e[n+1]==='"'?(r+='"',n+=1):i==='"'?a=!1:r+=i;else if(i==='"'&&r==="")a=!0;else if(i===",")t.push(r),r="";else{if(i===`
`||i==="\r")return t.push(r),t;r+=i}}if(a)throw new Error("The CSV header row has an unterminated quoted field.");return t.push(r),t}async function I(e){const t=await $(e);if(!t)throw new Error("The CSV is empty and has no header row.");const r=P(t);if(r[0]=r[0].replace(/^\uFEFF/,""),!r.some(a=>a!==""))throw new Error("The CSV is empty and has no header row.");return r}let E;function q(e){E&&window.removeEventListener("focus",E),E=e,E&&window.addEventListener("focus",E)}function _(e){return e.split(/[\\/]/).filter(Boolean).at(-1)??e}async function k(){const e=await fetch("/project/info");if(!e.ok){const t=await e.json();throw new Error(t.detail??`Could not open Project (${e.status}).`)}return e.json()}async function T(){const e=await fetch("/review-items");if(!e.ok){const t=await e.json();throw new Error(t.detail??`Could not load Review Items (${e.status}).`)}return e.json()}function h(e,t,r=!1){const a=e.querySelector("#notices");a.innerHTML="";const n=document.createElement("p");n.role=r?"alert":"status",n.textContent=t,a.append(n)}function L(e,t){e.querySelector("#mapping-count").textContent=String(t.mappings),e.querySelector("#review-item-count").textContent=String(t.review_items);const r=e.querySelector("#semantic-index-status");r.textContent=t.semantic_index_warning??"",t.semantic_index_warning?r.role="status":r.removeAttribute("role")}async function A(e,t,r){const a=r.querySelector("button");a.disabled=!0;try{const n=await fetch(`/review-items/${t.id}/accept`,{method:"POST"});if(!n.ok){const i=await n.json();throw Object.assign(new Error(i.detail??`Could not accept Review Item (${n.status}).`),{stale:n.status===409})}r.remove(),h(e,`Review Item ${t.id} accepted.`),await g(e)}catch(n){const i=n instanceof Error&&"stale"in n&&n.stale===!0;h(e,n instanceof Error?n.message:"Could not accept Review Item.",!0),i?await g(e):a.disabled=!1}}function R(e,t){const r=e.querySelector("#review-queue");if(!t.length){r.innerHTML='<p class="empty-state" role="status">No pending Review Items.</p>';return}r.innerHTML=`
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
  `;const a=r.querySelector("tbody"),n=new Set,i=t.filter(o=>!!o.suggested_text.trim()),f=r.querySelector("#accept-selected"),m=r.querySelector('input[aria-label="Select all eligible Review Items"]');function u(){f.textContent=`Accept selected (${n.size})`,f.disabled=n.size===0,m.checked=i.length>0&&n.size===i.length,m.indeterminate=n.size>0&&n.size<i.length,m.disabled=i.length===0}m.addEventListener("change",()=>{n.clear(),m.checked&&i.forEach(o=>n.add(o.id)),r.querySelectorAll('tbody input[type="checkbox"]').forEach(o=>{o.checked=n.has(Number(o.dataset.reviewItemId))}),u()}),f.addEventListener("click",async()=>{const o=t.map(s=>s.id).filter(s=>n.has(s));if(window.confirm(`Accept ${o.length} selected Review Items?`)){f.disabled=!0;try{const s=await fetch("/review-items/bulk-accept",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({review_item_ids:o})});if(!s.ok){const l=await s.json();throw new Error(l.detail??`Could not accept selected Review Items (${s.status}).`)}const p=await s.json();h(e,`Accepted ${p.accepted} Review Items.`),await g(e)}catch(s){h(e,s instanceof Error?s.message:"Could not accept selected Review Items.",!0),u()}}});function c(o,s,p){r.querySelectorAll('button[data-action="edit"]').forEach(d=>{d.disabled=!0});const l=document.createElement("input");l.type="text",l.value=o.suggested_text,l.setAttribute("aria-label",`Normalized text for Review Item ${o.id}`),s.replaceChildren(l);const w=document.createElement("button");w.type="button",w.textContent="Save and Accept";const v=document.createElement("button");v.type="button",v.textContent="Cancel";const y=()=>R(e,t),C=async()=>{if(!l.value.trim()){h(e,"Normalized text must not be blank.",!0);return}w.disabled=!0,v.disabled=!0;try{const d=await fetch(`/review-items/${o.id}/accept`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({normalized_text:l.value})});if(!d.ok){const b=await d.json();throw Object.assign(new Error(b.detail??`Could not accept Review Item (${d.status}).`),{stale:d.status===409})}h(e,`Review Item ${o.id} accepted.`),await g(e)}catch(d){const b=d instanceof Error&&"stale"in d&&d.stale===!0;h(e,d instanceof Error?d.message:"Could not accept Review Item.",!0),b?await g(e):(w.disabled=!1,v.disabled=!1)}};w.addEventListener("click",()=>void C()),v.addEventListener("click",y),l.addEventListener("keydown",d=>{d.key==="Escape"&&y(),d.key==="Enter"&&(d.preventDefault(),C())}),p.replaceChildren(w,v),l.focus()}for(const o of t){const s=document.createElement("tr");s.className="review-card";const p=document.createElement("td"),l=document.createElement("input");l.type="checkbox",l.setAttribute("aria-label",`Select Review Item ${o.id}`),l.dataset.reviewItemId=String(o.id),l.disabled=!o.suggested_text.trim(),l.addEventListener("change",()=>{l.checked?n.add(o.id):n.delete(o.id),u()}),p.append(l);const w=document.createElement("td");w.textContent=String(o.id);const v=document.createElement("td");v.textContent=o.raw_text;const y=document.createElement("td");y.textContent=o.suggested_text;const C=document.createElement("td"),d=document.createElement("button");d.type="button",d.textContent="Accept",d.disabled=!o.suggested_text.trim(),d.addEventListener("click",()=>void A(e,o,s));const b=document.createElement("button");b.type="button",b.dataset.action="edit",b.textContent="Edit",b.addEventListener("click",()=>c(o,y,C)),C.append(d,b),s.append(p,w,v,y,C),a.append(s)}u()}async function j(e){const t=e.querySelector("#review-queue");t.innerHTML='<p role="status">Loading Review Items…</p>';try{R(e,await T())}catch(r){t.innerHTML="";const a=document.createElement("p");a.role="alert",a.textContent=r instanceof Error?r.message:"Could not load Review Items.",t.append(a)}}async function g(e){try{const t=await k();L(e,t)}catch(t){h(e,t instanceof Error?t.message:"Could not refresh Project.",!0)}await j(e)}function S(e,t){e.querySelectorAll('[role="tab"]').forEach(r=>{const a=r.dataset.tab===t;r.setAttribute("aria-selected",String(a)),r.tabIndex=a?0:-1}),e.querySelector("#import-panel").hidden=t!=="import",e.querySelector("#review-panel").hidden=t!=="review"}function x(e,t){e.querySelectorAll("#mapping-import-form input, #mapping-import-form select, #mapping-import-form button, #batch-import-form input, #batch-import-form select, #batch-import-form button").forEach(r=>{r.disabled=t||r instanceof HTMLSelectElement&&r.options.length===1})}function O(e,t){const r=e.querySelector("#mapping-import-form"),a=e.querySelector("#mapping-file"),n=e.querySelector("#mapping-source-column"),i=e.querySelector("#mapping-target-column"),f=r.querySelector('button[type="submit"]');function m(c,o,s){c.replaceChildren(new Option("Choose a header","")),o.forEach(p=>c.add(new Option(p,p))),c.value=o.includes(s)?s:"",c.disabled=!1}function u(){n.replaceChildren(new Option("Choose a header","")),i.replaceChildren(new Option("Choose a header","")),n.disabled=!0,i.disabled=!0,i.setCustomValidity("")}a.addEventListener("change",async()=>{var o;u();const c=(o=a.files)==null?void 0:o[0];if(c)try{const s=await I(c);m(n,s,"raw_text"),m(i,s,"normalized_text")}catch(s){h(e,s instanceof Error?s.message:"Could not read the selected CSV.",!0)}}),r.addEventListener("submit",async c=>{var o;if(c.preventDefault(),!t.active){if(!((o=a.files)!=null&&o[0])||!n.value||!i.value){h(e,"Choose a CSV file and both source and target headers.",!0);return}if(n.value===i.value){i.setCustomValidity("Source and target headers must differ."),h(e,"Source and target headers must differ.",!0),i.focus();return}i.setCustomValidity(""),t.active=!0,x(e,!0),f.textContent=`Processing ${a.files[0].name}…`;try{const s=new FormData;s.append("file",a.files[0]);const p=await fetch(`/import/mappings?source_column=${encodeURIComponent(n.value)}&target_column=${encodeURIComponent(i.value)}`,{method:"POST",body:s});if(!p.ok){const w=await p.json();throw new Error(w.detail??`Could not import Mappings (${p.status}).`)}const l=await p.json();r.reset(),u(),h(e,`Imported ${l.imported} Mappings; skipped ${l.skipped}.`),await g(e)}catch(s){h(e,s instanceof Error?s.message:"Could not import Mappings.",!0)}finally{t.active=!1,x(e,!1),f.textContent="Import Mappings"}}}),[n,i].forEach(c=>c.addEventListener("change",()=>{n.value!==i.value&&i.setCustomValidity("")}))}function M(e,t){const r=e.querySelector("#batch-import-form"),a=e.querySelector("#batch-file"),n=e.querySelector("#batch-source-column"),i=r.querySelector('button[type="submit"]');function f(){n.replaceChildren(new Option("Choose a header","")),n.disabled=!0}a.addEventListener("change",async()=>{var u;f();const m=(u=a.files)==null?void 0:u[0];if(m)try{const c=await I(m);c.forEach(o=>n.add(new Option(o,o))),n.value=c.includes("raw_text")?"raw_text":"",n.disabled=!1}catch(c){h(e,c instanceof Error?c.message:"Could not read the selected CSV.",!0)}}),r.addEventListener("submit",async m=>{var c;if(m.preventDefault(),t.active)return;const u=(c=a.files)==null?void 0:c[0];if(!u||!n.value){h(e,"Choose a CSV file and raw text header.",!0);return}t.active=!0,x(e,!0),i.textContent=`Processing ${u.name}…`;try{const o=new FormData;o.append("file",u);const s=await fetch(`/import/records?column=${encodeURIComponent(n.value)}`,{method:"POST",body:o});if(!s.ok){const w=await s.json();throw new Error(w.detail??`Could not import Batch (${s.status}).`)}const p=await s.json();r.reset(),f();const l=p.review_items===1?"Review Item":"Review Items";h(e,`Batch Import complete: ${p.auto_committed} auto-committed, ${p.review_items} ${l}, ${p.skipped} skipped.`),await g(e),p.review_items>0&&S(e,"review")}catch(o){h(e,o instanceof Error?o.message:"Could not import Batch.",!0)}finally{t.active=!1,x(e,!1),i.textContent="Import Batch"}})}function V(e,t){const r=t.review_items>0?"review":"import";e.innerHTML=`
    <header>
      <div>
        <span class="eyebrow">Project</span>
        <h1></h1>
        <p class="project-path"></p>
      </div>
      <div class="counts">
        <div><strong id="mapping-count">${t.mappings}</strong> Mappings</div>
        <div><strong id="review-item-count">${t.review_items}</strong> pending Review Items</div>
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
        <section class="import-workflow" aria-labelledby="batch-import-heading">
          <div class="review-heading">
            <div>
              <span class="eyebrow">Primary workflow</span>
              <h2 id="batch-import-heading">Batch Import</h2>
            </div>
          </div>
          <p>Upload raw records for exact, semantic, and LLM matching.</p>
          <form id="batch-import-form">
            ${t.mappings===0?`
              <p class="import-note">This Project has no Mappings, so matching has fewer
                examples, but you can still import records for review.</p>
            `:""}
            <label for="batch-file">CSV file</label>
            <input id="batch-file" name="file" type="file" accept=".csv,text/csv" required>
            <label for="batch-source-column">Raw text header</label>
            <select id="batch-source-column" required disabled>
              <option value="">Choose a header</option>
            </select>
            <p class="import-warning">A successful Batch Import replaces the one Batch CSV
              retained for export.</p>
            <button type="submit">Import Batch</button>
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
  `,e.querySelector("h1").textContent=_(t.project),e.querySelector(".project-path").textContent=t.project,L(e,t),S(e,r);const a=[...e.querySelectorAll('[role="tab"]')];a.forEach((i,f)=>{i.addEventListener("click",()=>{S(e,i.dataset.tab)}),i.addEventListener("keydown",m=>{let u;if(m.key==="ArrowRight"&&(u=(f+1)%a.length),m.key==="ArrowLeft"&&(u=(f-1+a.length)%a.length),m.key==="Home"&&(u=0),m.key==="End"&&(u=a.length-1),u===void 0)return;m.preventDefault();const c=a[u];S(e,c.dataset.tab),c.focus()})});const n={active:!1};O(e,n),M(e,n),e.querySelector("#refresh-review-items").addEventListener("click",()=>void g(e)),q(()=>void g(e)),j(e)}async function B(e){q(),e.innerHTML='<main class="review-project"><p role="status">Loading Project…</p></main>';try{V(e,await k())}catch(t){const r=document.createElement("p");r.role="alert",r.textContent=t instanceof Error?t.message:"Could not load Project.",e.replaceChildren(r)}}function H(){const e=document.querySelector("#app");e&&B(e)}H();
