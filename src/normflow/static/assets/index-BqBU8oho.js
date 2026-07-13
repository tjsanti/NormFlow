(function(){const t=document.createElement("link").relList;if(t&&t.supports&&t.supports("modulepreload"))return;for(const r of document.querySelectorAll('link[rel="modulepreload"]'))s(r);new MutationObserver(r=>{for(const n of r)if(n.type==="childList")for(const i of n.addedNodes)i.tagName==="LINK"&&i.rel==="modulepreload"&&s(i)}).observe(document,{childList:!0,subtree:!0});function o(r){const n={};return r.integrity&&(n.integrity=r.integrity),r.referrerPolicy&&(n.referrerPolicy=r.referrerPolicy),r.crossOrigin==="use-credentials"?n.credentials="include":r.crossOrigin==="anonymous"?n.credentials="omit":n.credentials="same-origin",n}function s(r){if(r.ep)return;r.ep=!0;const n=o(r);fetch(r.href,n)}})();const R="normflow.recentProjects";let E;function x(e){E&&window.removeEventListener("focus",E),E=e,E&&window.addEventListener("focus",E)}function P(e){return e.split("/").filter(Boolean).at(-1)??e}function C(){try{const e=JSON.parse(window.localStorage.getItem(R)??"[]");return Array.isArray(e)?e.filter(t=>typeof t=="string"):[]}catch{return[]}}function I(e){const t=[e,...C().filter(o=>o!==e)];window.localStorage.setItem(R,JSON.stringify(t))}function L(e){window.localStorage.setItem(R,JSON.stringify(C().filter(t=>t!==e)))}async function k(e){const t=await fetch("/workspace/info",{headers:{"X-Normflow-Workspace":e}});if(!t.ok){const o=await t.json();throw new Error(o.detail??`Could not open Project (${t.status}).`)}return t.json()}async function O(e){const t=await fetch("/review-items",{headers:{"X-Normflow-Workspace":e}});if(!t.ok){const o=await t.json();throw new Error(o.detail??`Could not load Review Items (${t.status}).`)}return t.json()}function w(e,t,o=!1){const s=e.querySelector("#notices");s.innerHTML="";const r=document.createElement("p");r.role=o?"alert":"status",r.textContent=t,s.append(r)}function T(e,t){e.querySelector("#mapping-count").textContent=String(t.mappings),e.querySelector("#review-item-count").textContent=String(t.review_items)}async function _(e,t,o,s){const r=s.querySelector("button");r.disabled=!0;try{const n=await fetch(`/review-items/${o.id}/accept`,{method:"POST",headers:{"X-Normflow-Workspace":t}});if(!n.ok){const i=await n.json();throw Object.assign(new Error(i.detail??`Could not accept Review Item (${n.status}).`),{stale:n.status===409})}s.remove(),w(e,`Review Item ${o.id} accepted.`),await y(e,t)}catch(n){const i=n instanceof Error&&"stale"in n&&n.stale===!0;w(e,n instanceof Error?n.message:"Could not accept Review Item.",!0),i?await y(e,t):r.disabled=!1}}function q(e,t,o){const s=e.querySelector("#review-queue");if(!o.length){s.innerHTML='<p class="empty-state" role="status">No pending Review Items.</p>';return}s.innerHTML=`
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
  `;const r=s.querySelector("tbody"),n=new Set,i=o.filter(c=>!!c.suggested_text.trim()),m=s.querySelector("#accept-selected"),g=s.querySelector('input[aria-label="Select all eligible Review Items"]');function S(){m.textContent=`Accept selected (${n.size})`,m.disabled=n.size===0,g.checked=i.length>0&&n.size===i.length,g.indeterminate=n.size>0&&n.size<i.length,g.disabled=i.length===0}g.addEventListener("change",()=>{n.clear(),g.checked&&i.forEach(c=>n.add(c.id)),s.querySelectorAll('tbody input[type="checkbox"]').forEach(c=>{c.checked=n.has(Number(c.dataset.reviewItemId))}),S()}),m.addEventListener("click",async()=>{const c=o.map(a=>a.id).filter(a=>n.has(a));if(window.confirm(`Accept ${c.length} selected Review Items?`)){m.disabled=!0;try{const a=await fetch("/review-items/bulk-accept",{method:"POST",headers:{"Content-Type":"application/json","X-Normflow-Workspace":t},body:JSON.stringify({review_item_ids:c})});if(!a.ok){const d=await a.json();throw new Error(d.detail??`Could not accept selected Review Items (${a.status}).`)}const v=await a.json();w(e,`Accepted ${v.accepted} Review Items.`),await y(e,t)}catch(a){w(e,a instanceof Error?a.message:"Could not accept selected Review Items.",!0),S()}}});function A(c,a,v){s.querySelectorAll('button[data-action="edit"]').forEach(l=>{l.disabled=!0});const d=document.createElement("input");d.type="text",d.value=c.suggested_text,d.setAttribute("aria-label",`Normalized text for Review Item ${c.id}`),a.replaceChildren(d);const u=document.createElement("button");u.type="button",u.textContent="Save and Accept";const p=document.createElement("button");p.type="button",p.textContent="Cancel";const h=()=>q(e,t,o),b=async()=>{if(!d.value.trim()){w(e,"Normalized text must not be blank.",!0);return}u.disabled=!0,p.disabled=!0;try{const l=await fetch(`/review-items/${c.id}/edit-and-accept?normalized_text=${encodeURIComponent(d.value)}`,{method:"POST",headers:{"X-Normflow-Workspace":t}});if(!l.ok){const f=await l.json();throw new Error(f.detail??`Could not edit and accept Review Item (${l.status}).`)}w(e,`Review Item ${c.id} accepted with edit.`),await y(e,t)}catch(l){w(e,l instanceof Error?l.message:"Could not edit and accept Review Item.",!0),u.disabled=!1,p.disabled=!1}};u.addEventListener("click",()=>void b()),p.addEventListener("click",h),d.addEventListener("keydown",l=>{l.key==="Escape"&&h(),l.key==="Enter"&&(l.preventDefault(),b())}),v.replaceChildren(u,p),d.focus()}for(const c of o){const a=document.createElement("tr");a.className="review-card";const v=document.createElement("td"),d=document.createElement("input");d.type="checkbox",d.setAttribute("aria-label",`Select Review Item ${c.id}`),d.dataset.reviewItemId=String(c.id),d.disabled=!c.suggested_text.trim(),d.addEventListener("change",()=>{d.checked?n.add(c.id):n.delete(c.id),S()}),v.append(d);const u=document.createElement("td");u.textContent=String(c.id);const p=document.createElement("td");p.textContent=c.raw_text;const h=document.createElement("td");h.textContent=c.suggested_text;const b=document.createElement("td"),l=document.createElement("button");l.type="button",l.textContent="Accept",l.disabled=!c.suggested_text.trim(),l.addEventListener("click",()=>void _(e,t,c,a));const f=document.createElement("button");f.type="button",f.dataset.action="edit",f.textContent="Edit",f.addEventListener("click",()=>A(c,h,b)),b.append(l,f),a.append(v,u,p,h,b),r.append(a)}S()}async function N(e,t){const o=e.querySelector("#review-queue");o.innerHTML='<p role="status">Loading Review Items…</p>';try{q(e,t,await O(t))}catch(s){o.innerHTML="";const r=document.createElement("p");r.role="alert",r.textContent=s instanceof Error?s.message:"Could not load Review Items.",o.append(r)}}async function y(e,t){const o=await k(t);T(e,o),await N(e,t)}function j(e,t){e.innerHTML=`
    <header>
      <div>
        <span class="eyebrow">Project</span>
        <h1>${P(t.workspace)}</h1>
      </div>
      <div class="counts">
        <div><strong id="mapping-count">${t.mappings}</strong> Mappings</div>
        <div><strong id="review-item-count">${t.review_items}</strong> pending Review Items</div>
      </div>
      <button type="button">Switch Project</button>
    </header>
    <main class="review-workspace">
      <div class="review-heading">
        <div><span class="eyebrow">Pending work</span><h2>Review Items</h2></div>
        <button type="button" id="refresh-review-items">Refresh</button>
      </div>
      <div id="notices" aria-live="polite"></div>
      <section id="review-queue" aria-label="Review Items"></section>
    </main>
  `,e.querySelector("header button").addEventListener("click",()=>$(e)),e.querySelector("#refresh-review-items").addEventListener("click",()=>void y(e,t.workspace)),x(()=>void y(e,t.workspace)),N(e,t.workspace)}function $(e){x(),e.innerHTML=`
    <main class="picker">
      <p class="eyebrow">Local normalization workbench</p>
      <h1>Open a Project</h1>
      <p>Enter the folder containing your NormFlow Project.</p>
      <form>
        <label for="project-path">Project folder path</label>
        <div class="field-row">
          <input id="project-path" name="project-path" autocomplete="off" required />
          <button type="submit">Open Project</button>
        </div>
      </form>
    </main>
  `,e.querySelector("form").addEventListener("submit",async o=>{var r;o.preventDefault();const s=e.querySelector("#project-path").value;(r=e.querySelector("[role=alert]"))==null||r.remove();try{const n=await k(s);I(n.workspace),j(e,n)}catch(n){const i=document.createElement("p");i.role="alert",i.textContent=n instanceof Error?n.message:"Could not open Project.",e.querySelector("form").append(i)}});const t=C();if(t.length){const o=document.createElement("section");o.className="recents";const s=document.createElement("h2");s.textContent="Recent Projects",o.append(s);for(const r of t){const n=document.createElement("button");n.type="button",n.textContent=`${P(r)} — ${r}`,n.addEventListener("click",async()=>{try{const i=await k(r);I(i.workspace),j(e,i)}catch(i){L(r);const m=document.createElement("p");m.role="alert",m.textContent=i instanceof Error?i.message:"Could not open Project.",o.append(m),n.remove()}}),o.append(n)}e.querySelector("main").append(o)}}async function z(e){for(const t of C())try{const o=await k(t);I(o.workspace),j(e,o);return}catch{L(t)}}function M(){const e=document.querySelector("#app");e&&($(e),z(e))}M();
