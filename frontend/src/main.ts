import "./style.css";
import { readCsvHeaders } from "./csv";

interface ProjectInfo {
  project: string;
  database: string;
  mappings: number;
  review_items: number;
  semantic_index_status: "fresh" | "refresh_required" | "unverified" | "missing";
  semantic_index_warning: string | null;
}

interface ReviewItem {
  id: number;
  raw_text: string;
  suggested_text: string;
}

let focusRefresh: (() => void) | undefined;

function setFocusRefresh(refresh?: () => void): void {
  if (focusRefresh) window.removeEventListener("focus", focusRefresh);
  focusRefresh = refresh;
  if (focusRefresh) window.addEventListener("focus", focusRefresh);
}

function projectName(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).at(-1) ?? path;
}

async function fetchProject(): Promise<ProjectInfo> {
  const response = await fetch("/project/info");
  if (!response.ok) {
    const error = await response.json() as { detail?: string };
    throw new Error(error.detail ?? `Could not open Project (${response.status}).`);
  }
  return response.json() as Promise<ProjectInfo>;
}

async function fetchReviewItems(): Promise<ReviewItem[]> {
  const response = await fetch("/review-items");
  if (!response.ok) {
    const error = await response.json() as { detail?: string };
    throw new Error(error.detail ?? `Could not load Review Items (${response.status}).`);
  }
  return response.json() as Promise<ReviewItem[]>;
}

function showNotice(root: HTMLElement, message: string, error = false): void {
  const notices = root.querySelector<HTMLElement>("#notices")!;
  notices.innerHTML = "";
  const notice = document.createElement("p");
  notice.role = error ? "alert" : "status";
  notice.textContent = message;
  notices.append(notice);
}

function updateProjectSummary(root: HTMLElement, project: ProjectInfo): void {
  root.querySelector<HTMLElement>("#mapping-count")!.textContent = String(project.mappings);
  root.querySelector<HTMLElement>("#review-item-count")!.textContent = String(project.review_items);
  const indexStatus = root.querySelector<HTMLElement>("#semantic-index-status")!;
  indexStatus.textContent = project.semantic_index_warning ?? "";
  if (project.semantic_index_warning) indexStatus.role = "status";
  else indexStatus.removeAttribute("role");
}

async function acceptReviewItem(
  root: HTMLElement,
  item: ReviewItem,
  row: HTMLTableRowElement,
): Promise<void> {
  const button = row.querySelector<HTMLButtonElement>("button")!;
  button.disabled = true;
  try {
    const response = await fetch(`/review-items/${item.id}/accept`, {
      method: "POST",
    });
    if (!response.ok) {
      const error = await response.json() as { detail?: string };
      throw Object.assign(
        new Error(error.detail ?? `Could not accept Review Item (${response.status}).`),
        { stale: response.status === 409 },
      );
    }
    row.remove();
    showNotice(root, `Review Item ${item.id} accepted.`);
    await refreshProject(root);
  } catch (error) {
    const stale = error instanceof Error && "stale" in error && error.stale === true;
    showNotice(root, error instanceof Error ? error.message : "Could not accept Review Item.", true);
    if (stale) {
      await refreshProject(root);
    } else {
      button.disabled = false;
    }
  }
}

