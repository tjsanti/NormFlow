(function(){const t=document.createElement("link").relList;if(t&&t.supports&&t.supports("modulepreload"))return;for(const n of document.querySelectorAll('link[rel="modulepreload"]'))a(n);new MutationObserver(n=>{for(const i of n)if(i.type==="childList")for(const m of i.addedNodes)m.tagName==="LINK"&&m.rel==="modulepreload"&&a(m)}).observe(document,{childList:!0,subtree:!0});function r(n){const i={};return n.integrity&&(i.integrity=n.integrity),n.referrerPolicy&&(i.referrerPolicy=n.referrerPolicy),n.crossOrigin==="use-credentials"?i.credentials="include":n.crossOrigin==="anonymous"?i.credentials="omit":i.credentials="same-origin",i}function a(n){if(n.ep)return;n.ep=!0;const i=r(n);fetch(n.href,i)}})();function P(e){return new Promise((t,r)=>{const a=new FileReader;a.addEventListener("load",()=>t(String(a.result??""))),a.addEventListener("error",()=>r(new Error("Could not read the selected CSV."))),a.readAsText(e)})}function T(e){const t=[];let r="",a=!1;for(let n=0;n<e.length;n+=1){const i=e[n];if(a)i==='"'&&e[n+1]==='"'?(r+='"',n+=1):i==='"'?a=!1:r+=i;else if(i==='"'&&r==="")a=!0;else if(i===",")t.push(r),r="";else{if(i===`
`||i==="\r")return t.push(r),t;r+=i}}if(a)throw new Error("The CSV header row has an unterminated quoted field.");return t.push(r),t}async function x(e){const t=await P(e);if(!t)throw new Error("The CSV is empty and has no header row.");const r=T(t);if(r[0]=r[0].replace(/^\uFEFF/,""),!r.some(a=>a!==""))throw new Error("The CSV is empty and has no header row.");return r}function k(e,t){if(typeof e=="string")return e;if(e&&typeof e=="object"&&"message"in e){const r=e.message;if(typeof r=="string")return r}return t}async function _(e){let t=await e.json();const r=e.headers.get("Location")??`/batch-import-runs/${t.id}`;let a=100;for(;t.status==="active";){await new Promise(i=>window.setTimeout(i,a)),a=Math.min(a*2,2e3);const n=await fetch(r);if(!n.ok)throw new Error(`Could not observe Batch Import (${n.status}).`);t=await n.json()}if(t.status!=="succeeded"||!t.result)throw new Error(t.error??`Batch Import ended ${t.status}.`);return t.result}let E;function q(e){E&&window.removeEventListener("focus",E),E=e,E&&window.addEventListener("focus",E)}function A(e){return e.split(/[\\/]/).filter(Boolean).at(-1)??e}async function L(){const e=await fetch("/project/info");if(!e.ok){const t=await e.json();throw new Error(t.detail??`Could not open Project (${e.status}).`)}return e.json()}async function O(){const e=await fetch("/review-items");if(!e.ok){const t=await e.json();throw new Error(t.detail??`Could not load Review Items (${e.status}).`)}return e.json()}function f(e,t,r=!1){const a=e.querySelector("#notices");a.innerHTML="";const n=document.createElement("p");n.role=r?"alert":"status",n.textContent=t,a.append(n)}function R(e,t){e.querySelector("#mapping-count").textContent=String(t.mappings),e.querySelector("#review-item-count").textContent=String(t.review_items);const r=e.querySelector("#semantic-index-status");r.textContent=t.semantic_index_warning??"",t.semantic_index_warning?r.role="status":r.removeAttribute("role")}async function M(e,t,r){const a=r.querySelector("button");a.disabled=!0;try{const n=await fetch(`/review-items/${t.id}/accept`,{method:"POST"});if(!n.ok){const i=await n.json();throw Object.assign(new Error(i.detail??`Could not accept Review Item (${n.status}).`),{stale:n.status===409})}r.remove(),f(e,`Review Item ${t.id} accepted.`),await b(e)}catch(n){const i=n instanceof Error&&"stale"in n&&n.stale===!0;f(e,n instanceof Error?n.message:"Could not accept Review Item.",!0),i?await b(e):a.disabled=!1}}function j(e,t){const r=e.querySelector("#review-queue");if(!t.length){r.innerHTML='<p class="empty-state" role="status">No pending Review Items.</p>';return}r.innerHTML=`
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
  `;const a=r.querySelector("tbody"),n=new Set,i=t.filter(s=>!!s.suggested_text.trim()),m=r.querySelector("#accept-selected"),p=r.querySelector('input[aria-label="Select all eligible Review Items"]');function u(){m.textContent=`Accept selected (${n.size})`,m.disabled=n.size===0,p.checked=i.length>0&&n.size===i.length,p.indeterminate=n.size>0&&n.size<i.length,p.disabled=i.length===0}p.addEventListener("change",()=>{n.clear(),p.checked&&i.forEach(s=>n.add(s.id)),r.querySelectorAll('tbody input[type="checkbox"]').forEach(s=>{s.checked=n.has(Number(s.dataset.reviewItemId))}),u()}),m.addEventListener("click",async()=>{const s=t.map(o=>o.id).filter(o=>n.has(o));if(window.confirm(`Accept ${s.length} selected Review Items?`)){m.disabled=!0;try{const o=await fetch("/review-items/bulk-accept",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({review_item_ids:s})});if(!o.ok){const c=await o.json();throw Object.assign(new Error(k(c.detail,`Could not accept selected Review Items (${o.status}).`)),{stale:o.status===409})}const l=await o.json();f(e,`Accepted ${l.accepted} Review Items.`),await b(e)}catch(o){const l=o instanceof Error&&"stale"in o&&o.stale===!0;f(e,o instanceof Error?o.message:"Could not accept selected Review Items.",!0),l?await b(e):u()}}});function h(s,o,l){r.querySelectorAll('button[data-action="edit"]').forEach(d=>{d.disabled=!0});const c=document.createElement("input");c.type="text",c.value=s.suggested_text,c.setAttribute("aria-label",`Normalized text for Review Item ${s.id}`),o.replaceChildren(c);const w=document.createElement("button");w.type="button",w.textContent="Save and Accept";const v=document.createElement("button");v.type="button",v.textContent="Cancel";const y=()=>j(e,t),C=async()=>{if(!c.value.trim()){f(e,"Normalized text must not be blank.",!0);return}w.disabled=!0,v.disabled=!0;try{const d=await fetch(`/review-items/${s.id}/accept`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({normalized_text:c.value})});if(!d.ok){const g=await d.json();throw Object.assign(new Error(g.detail??`Could not accept Review Item (${d.status}).`),{stale:d.status===409})}f(e,`Review Item ${s.id} accepted.`),await b(e)}catch(d){const g=d instanceof Error&&"stale"in d&&d.stale===!0;f(e,d instanceof Error?d.message:"Could not accept Review Item.",!0),g?await b(e):(w.disabled=!1,v.disabled=!1)}};w.addEventListener("click",()=>void C()),v.addEventListener("click",y),c.addEventListener("keydown",d=>{d.key==="Escape"&&y(),d.key==="Enter"&&(d.preventDefault(),C())}),l.replaceChildren(w,v),c.focus()}for(const s of t){const o=document.createElement("tr");o.className="review-card";const l=document.createElement("td"),c=document.createElement("input");c.type="checkbox",c.setAttribute("aria-label",`Select Review Item ${s.id}`),c.dataset.reviewItemId=String(s.id),c.disabled=!s.suggested_text.trim(),c.addEventListener("change",()=>{c.checked?n.add(s.id):n.delete(s.id),u()}),l.append(c);const w=document.createElement("td");w.textContent=String(s.id);const v=document.createElement("td");v.textContent=s.raw_text;const y=document.createElement("td");y.textContent=s.suggested_text;const C=document.createElement("td"),d=document.createElement("button");d.type="button",d.textContent="Accept",d.disabled=!s.suggested_text.trim(),d.addEventListener("click",()=>void M(e,s,o));const g=document.createElement("button");g.type="button",g.dataset.action="edit",g.textContent="Edit",g.addEventListener("click",()=>h(s,y,C)),C.append(d,g),o.append(l,w,v,y,C),a.append(o)}u()}async function $(e){const t=e.querySelector("#review-queue");t.innerHTML='<p role="status">Loading Review Items…</p>';try{j(e,await O())}catch(r){t.innerHTML="";const a=document.createElement("p");a.role="alert",a.textContent=r instanceof Error?r.message:"Could not load Review Items.",t.append(a)}}async function b(e){try{const t=await L();R(e,t)}catch(t){f(e,t instanceof Error?t.message:"Could not refresh Project.",!0)}await $(e)}function S(e,t){e.querySelectorAll('[role="tab"]').forEach(r=>{const a=r.dataset.tab===t;r.setAttribute("aria-selected",String(a)),r.tabIndex=a?0:-1}),e.querySelector("#import-panel").hidden=t!=="import",e.querySelector("#review-panel").hidden=t!=="review"}function I(e,t){e.querySelectorAll("#mapping-import-form input, #mapping-import-form select, #mapping-import-form button, #batch-import-form input, #batch-import-form select, #batch-import-form button").forEach(r=>{r.disabled=t||r instanceof HTMLSelectElement&&r.options.length===1})}function V(e,t){const r=e.querySelector("#mapping-import-form"),a=e.querySelector("#mapping-file"),n=e.querySelector("#mapping-source-column"),i=e.querySelector("#mapping-target-column"),m=r.querySelector('button[type="submit"]');let p=0;function u(s,o,l){s.replaceChildren(new Option("Choose a header","")),o.forEach(c=>s.add(new Option(c,c))),s.value=o.includes(l)?l:"",s.disabled=!1}function h(){n.replaceChildren(new Option("Choose a header","")),i.replaceChildren(new Option("Choose a header","")),n.disabled=!0,i.disabled=!0,i.setCustomValidity("")}a.addEventListener("change",async()=>{var l;const s=++p;h();const o=(l=a.files)==null?void 0:l[0];if(o)try{const c=await x(o);if(s!==p)return;u(n,c,"raw_text"),u(i,c,"normalized_text")}catch(c){if(s!==p)return;f(e,c instanceof Error?c.message:"Could not read the selected CSV.",!0)}}),r.addEventListener("submit",async s=>{var o;if(s.preventDefault(),!t.active){if(!((o=a.files)!=null&&o[0])||!n.value||!i.value){f(e,"Choose a CSV file and both source and target headers.",!0);return}if(n.value===i.value){i.setCustomValidity("Source and target headers must differ."),f(e,"Source and target headers must differ.",!0),i.focus();return}i.setCustomValidity(""),t.active=!0,I(e,!0),m.textContent=`Processing ${a.files[0].name}…`;try{const l=new FormData;l.append("file",a.files[0]);const c=await fetch(`/import/mappings?source_column=${encodeURIComponent(n.value)}&target_column=${encodeURIComponent(i.value)}`,{method:"POST",body:l});if(!c.ok){const v=await c.json();throw new Error(v.detail??`Could not import Mappings (${c.status}).`)}const w=await c.json();r.reset(),h(),f(e,`Imported ${w.imported} Mappings; skipped ${w.skipped}.`),await b(e)}catch(l){f(e,l instanceof Error?l.message:"Could not import Mappings.",!0)}finally{t.active=!1,I(e,!1),m.textContent="Import Mappings"}}}),[n,i].forEach(s=>s.addEventListener("change",()=>{n.value!==i.value&&i.setCustomValidity("")}))}function B(e,t){const r=e.querySelector("#batch-import-form"),a=e.querySelector("#batch-file"),n=e.querySelector("#batch-source-column"),i=r.querySelector('button[type="submit"]');function m(){n.replaceChildren(new Option("Choose a header","")),n.disabled=!0}a.addEventListener("change",async()=>{var u;m();const p=(u=a.files)==null?void 0:u[0];if(p)try{const h=await x(p);h.forEach(s=>n.add(new Option(s,s))),n.value=h.includes("raw_text")?"raw_text":"",n.disabled=!1}catch(h){f(e,h instanceof Error?h.message:"Could not read the selected CSV.",!0)}}),r.addEventListener("submit",async p=>{var h;if(p.preventDefault(),t.active)return;const u=(h=a.files)==null?void 0:h[0];if(!u||!n.value){f(e,"Choose a CSV file and raw text header.",!0);return}t.active=!0,I(e,!0),i.textContent=`Processing ${u.name}…`;try{const s=new FormData;s.append("file",u);const o=await fetch(`/batch-import-runs?column=${encodeURIComponent(n.value)}`,{method:"POST",body:s});if(!o.ok){const w=await o.json();throw new Error(k(w.detail,`Could not import Batch (${o.status}).`))}const l=await _(o);r.reset(),m();const c=l.review_items===1?"Review Item":"Review Items";f(e,`Batch Import complete: ${l.auto_committed} auto-committed, ${l.review_items} ${c}, ${l.skipped} skipped.`),await b(e),l.review_items>0&&S(e,"review")}catch(s){S(e,"import"),f(e,s instanceof Error?s.message:"Could not import Batch.",!0)}finally{t.active=!1,I(e,!1),i.textContent="Import Batch"}})}function H(e,t){const r=t.review_items>0?"review":"import";e.innerHTML=`
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
  `,e.querySelector("h1").textContent=A(t.project),e.querySelector(".project-path").textContent=t.project,R(e,t),S(e,r);const a=[...e.querySelectorAll('[role="tab"]')];a.forEach((i,m)=>{i.addEventListener("click",()=>{S(e,i.dataset.tab)}),i.addEventListener("keydown",p=>{let u;if(p.key==="ArrowRight"&&(u=(m+1)%a.length),p.key==="ArrowLeft"&&(u=(m-1+a.length)%a.length),p.key==="Home"&&(u=0),p.key==="End"&&(u=a.length-1),u===void 0)return;p.preventDefault();const h=a[u];S(e,h.dataset.tab),h.focus()})});const n={active:!1};V(e,n),B(e,n),e.querySelector("#refresh-review-items").addEventListener("click",()=>void b(e)),q(()=>void b(e)),$(e)}async function N(e){q(),e.innerHTML='<main class="review-project"><p role="status">Loading Project…</p></main>';try{H(e,await L())}catch(t){const r=document.createElement("p");r.role="alert",r.textContent=t instanceof Error?t.message:"Could not load Project.",e.replaceChildren(r)}}function F(){const e=document.querySelector("#app");e&&N(e)}F();