function renderReviewItems(root: HTMLElement, items: ReviewItem[]): void {
  const region = root.querySelector<HTMLElement>("#review-queue")!;
  if (!items.length) {
    region.innerHTML = '<p class="empty-state" role="status">No pending Review Items.</p>';
    return;
  }

  region.innerHTML = `
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
  `;
  const body = region.querySelector("tbody")!;
  const selectedIds = new Set<number>();
  const eligibleItems = items.filter((item) => Boolean(item.suggested_text.trim()));
  const acceptSelected = region.querySelector<HTMLButtonElement>("#accept-selected")!;
  const selectAll = region.querySelector<HTMLInputElement>(
    'input[aria-label="Select all eligible Review Items"]',
  )!;

  function updateBulkControls(): void {
    acceptSelected.textContent = `Accept selected (${selectedIds.size})`;
    acceptSelected.disabled = selectedIds.size === 0;
    selectAll.checked = eligibleItems.length > 0 && selectedIds.size === eligibleItems.length;
    selectAll.indeterminate = selectedIds.size > 0 && selectedIds.size < eligibleItems.length;
    selectAll.disabled = eligibleItems.length === 0;
  }

  selectAll.addEventListener("change", () => {
    selectedIds.clear();
    if (selectAll.checked) {
      eligibleItems.forEach((item) => selectedIds.add(item.id));
    }
    region.querySelectorAll<HTMLInputElement>('tbody input[type="checkbox"]')
      .forEach((checkbox) => {
        checkbox.checked = selectedIds.has(Number(checkbox.dataset.reviewItemId));
      });
    updateBulkControls();
  });

  acceptSelected.addEventListener("click", async () => {
    const reviewItemIds = items
      .map((item) => item.id)
      .filter((recordId) => selectedIds.has(recordId));
    if (!window.confirm(`Accept ${reviewItemIds.length} selected Review Items?`)) return;

    acceptSelected.disabled = true;
    try {
      const response = await fetch("/review-items/bulk-accept", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ review_item_ids: reviewItemIds }),
      });
      if (!response.ok) {
        const error = await response.json() as { detail?: string };
        throw new Error(error.detail ?? `Could not accept selected Review Items (${response.status}).`);
      }
      const result = await response.json() as { accepted: number };
      showNotice(root, `Accepted ${result.accepted} Review Items.`);
      await refreshProject(root);
    } catch (error) {
      showNotice(
        root,
        error instanceof Error ? error.message : "Could not accept selected Review Items.",
        true,
      );
      updateBulkControls();
    }
  });

  function beginEdit(
    item: ReviewItem,
    suggestionCell: HTMLTableCellElement,
    actionCell: HTMLTableCellElement,
  ): void {
    region.querySelectorAll<HTMLButtonElement>('button[data-action="edit"]')
      .forEach((button) => { button.disabled = true; });
    const input = document.createElement("input");
    input.type = "text";
    input.value = item.suggested_text;
    input.setAttribute("aria-label", `Normalized text for Review Item ${item.id}`);
    suggestionCell.replaceChildren(input);

    const save = document.createElement("button");
    save.type = "button";
    save.textContent = "Save and Accept";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.textContent = "Cancel";
    const cancelEdit = () => renderReviewItems(root, items);
    const submitEdit = async () => {
      if (!input.value.trim()) {
        showNotice(root, "Normalized text must not be blank.", true);
        return;
      }
      save.disabled = true;
      cancel.disabled = true;
      try {
        const response = await fetch(`/review-items/${item.id}/accept`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ normalized_text: input.value }),
        });
        if (!response.ok) {
          const error = await response.json() as { detail?: string };
          throw Object.assign(
            new Error(error.detail ?? `Could not accept Review Item (${response.status}).`),
            { stale: response.status === 409 },
          );
        }
        showNotice(root, `Review Item ${item.id} accepted.`);
        await refreshProject(root);
      } catch (error) {
        const stale = error instanceof Error && "stale" in error && error.stale === true;
        showNotice(
          root,
          error instanceof Error ? error.message : "Could not accept Review Item.",
          true,
        );
        if (stale) {
          await refreshProject(root);
        } else {
          save.disabled = false;
          cancel.disabled = false;
        }
      }
    };
    save.addEventListener("click", () => void submitEdit());
    cancel.addEventListener("click", cancelEdit);
    input.addEventListener("keydown", (event) => {
      if (event.key === "Escape") cancelEdit();
      if (event.key === "Enter") {
        event.preventDefault();
        void submitEdit();
      }
    });
    actionCell.replaceChildren(save, cancel);
    input.focus();
  }

  for (const item of items) {
    const row = document.createElement("tr");
    row.className = "review-card";
    const checkboxCell = document.createElement("td");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.setAttribute("aria-label", `Select Review Item ${item.id}`);
    checkbox.dataset.reviewItemId = String(item.id);
    checkbox.disabled = !item.suggested_text.trim();
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) selectedIds.add(item.id);
      else selectedIds.delete(item.id);
      updateBulkControls();
    });
    checkboxCell.append(checkbox);
    const idCell = document.createElement("td");
    idCell.textContent = String(item.id);
    const rawCell = document.createElement("td");
    rawCell.textContent = item.raw_text;
    const suggestionCell = document.createElement("td");
    suggestionCell.textContent = item.suggested_text;
    const actionCell = document.createElement("td");
    const accept = document.createElement("button");
    accept.type = "button";
    accept.textContent = "Accept";
    accept.disabled = !item.suggested_text.trim();
    accept.addEventListener("click", () => void acceptReviewItem(root, item, row));
    const edit = document.createElement("button");
    edit.type = "button";
    edit.dataset.action = "edit";
    edit.textContent = "Edit";
    edit.addEventListener("click", () => beginEdit(item, suggestionCell, actionCell));
    actionCell.append(accept, edit);
    row.append(checkboxCell, idCell, rawCell, suggestionCell, actionCell);
    body.append(row);
  }
  updateBulkControls();
}

async function refreshReviewItems(root: HTMLElement): Promise<void> {
  const region = root.querySelector<HTMLElement>("#review-queue")!;
  region.innerHTML = '<p role="status">Loading Review Items…</p>';
  try {
    renderReviewItems(root, await fetchReviewItems());
  } catch (error) {
    region.innerHTML = "";
    const message = document.createElement("p");
    message.role = "alert";
    message.textContent = error instanceof Error ? error.message : "Could not load Review Items.";
    region.append(message);
  }
}

async function refreshProject(root: HTMLElement): Promise<void> {
  try {
    const project = await fetchProject();
    updateProjectSummary(root, project);
  } catch (error) {
    showNotice(root, error instanceof Error ? error.message : "Could not refresh Project.", true);
  }
  await refreshReviewItems(root);
}

type ProjectTab = "import" | "review";

function selectProjectTab(root: HTMLElement, selected: ProjectTab): void {
  root.querySelectorAll<HTMLButtonElement>('[role="tab"]')
    .forEach((tab) => {
      const active = tab.dataset.tab === selected;
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
    });
  root.querySelector<HTMLElement>("#import-panel")!.hidden = selected !== "import";
  root.querySelector<HTMLElement>("#review-panel")!.hidden = selected !== "review";
}

function setupMappingImport(root: HTMLElement): void {
  const form = root.querySelector<HTMLFormElement>("#mapping-import-form")!;
  const fileInput = root.querySelector<HTMLInputElement>("#mapping-file")!;
  const source = root.querySelector<HTMLSelectElement>("#mapping-source-column")!;
  const target = root.querySelector<HTMLSelectElement>("#mapping-target-column")!;
  const submit = form.querySelector<HTMLButtonElement>('button[type="submit"]')!;
  let submitting = false;

  function populate(select: HTMLSelectElement, headers: string[], preferred: string): void {
    select.replaceChildren(new Option("Choose a header", ""));
    headers.forEach((header) => select.add(new Option(header, header)));
    select.value = headers.includes(preferred) ? preferred : "";
    select.disabled = false;
  }

  function resetHeaderSelections(): void {
    source.replaceChildren(new Option("Choose a header", ""));
    target.replaceChildren(new Option("Choose a header", ""));
    source.disabled = true;
    target.disabled = true;
    target.setCustomValidity("");
  }

  fileInput.addEventListener("change", async () => {
    resetHeaderSelections();
    const file = fileInput.files?.[0];
    if (!file) return;
    try {
      const headers = await readCsvHeaders(file);
      populate(source, headers, "raw_text");
      populate(target, headers, "normalized_text");
    } catch (error) {
      showNotice(
        root,
        error instanceof Error ? error.message : "Could not read the selected CSV.",
        true,
      );
    }
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (submitting) return;
    if (!fileInput.files?.[0] || !source.value || !target.value) {
      showNotice(root, "Choose a CSV file and both source and target headers.", true);
      return;
    }
    if (source.value === target.value) {
      target.setCustomValidity("Source and target headers must differ.");
      showNotice(root, "Source and target headers must differ.", true);
      target.focus();
      return;
    }
    target.setCustomValidity("");

    submitting = true;
    submit.disabled = true;
    submit.textContent = "Importing…";
    try {
      const body = new FormData();
      body.append("file", fileInput.files[0]);
      const response = await fetch(
        `/import/mappings?source_column=${encodeURIComponent(source.value)}`
          + `&target_column=${encodeURIComponent(target.value)}`,
        { method: "POST", body },
      );
      if (!response.ok) {
        const error = await response.json() as { detail?: string };
        throw new Error(error.detail ?? `Could not import Mappings (${response.status}).`);
      }
      const result = await response.json() as { imported: number; skipped: number };
      form.reset();
      resetHeaderSelections();
      showNotice(root, `Imported ${result.imported} Mappings; skipped ${result.skipped}.`);
      await refreshProject(root);
    } catch (error) {
      showNotice(
        root,
        error instanceof Error ? error.message : "Could not import Mappings.",
        true,
      );
    } finally {
      submitting = false;
      submit.disabled = false;
      submit.textContent = "Import Mappings";
    }
  });

  [source, target].forEach((select) => select.addEventListener("change", () => {
    if (source.value !== target.value) target.setCustomValidity("");
  }));
}

function showProject(root: HTMLElement, project: ProjectInfo): void {
  const initialTab: ProjectTab = project.review_items > 0 ? "review" : "import";
  root.innerHTML = `
    <header>
      <div>
        <span class="eyebrow">Project</span>
        <h1></h1>
        <p class="project-path"></p>
      </div>
      <div class="counts">
        <div><strong id="mapping-count">${project.mappings}</strong> Mappings</div>
        <div><strong id="review-item-count">${project.review_items}</strong> pending Review Items</div>
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
  `;
  root.querySelector("h1")!.textContent = projectName(project.project);
  root.querySelector<HTMLElement>(".project-path")!.textContent = project.project;
  updateProjectSummary(root, project);
  selectProjectTab(root, initialTab);
  const tabs = [...root.querySelectorAll<HTMLButtonElement>('[role="tab"]')];
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => {
      selectProjectTab(root, tab.dataset.tab as ProjectTab);
    });
    tab.addEventListener("keydown", (event) => {
      let nextIndex: number | undefined;
      if (event.key === "ArrowRight") nextIndex = (index + 1) % tabs.length;
      if (event.key === "ArrowLeft") nextIndex = (index - 1 + tabs.length) % tabs.length;
      if (event.key === "Home") nextIndex = 0;
      if (event.key === "End") nextIndex = tabs.length - 1;
      if (nextIndex === undefined) return;
      event.preventDefault();
      const next = tabs[nextIndex];
      selectProjectTab(root, next.dataset.tab as ProjectTab);
      next.focus();
    });
  });
  setupMappingImport(root);
  root.querySelector("#refresh-review-items")!.addEventListener(
    "click",
    () => void refreshProject(root),
  );
  setFocusRefresh(() => void refreshProject(root));
  void refreshReviewItems(root);
}

async function loadBoundProject(root: HTMLElement): Promise<void> {
  setFocusRefresh();
  root.innerHTML = '<main class="review-project"><p role="status">Loading Project…</p></main>';
  try {
    showProject(root, await fetchProject());
  } catch (error) {
    const message = document.createElement("p");
    message.role = "alert";
    message.textContent = error instanceof Error ? error.message : "Could not load Project.";
    root.replaceChildren(message);
  }
}

export function startApp(): void {
  const root = document.querySelector<HTMLElement>("#app");
  if (!root) return;
  void loadBoundProject(root);
}

startApp();
