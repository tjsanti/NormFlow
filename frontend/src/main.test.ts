import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { startApp } from "./main";

const projectInfo = {
  project: "/Users/example/projects/customer-names",
  database: "/Users/example/projects/customer-names/normflow.db",
  mappings: 12,
  review_items: 4,
  semantic_index_status: "fresh",
  semantic_index_warning: null,
};

function okJson(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function stubFetch(
  fetchMock: ReturnType<typeof vi.fn>,
  updateStatus: unknown = null,
): ReturnType<typeof vi.fn> {
  const request = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    if (typeof input === "string" && input.startsWith("/update-status?browser_date=")) {
      if (updateStatus instanceof Error) return Promise.reject(updateStatus);
      return Promise.resolve(okJson(updateStatus));
    }
    return init === undefined ? fetchMock(input) : fetchMock(input, init);
  });
  vi.stubGlobal("fetch", request);
  return request;
}

function chooseFile(input: HTMLInputElement, contents: string, name = "mappings.csv"): File {
  const file = new File([contents], name, { type: "text/csv" });
  Object.defineProperty(input, "files", { configurable: true, value: [file] });
  input.dispatchEvent(new Event("change", { bubbles: true }));
  return file;
}

class ControlledFileReader {
  result: string | null = null;
  private listeners: Partial<Record<"load" | "error", () => void>> = {};

  addEventListener(type: "load" | "error", listener: () => void): void {
    this.listeners[type] = listener;
  }

  readAsText(): void {}

  resolve(contents: string): void {
    this.result = contents;
    this.listeners.load?.();
  }

  reject(): void {
    this.listeners.error?.();
  }
}

function useControlledFileReaders(): ControlledFileReader[] {
  const readers: ControlledFileReader[] = [];
  vi.stubGlobal("FileReader", class extends ControlledFileReader {
    constructor() {
      super();
      readers.push(this);
    }
  });
  return readers;
}

describe("Bound Project launch", () => {
  beforeEach(() => {
    document.body.innerHTML = '<div id="app"></div>';
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  test("shows the Project summary above accessible Import and Review Items tabs", async () => {
    stubFetch(vi.fn()
      .mockResolvedValueOnce(okJson(projectInfo))
      .mockResolvedValueOnce(okJson([])));

    startApp();

    await vi.waitFor(() => expect(document.querySelector(".empty-state")).not.toBeNull());
    const tabs = [...document.querySelectorAll<HTMLElement>('[role="tab"]')];
    expect(tabs.map((tab) => tab.textContent)).toEqual(["Import", "Review Items"]);
    expect(tabs.map((tab) => tab.getAttribute("aria-selected"))).toEqual(["false", "true"]);
    expect(document.querySelector<HTMLElement>("#import-panel")?.hidden).toBe(true);
    expect(document.querySelector<HTMLElement>("#review-panel")?.hidden).toBe(false);
    expect(document.querySelector("header")!.compareDocumentPosition(tabs[0]))
      .toBe(Node.DOCUMENT_POSITION_FOLLOWING);
  });

  test("keyboard tab selection remains selected across Project refreshes", async () => {
    const emptyProject = { ...projectInfo, review_items: 0 };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(okJson(emptyProject))
      .mockResolvedValueOnce(okJson([]))
      .mockResolvedValueOnce(okJson(emptyProject))
      .mockResolvedValueOnce(okJson([]));
    stubFetch(fetchMock);
    startApp();

    await vi.waitFor(() => expect(document.querySelector(".empty-state")).not.toBeNull());
    const importTab = document.querySelector<HTMLButtonElement>("#import-tab")!;
    const reviewTab = document.querySelector<HTMLButtonElement>("#review-tab")!;
    importTab.focus();
    importTab.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));

    expect(reviewTab.getAttribute("aria-selected")).toBe("true");
    expect(document.activeElement).toBe(reviewTab);
    document.querySelector<HTMLButtonElement>("#refresh-review-items")!.click();
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(4));
    expect(reviewTab.getAttribute("aria-selected")).toBe("true");
  });

  test("immediately loads review for the server-bound canonical Project", async () => {
    window.localStorage.setItem("normflow.recentProjects", JSON.stringify(["/old/project"]));
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(okJson(projectInfo))
      .mockResolvedValueOnce(okJson([]));
    stubFetch(fetchMock);
    startApp();

    await vi.waitFor(() => expect(document.querySelector(".empty-state")).not.toBeNull());

    expect(fetchMock).toHaveBeenNthCalledWith(1, "/project/info");
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/review-items");
    expect(document.body.textContent).toContain("customer-names");
    expect(document.body.textContent).toContain(projectInfo.project);
    expect(document.body.textContent).toContain("12 Mappings");
    expect(document.body.textContent).toContain("4 pending Review Items");
    expect(document.querySelector("#import-panel form")).not.toBeNull();
    expect(document.querySelector("#project-path")).toBeNull();
    expect(document.body.textContent).not.toContain("Switch Project");
    expect(document.body.textContent).not.toContain("Recent Projects");
    expect(window.localStorage.getItem("normflow.recentProjects"))
      .toBe(JSON.stringify(["/old/project"]));
  });

  test("renders the canonical Project path as text", async () => {
    const project = {
      ...projectInfo,
      project: "/tmp/<img src=x onerror=alert(1)>",
    };
    stubFetch(vi.fn()
      .mockResolvedValueOnce(okJson(project))
      .mockResolvedValueOnce(okJson([])));

    startApp();

    await vi.waitFor(() => expect(document.querySelector(".empty-state")).not.toBeNull());
    expect(document.querySelector("header img")).toBeNull();
    expect(document.querySelector("h1")?.textContent).toBe("<img src=x onerror=alert(1)>");
    expect(document.querySelector(".project-path")?.textContent).toBe(project.project);
  });

  test("shows the Project name for a canonical Windows path", async () => {
    const project = {
      ...projectInfo,
      project: "C:\\Projects\\customer-names",
    };
    stubFetch(vi.fn()
      .mockResolvedValueOnce(okJson(project))
      .mockResolvedValueOnce(okJson([])));

    startApp();

    await vi.waitFor(() => expect(document.querySelector(".empty-state")).not.toBeNull());
    expect(document.querySelector("h1")?.textContent).toBe("customer-names");
    expect(document.querySelector(".project-path")?.textContent).toBe(project.project);
  });

  test("keeps semantic index refresh status visible without blocking Review", async () => {
    const project = {
      ...projectInfo,
      semantic_index_status: "refresh_required",
      semantic_index_warning: "The semantic index will refresh before the next semantic Suggestion.",
    };
    stubFetch(vi.fn()
      .mockResolvedValueOnce(okJson(project))
      .mockResolvedValueOnce(okJson([])));

    startApp();

    await vi.waitFor(() => expect(document.querySelector(".empty-state")).not.toBeNull());
    const status = document.querySelector<HTMLElement>("#semantic-index-status");
    expect(status?.textContent).toContain(project.semantic_index_warning);
    expect(status?.getAttribute("aria-live")).toBe("polite");
    expect(document.querySelector<HTMLButtonElement>("#refresh-review-items")?.disabled).toBe(false);
  });

  test("shows and dismisses an accessible update banner with the exact installer command", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(2026, 6, 16, 12));
    const installCommand = "curl --proto '=https' --tlsv1.2 --fail --silent "
      + "--show-error --location https://github.com/tjsanti/NormFlow/releases/latest/"
      + "download/install.sh | sh";
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(okJson(projectInfo))
      .mockResolvedValueOnce(okJson([]))
      .mockResolvedValueOnce(okJson({ status: "dismissed" }));
    const requests = stubFetch(fetchMock, {
      installed_version: "0.1.0",
      latest_version: "0.2.0",
      install_command: installCommand,
    });

    startApp();

    await vi.waitFor(() => expect(document.querySelector("#update-banner")).not.toBeNull());
    expect(requests).toHaveBeenCalledWith(
      "/update-status?browser_date=2026-07-16",
    );
    const banner = document.querySelector<HTMLElement>("#update-banner")!;
    const dismiss = banner.querySelector<HTMLButtonElement>("button")!;
    expect(banner.textContent).toContain("0.1.0");
    expect(banner.textContent).toContain("0.2.0");
    expect(banner.querySelector("code")?.textContent).toBe(installCommand);
    expect(dismiss.type).toBe("button");
    dismiss.focus();
    expect(document.activeElement).toBe(dismiss);

    dismiss.click();

    await vi.waitFor(() => expect(document.querySelector("#update-banner")).toBeNull());
    expect(fetchMock).toHaveBeenNthCalledWith(3, "/update-status/dismiss", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        latest_version: "0.2.0",
        browser_date: "2026-07-16",
      }),
    });
  });

  test.each([
    ["current", null],
    ["dismissed today", null],
    ["opted out", null],
    ["offline", new Error("offline")],
    ["malformed response", { latest_version: "0.2.0" }],
  ])("keeps the Project usable without a banner when update status is %s", async (_, status) => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(okJson(projectInfo))
      .mockResolvedValueOnce(okJson([]));
    stubFetch(fetchMock, status);

    startApp();

    await vi.waitFor(() => expect(document.querySelector(".empty-state")).not.toBeNull());
    expect(document.querySelector("#update-banner")).toBeNull();
    expect(document.querySelector<HTMLButtonElement>("#refresh-review-items")?.disabled)
      .toBe(false);
  });

  test("requests the update again on the next browser-local day", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(2026, 6, 16, 23, 59));
    const firstFetch = vi.fn()
      .mockResolvedValueOnce(okJson(projectInfo))
      .mockResolvedValueOnce(okJson([]))
      .mockResolvedValueOnce(okJson({ status: "dismissed" }));
    const firstRequests = stubFetch(
      firstFetch,
      {
        installed_version: "0.1.0",
        latest_version: "0.2.0",
        install_command: "install command",
      },
    );
    startApp();

    await vi.waitFor(() => expect(document.querySelector("#update-banner")).not.toBeNull());
    expect(firstRequests).toHaveBeenCalledWith(
      "/update-status?browser_date=2026-07-16",
    );
    document.querySelector<HTMLButtonElement>("#update-banner button")!.click();
    await vi.waitFor(() => expect(document.querySelector("#update-banner")).toBeNull());
    expect(firstFetch).toHaveBeenNthCalledWith(3, "/update-status/dismiss", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        latest_version: "0.2.0",
        browser_date: "2026-07-16",
      }),
    });

    vi.setSystemTime(new Date(2026, 6, 17, 0, 1));
    document.body.innerHTML = '<div id="app"></div>';
    const nextRequests = stubFetch(
      vi.fn()
        .mockResolvedValueOnce(okJson(projectInfo))
        .mockResolvedValueOnce(okJson([])),
      {
        installed_version: "0.1.0",
        latest_version: "0.2.0",
        install_command: "install command",
      },
    );
    startApp();

    await vi.waitFor(() => expect(document.querySelector("#update-banner")).not.toBeNull());
    expect(nextRequests).toHaveBeenCalledWith(
      "/update-status?browser_date=2026-07-17",
    );
    expect(document.querySelector("#update-banner")?.textContent).toContain("0.2.0");
  });
});

describe("Mapping Import", () => {
  beforeEach(() => {
    document.body.innerHTML = '<div id="app"></div>';
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  test("opens Import when no work is pending and selects only exact standard CSV headers", async () => {
    stubFetch(vi.fn()
      .mockResolvedValueOnce(okJson({ ...projectInfo, review_items: 0 }))
      .mockResolvedValueOnce(okJson([])));
    startApp();

    await vi.waitFor(() => expect(document.querySelector(".empty-state")).not.toBeNull());
    expect(document.querySelector("#import-tab")?.getAttribute("aria-selected")).toBe("true");
    const fileInput = document.querySelector<HTMLInputElement>('#import-panel input[type="file"]')!;
    chooseFile(fileInput, "name,raw_text,notes,normalized_text\r\nN,O2 sensor,n,Oxygen Sensor\r\n");

    const source = document.querySelector<HTMLSelectElement>("#mapping-source-column")!;
    const target = document.querySelector<HTMLSelectElement>("#mapping-target-column")!;
    await vi.waitFor(() => expect(source.options).toHaveLength(5));
    expect([...source.options].map((option) => option.textContent)).toEqual([
      "Choose a header", "name", "raw_text", "notes", "normalized_text",
    ]);
    expect(source.value).toBe("raw_text");
    expect(target.value).toBe("normalized_text");
    expect(source.required).toBe(true);
    expect(target.required).toBe(true);
  });

  test("does not guess nonstandard headers and rejects matching source and target selections", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(okJson({ ...projectInfo, review_items: 0 }))
      .mockResolvedValueOnce(okJson([]));
    stubFetch(fetchMock);
    startApp();

    await vi.waitFor(() => expect(document.querySelector("#mapping-file")).not.toBeNull());
    chooseFile(document.querySelector<HTMLInputElement>("#mapping-file")!, "source,target\nA,B\n");
    const source = document.querySelector<HTMLSelectElement>("#mapping-source-column")!;
    const target = document.querySelector<HTMLSelectElement>("#mapping-target-column")!;
    await vi.waitFor(() => expect(source.disabled).toBe(false));
    expect(source.value).toBe("");
    expect(target.value).toBe("");

    source.value = "source";
    target.value = "source";
    document.querySelector<HTMLFormElement>("#mapping-import-form")!
      .dispatchEvent(new SubmitEvent("submit", { bubbles: true, cancelable: true }));

    expect(document.querySelector("[role=alert]")?.textContent)
      .toContain("Source and target headers must differ");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  test("submits multipart data once, reports counts, refreshes the Project, and resets", async () => {
    let resolveImport!: (response: Response) => void;
    const importResponse = new Promise<Response>((resolve) => { resolveImport = resolve; });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(okJson({ ...projectInfo, review_items: 0 }))
      .mockResolvedValueOnce(okJson([]))
      .mockReturnValueOnce(importResponse)
      .mockResolvedValueOnce(okJson({ ...projectInfo, mappings: 14, review_items: 0 }))
      .mockResolvedValueOnce(okJson([]));
    stubFetch(fetchMock);
    startApp();

    await vi.waitFor(() => expect(document.querySelector("#mapping-file")).not.toBeNull());
    const file = chooseFile(
      document.querySelector<HTMLInputElement>("#mapping-file")!,
      "raw_text,normalized_text\nO2 sensor,Oxygen Sensor\n",
    );
    const source = document.querySelector<HTMLSelectElement>("#mapping-source-column")!;
    await vi.waitFor(() => expect(source.value).toBe("raw_text"));
    const form = document.querySelector<HTMLFormElement>("#mapping-import-form")!;
    form.dispatchEvent(new SubmitEvent("submit", { bubbles: true, cancelable: true }));
    form.dispatchEvent(new SubmitEvent("submit", { bubbles: true, cancelable: true }));

    const submit = form.querySelector<HTMLButtonElement>('button[type="submit"]')!;
    expect(submit.disabled).toBe(true);
    expect(submit.textContent).toBe("Processing mappings.csv…");
    expect([...document.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLButtonElement>(
      "#batch-import-form input, #batch-import-form select, #batch-import-form button",
    )].every((control) => control.disabled)).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    const [url, request] = fetchMock.mock.calls[2] as [string, RequestInit];
    expect(url).toBe("/import/mappings?source_column=raw_text&target_column=normalized_text");
    expect(request.method).toBe("POST");
    expect((request.body as FormData).get("file")).toBe(file);

    resolveImport(okJson({ imported: 2, skipped: 1 }));
    await vi.waitFor(() => expect(submit.disabled).toBe(false));
    expect(document.querySelector("#notices [role=status]")?.textContent)
      .toContain("Imported 2 Mappings; skipped 1");
    expect(document.querySelector("header")?.textContent).toContain("14 Mappings");
    expect(source.disabled).toBe(true);
    expect(source.value).toBe("");
    expect(document.querySelector("#import-tab")?.getAttribute("aria-selected")).toBe("true");
    expect(fetchMock).toHaveBeenCalledTimes(5);
  });

  test("surfaces the API detail and preserves the selected file and headers for retry", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(okJson({ ...projectInfo, review_items: 0 }))
      .mockResolvedValueOnce(okJson([]))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        detail: "CSV does not contain a column named 'clean'. Available columns: raw, approved",
      }), { status: 400, headers: { "Content-Type": "application/json" } }));
    stubFetch(fetchMock);
    startApp();

    await vi.waitFor(() => expect(document.querySelector("#mapping-file")).not.toBeNull());
    const fileInput = document.querySelector<HTMLInputElement>("#mapping-file")!;
    const file = chooseFile(fileInput, "raw,approved\nO2 sensor,Oxygen Sensor\n");
    const source = document.querySelector<HTMLSelectElement>("#mapping-source-column")!;
    const target = document.querySelector<HTMLSelectElement>("#mapping-target-column")!;
    await vi.waitFor(() => expect(source.disabled).toBe(false));
    source.value = "raw";
    target.value = "approved";
    document.querySelector<HTMLFormElement>("#mapping-import-form")!
      .dispatchEvent(new SubmitEvent("submit", { bubbles: true, cancelable: true }));

    await vi.waitFor(() => expect(document.querySelector("[role=alert]")).not.toBeNull());
    expect(document.querySelector("[role=alert]")?.textContent).toContain("Available columns: raw, approved");
    expect(fileInput.files?.[0]).toBe(file);
    expect(source.value).toBe("raw");
    expect(target.value).toBe("approved");
    expect(document.querySelector<HTMLButtonElement>('#mapping-import-form button[type="submit"]')?.disabled)
      .toBe(false);
  });

  test("clears stale headers as soon as a different CSV is selected", async () => {
    stubFetch(vi.fn()
      .mockResolvedValueOnce(okJson({ ...projectInfo, review_items: 0 }))
      .mockResolvedValueOnce(okJson([])));
    startApp();

    await vi.waitFor(() => expect(document.querySelector("#mapping-file")).not.toBeNull());
    const fileInput = document.querySelector<HTMLInputElement>("#mapping-file")!;
    const source = document.querySelector<HTMLSelectElement>("#mapping-source-column")!;
    const target = document.querySelector<HTMLSelectElement>("#mapping-target-column")!;
    chooseFile(fileInput, "raw,approved\nA,B\n");
    await vi.waitFor(() => expect(source.options).toHaveLength(3));
    source.value = "raw";
    target.value = "approved";

    chooseFile(fileInput, "", "empty.csv");
    expect(source.disabled).toBe(true);
    expect(target.disabled).toBe(true);
    expect(source.value).toBe("");
    expect(target.value).toBe("");
    await vi.waitFor(() => expect(document.querySelector("[role=alert]")?.textContent)
      .toContain("empty"));
    expect(source.disabled).toBe(true);
  });

  test("ignores headers from an older file selection that finishes last", async () => {
    const readers = useControlledFileReaders();
    stubFetch(vi.fn()
      .mockResolvedValueOnce(okJson({ ...projectInfo, review_items: 0 }))
      .mockResolvedValueOnce(okJson([])));
    startApp();

    await vi.waitFor(() => expect(document.querySelector("#mapping-file")).not.toBeNull());
    const fileInput = document.querySelector<HTMLInputElement>("#mapping-file")!;
    const source = document.querySelector<HTMLSelectElement>("#mapping-source-column")!;
    chooseFile(fileInput, "old_source,old_target\nA,B\n", "old.csv");
    chooseFile(fileInput, "new_source,new_target\nC,D\n", "new.csv");

    readers[1].resolve("new_source,new_target\nC,D\n");
    await vi.waitFor(() => expect(source.options[1]?.textContent).toBe("new_source"));
    readers[0].resolve("old_source,old_target\nA,B\n");
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect([...source.options].map((option) => option.textContent)).toEqual([
      "Choose a header", "new_source", "new_target",
    ]);
  });

  test("ignores a read error from an older file selection", async () => {
    const readers = useControlledFileReaders();
    stubFetch(vi.fn()
      .mockResolvedValueOnce(okJson({ ...projectInfo, review_items: 0 }))
      .mockResolvedValueOnce(okJson([])));
    startApp();

    await vi.waitFor(() => expect(document.querySelector("#mapping-file")).not.toBeNull());
    const fileInput = document.querySelector<HTMLInputElement>("#mapping-file")!;
    const source = document.querySelector<HTMLSelectElement>("#mapping-source-column")!;
    chooseFile(fileInput, "old_source,old_target\nA,B\n", "old.csv");
    chooseFile(fileInput, "new_source,new_target\nC,D\n", "new.csv");

    readers[1].resolve("new_source,new_target\nC,D\n");
    await vi.waitFor(() => expect(source.options[1]?.textContent).toBe("new_source"));
    readers[0].reject();
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(document.querySelector("[role=alert]")).toBeNull();
    expect(source.options[1]?.textContent).toBe("new_source");
  });
});

describe("Batch Import", () => {
  beforeEach(() => {
    document.body.innerHTML = '<div id="app"></div>';
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  test("is the second Import workflow and stays available without Mappings", async () => {
    stubFetch(vi.fn()
      .mockResolvedValueOnce(okJson({
        ...projectInfo,
        mappings: 0,
        review_items: 0,
      }))
      .mockResolvedValueOnce(okJson([])));
    startApp();

    await vi.waitFor(() => expect(document.querySelector("#batch-import-form")).not.toBeNull());

    expect([...document.querySelectorAll("#import-panel h2")]
      .map((heading) => heading.textContent)).toEqual(["Mapping Import", "Batch Import"]);
    expect(document.querySelector<HTMLInputElement>("#batch-file")?.disabled).toBe(false);
    expect(document.querySelector("#batch-import-form")?.textContent).toContain("no Mappings");
    expect(document.querySelector("#batch-import-form")?.textContent).toContain("still import");
    expect(document.querySelector("#batch-import-form")?.textContent).toContain("replaces");
    expect(document.querySelector('#batch-import-form input[name="threshold"]')).toBeNull();
    expect(document.querySelector('#batch-import-form input[name="semantic"]')).toBeNull();
  });

  test("loads source headers and auto-selects only an exact raw_text header", async () => {
    stubFetch(vi.fn()
      .mockResolvedValueOnce(okJson({ ...projectInfo, review_items: 0 }))
      .mockResolvedValueOnce(okJson([])));
    startApp();

    await vi.waitFor(() => expect(document.querySelector("#batch-file")).not.toBeNull());
    const fileInput = document.querySelector<HTMLInputElement>("#batch-file")!;
    const source = document.querySelector<HTMLSelectElement>("#batch-source-column")!;
    chooseFile(fileInput, "id,raw_text,notes\n1,O2 sensor,urgent\n", "records.csv");

    await vi.waitFor(() => expect(source.options).toHaveLength(4));
    expect([...source.options].map((option) => option.textContent)).toEqual([
      "Choose a header", "id", "raw_text", "notes",
    ]);
    expect(source.value).toBe("raw_text");

    chooseFile(fileInput, "id,Raw Text,description\n1,O2 sensor,urgent\n", "other.csv");
    await vi.waitFor(() => expect(source.options[2]?.textContent).toBe("Raw Text"));
    expect(source.value).toBe("");
    expect(source.disabled).toBe(false);
  });

  test("processes one Batch filename with the full fallback API while both forms are locked", async () => {
    let resolveImport!: (response: Response) => void;
    const importResponse = new Promise<Response>((resolve) => { resolveImport = resolve; });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(okJson({ ...projectInfo, review_items: 0 }))
      .mockResolvedValueOnce(okJson([]))
      .mockReturnValueOnce(importResponse)
      .mockResolvedValueOnce(okJson({
        id: "run-1", status: "active", error: null, result: null,
      }))
      .mockResolvedValueOnce(okJson({
        id: "run-1", status: "succeeded", error: null,
        result: { auto_committed: 0, review_items: 0, skipped: 0 },
      }))
      .mockResolvedValueOnce(okJson(projectInfo))
      .mockResolvedValueOnce(okJson([]));
    stubFetch(fetchMock);
    startApp();

    await vi.waitFor(() => expect(document.querySelector("#batch-file")).not.toBeNull());
    const file = chooseFile(
      document.querySelector<HTMLInputElement>("#batch-file")!,
      "raw_text\nO2 sensor\n",
      "healthcare.csv",
    );
    const source = document.querySelector<HTMLSelectElement>("#batch-source-column")!;
    await vi.waitFor(() => expect(source.value).toBe("raw_text"));
    const form = document.querySelector<HTMLFormElement>("#batch-import-form")!;
    form.dispatchEvent(new SubmitEvent("submit", { bubbles: true, cancelable: true }));
    form.dispatchEvent(new SubmitEvent("submit", { bubbles: true, cancelable: true }));

    expect(fetchMock).toHaveBeenCalledTimes(3);
    const [url, request] = fetchMock.mock.calls[2] as [string, RequestInit];
    expect(url).toBe("/batch-import-runs?column=raw_text");
    expect(request.method).toBe("POST");
    expect((request.body as FormData).get("file")).toBe(file);
    expect(form.querySelector<HTMLButtonElement>('button[type="submit"]')?.textContent)
      .toBe("Processing healthcare.csv…");
    expect([...document.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLButtonElement>(
      "#mapping-import-form input, #mapping-import-form select, #mapping-import-form button, "
      + "#batch-import-form input, #batch-import-form select, #batch-import-form button",
    )].every((control) => control.disabled)).toBe(true);
    expect([...document.querySelectorAll("button")]
      .some((button) => button.textContent?.includes("Cancel"))).toBe(false);

    resolveImport(new Response(JSON.stringify({
      id: "run-1", status: "active", error: null, result: null,
    }), {
      status: 202,
      headers: {
        "Content-Type": "application/json",
        Location: "/batch-import-runs/run-1/status",
      },
    }));
    await vi.waitFor(() => expect(
      form.querySelector<HTMLButtonElement>('button[type="submit"]')?.disabled,
    ).toBe(false));
    expect(fetchMock).toHaveBeenNthCalledWith(4, "/batch-import-runs/run-1/status");
    expect(fetchMock).toHaveBeenNthCalledWith(5, "/batch-import-runs/run-1/status");
  });

  test("reports Batch counts, resets, refreshes, and opens newly pending Review Items", async () => {
    const warning = "The semantic index will refresh before the next semantic Suggestion.";
    const pending = [{ id: 7, raw_text: "O2 sensr", suggested_text: "Oxygen Sensor" }];
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(okJson({ ...projectInfo, review_items: 0 }))
      .mockResolvedValueOnce(okJson([]))
      .mockResolvedValueOnce(okJson({
        id: "run-2", status: "active", error: null, result: null,
      }))
      .mockResolvedValueOnce(okJson({
        id: "run-2", status: "succeeded", error: null,
        result: {
          auto_committed: 2,
          review_items: 1,
          skipped: 3,
          semantic_index_status: "refresh_required",
          semantic_index_warning: warning,
        },
      }))
      .mockResolvedValueOnce(okJson({
        ...projectInfo,
        mappings: 14,
        review_items: 1,
        semantic_index_status: "refresh_required",
        semantic_index_warning: warning,
      }))
      .mockResolvedValueOnce(okJson(pending));
    stubFetch(fetchMock);
    startApp();

    await vi.waitFor(() => expect(document.querySelector("#batch-file")).not.toBeNull());
    chooseFile(
      document.querySelector<HTMLInputElement>("#batch-file")!,
      "raw_text\nO2 sensor\nO2 sensr\n",
      "healthcare.csv",
    );
    const source = document.querySelector<HTMLSelectElement>("#batch-source-column")!;
    await vi.waitFor(() => expect(source.value).toBe("raw_text"));
    document.querySelector<HTMLFormElement>("#batch-import-form")!
      .dispatchEvent(new SubmitEvent("submit", { bubbles: true, cancelable: true }));

    await vi.waitFor(() => expect(
      document.querySelector("#review-tab")?.getAttribute("aria-selected"),
    ).toBe("true"));
    expect(document.querySelector("#notices [role=status]")?.textContent)
      .toContain("2 auto-committed, 1 Review Item, 3 skipped");
    expect(document.querySelector("header")?.textContent).toContain("14 Mappings");
    expect(document.querySelector("#review-queue")?.textContent).toContain("O2 sensr");
    expect(source.disabled).toBe(true);
    expect(source.value).toBe("");
    expect(document.querySelector("#semantic-index-status")?.textContent).toContain(warning);
    expect(fetchMock).toHaveBeenNthCalledWith(4, "/batch-import-runs/run-2");
    expect(fetchMock).toHaveBeenNthCalledWith(5, "/project/info");
    expect(fetchMock).toHaveBeenNthCalledWith(6, "/review-items");
  });

  test("stays on Import after a successful Batch creates no pending Review Items", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(okJson({ ...projectInfo, review_items: 0 }))
      .mockResolvedValueOnce(okJson([]))
      .mockResolvedValueOnce(okJson({
        id: "run-3", status: "succeeded", error: null,
        result: {
          auto_committed: 2,
          review_items: 0,
          skipped: 1,
          semantic_index_status: "refresh_required",
          semantic_index_warning: null,
        },
      }))
      .mockResolvedValueOnce(okJson({
        ...projectInfo,
        mappings: 14,
        review_items: 0,
      }))
      .mockResolvedValueOnce(okJson([]));
    stubFetch(fetchMock);
    startApp();

    await vi.waitFor(() => expect(document.querySelector("#batch-file")).not.toBeNull());
    chooseFile(
      document.querySelector<HTMLInputElement>("#batch-file")!,
      "raw_text\nO2 sensor\n",
      "exact-records.csv",
    );
    const source = document.querySelector<HTMLSelectElement>("#batch-source-column")!;
    await vi.waitFor(() => expect(source.value).toBe("raw_text"));
    document.querySelector<HTMLFormElement>("#batch-import-form")!
      .dispatchEvent(new SubmitEvent("submit", { bubbles: true, cancelable: true }));

    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(5));
    expect(document.querySelector("#import-tab")?.getAttribute("aria-selected")).toBe("true");
    expect(document.querySelector("#notices [role=status]")?.textContent)
      .toContain("2 auto-committed, 0 Review Items, 1 skipped");
  });

  test("keeps the failed Batch selections on Import and shows the actionable API detail", async () => {
    let rejectImport!: (response: Response) => void;
    const importResponse = new Promise<Response>((resolve) => { rejectImport = resolve; });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(okJson({ ...projectInfo, review_items: 0 }))
      .mockResolvedValueOnce(okJson([]))
      .mockReturnValueOnce(importResponse);
    stubFetch(fetchMock);
    startApp();

    await vi.waitFor(() => expect(document.querySelector("#batch-file")).not.toBeNull());
    const fileInput = document.querySelector<HTMLInputElement>("#batch-file")!;
    const file = chooseFile(fileInput, "name\nO2 sensor\n", "retry.csv");
    const source = document.querySelector<HTMLSelectElement>("#batch-source-column")!;
    await vi.waitFor(() => expect(source.disabled).toBe(false));
    source.value = "name";
    document.querySelector<HTMLFormElement>("#batch-import-form")!
      .dispatchEvent(new SubmitEvent("submit", { bubbles: true, cancelable: true }));
    document.querySelector<HTMLButtonElement>("#review-tab")!.click();
    expect(document.querySelector("#review-tab")?.getAttribute("aria-selected")).toBe("true");

    rejectImport(new Response(JSON.stringify({
      detail: {
        message: "The Project is currently being changed; try again later.",
        active_run: { id: "run-4", status: "active" },
      },
    }), { status: 502, headers: { "Content-Type": "application/json" } }));

    await vi.waitFor(() => expect(document.querySelector("#notices [role=alert]")).not.toBeNull());
    expect(document.querySelector("#notices [role=alert]")?.textContent)
      .toContain("currently being changed");
    expect(fileInput.files?.[0]).toBe(file);
    expect(source.value).toBe("name");
    expect(source.disabled).toBe(false);
    expect(document.querySelector("#import-tab")?.getAttribute("aria-selected")).toBe("true");
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });
});

describe("Review queue", () => {
  beforeEach(() => {
    document.body.innerHTML = '<div id="app"></div>';
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  test("opening a Project loads the complete queue in its table", async () => {
    let resolveQueue!: (response: Response) => void;
    const queueResponse = new Promise<Response>((resolve) => { resolveQueue = resolve; });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(okJson(projectInfo))
      .mockReturnValueOnce(queueResponse);
    stubFetch(fetchMock);
    startApp();

    await vi.waitFor(() => expect(document.querySelector("[role=status]")?.textContent).toContain("Loading"));
    resolveQueue(okJson([
      { id: 4, raw_text: "first raw", suggested_text: "First" },
      { id: 9, raw_text: "second raw", suggested_text: "" },
    ]));
    await vi.waitFor(() => expect(document.querySelectorAll("tbody tr")).toHaveLength(2));

    expect([...document.querySelectorAll("th")].map((cell) => cell.textContent?.trim())).toEqual([
      "Checkbox", "ID", "Raw Text", "Suggestion", "Actions",
    ]);
    expect([...document.querySelectorAll("tbody tr")].map((row) => row.textContent)).toEqual([
      expect.stringContaining("4first rawFirstAccept"),
      expect.stringContaining("9second rawAccept"),
    ]);
    expect(document.querySelectorAll<HTMLInputElement>('tbody input[type="checkbox"]')).toHaveLength(2);
    expect(document.querySelector("table")?.classList.contains("review-table")).toBe(true);
    expect(document.querySelectorAll("tbody tr.review-card")).toHaveLength(2);
    expect([...document.querySelectorAll<HTMLButtonElement>("tbody button")].map((button) => button.disabled)).toEqual([
      false, false, true, false,
    ]);
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/review-items");
  });

  test("only eligible rows can be selected and select-all covers the complete loaded queue", async () => {
    stubFetch(vi.fn()
      .mockResolvedValueOnce(okJson(projectInfo))
      .mockResolvedValueOnce(okJson([
        { id: 4, raw_text: "first raw", suggested_text: "First" },
        { id: 9, raw_text: "no suggestion", suggested_text: "  " },
        { id: 12, raw_text: "third raw", suggested_text: "Third" },
      ])));
    startApp();

    await vi.waitFor(() => expect(document.querySelectorAll("tbody tr")).toHaveLength(3));

    const selectedAction = document.querySelector<HTMLButtonElement>("#accept-selected")!;
    const rowCheckboxes = [...document.querySelectorAll<HTMLInputElement>(
      'tbody input[type="checkbox"]',
    )];
    expect(rowCheckboxes.map((checkbox) => checkbox.disabled)).toEqual([false, true, false]);
    expect(selectedAction.textContent).toBe("Accept selected (0)");
    expect(selectedAction.disabled).toBe(true);

    rowCheckboxes[0].click();
    expect(selectedAction.textContent).toBe("Accept selected (1)");
    expect(selectedAction.disabled).toBe(false);

    document.querySelector<HTMLInputElement>(
      'input[aria-label="Select all eligible Review Items"]',
    )!.click();
    expect(rowCheckboxes.map((checkbox) => checkbox.checked)).toEqual([true, false, true]);
    expect(selectedAction.textContent).toBe("Accept selected (2)");
  });

  test("confirmed bulk acceptance removes rows, clears selection, refreshes counts, and reports exact count", async () => {
    const remaining = [{ id: 9, raw_text: "no suggestion", suggested_text: "" }];
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(okJson(projectInfo))
      .mockResolvedValueOnce(okJson([
        { id: 4, raw_text: "first raw", suggested_text: "First" },
        ...remaining,
        { id: 12, raw_text: "third raw", suggested_text: "Third" },
      ]))
      .mockResolvedValueOnce(okJson({ accepted: 2 }))
      .mockResolvedValueOnce(okJson({ ...projectInfo, mappings: 14, review_items: 2 }))
      .mockResolvedValueOnce(okJson(remaining));
    stubFetch(fetchMock);
    const confirmMock = vi.fn().mockReturnValue(true);
    vi.stubGlobal("confirm", confirmMock);
    startApp();

    await vi.waitFor(() => expect(document.querySelectorAll("tbody tr")).toHaveLength(3));
    document.querySelector<HTMLInputElement>(
      'input[aria-label="Select all eligible Review Items"]',
    )!.click();
    document.querySelector<HTMLButtonElement>("#accept-selected")!.click();
    await vi.waitFor(() => expect(document.querySelectorAll("tbody tr")).toHaveLength(1));

    expect(confirmMock).toHaveBeenCalledWith("Accept 2 selected Review Items?");
    expect(fetchMock).toHaveBeenNthCalledWith(3, "/review-items/bulk-accept", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ review_item_ids: [4, 12] }),
    });
    expect(document.querySelector<HTMLButtonElement>("#accept-selected")!.textContent)
      .toBe("Accept selected (0)");
    expect(document.querySelector<HTMLButtonElement>("#accept-selected")!.disabled).toBe(true);
    expect(document.querySelector("[role=status]")?.textContent).toContain("Accepted 2 Review Items");
    expect(document.querySelector("header")?.textContent).toContain("14 Mappings");
    expect(document.querySelector("header")?.textContent).toContain("2 pending Review Items");
  });

  test("failed bulk acceptance preserves the loaded queue and exact selection", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(okJson(projectInfo))
      .mockResolvedValueOnce(okJson([
        { id: 4, raw_text: "first raw", suggested_text: "First" },
        { id: 12, raw_text: "third raw", suggested_text: "Third" },
      ]))
      .mockResolvedValueOnce(new Response(
        JSON.stringify({ detail: "Could not accept selected Review Items; no changes were made" }),
        { status: 500, headers: { "Content-Type": "application/json" } },
      ));
    stubFetch(fetchMock);
    vi.stubGlobal("confirm", vi.fn().mockReturnValue(true));
    startApp();

    await vi.waitFor(() => expect(document.querySelectorAll("tbody tr")).toHaveLength(2));
    const rowCheckboxes = [...document.querySelectorAll<HTMLInputElement>(
      'tbody input[type="checkbox"]',
    )];
    rowCheckboxes[1].click();
    document.querySelector<HTMLButtonElement>("#accept-selected")!.click();
    await vi.waitFor(() => expect(document.querySelector("[role=alert]")).not.toBeNull());

    expect(document.querySelectorAll("tbody tr")).toHaveLength(2);
    expect(rowCheckboxes.map((checkbox) => checkbox.checked)).toEqual([false, true]);
    expect(document.querySelector<HTMLButtonElement>("#accept-selected")!.textContent)
      .toBe("Accept selected (1)");
    expect(document.querySelector<HTMLButtonElement>("#accept-selected")!.disabled).toBe(false);
    expect(document.querySelector("[role=alert]")?.textContent).toContain("no changes were made");
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  test("stale bulk acceptance refreshes the queue and drops the obsolete selection", async () => {
    const item = { id: 4, raw_text: "first raw", suggested_text: "First" };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(okJson(projectInfo))
      .mockResolvedValueOnce(okJson([item]))
      .mockResolvedValueOnce(new Response(
        JSON.stringify({ detail: "Review Item 4 is no longer pending" }),
        { status: 409, headers: { "Content-Type": "application/json" } },
      ))
      .mockResolvedValueOnce(okJson({ ...projectInfo, review_items: 0 }))
      .mockResolvedValueOnce(okJson([]));
    stubFetch(fetchMock);
    vi.stubGlobal("confirm", vi.fn().mockReturnValue(true));
    startApp();

    await vi.waitFor(() => expect(document.querySelectorAll("tbody tr")).toHaveLength(1));
    document.querySelector<HTMLInputElement>('tbody input[type="checkbox"]')!.click();
    document.querySelector<HTMLButtonElement>("#accept-selected")!.click();

    await vi.waitFor(() => expect(document.querySelector(".empty-state")).not.toBeNull());
    expect(document.querySelector("[role=alert]")?.textContent).toContain("no longer pending");
    expect(fetchMock).toHaveBeenNthCalledWith(4, "/project/info");
    expect(fetchMock).toHaveBeenNthCalledWith(5, "/review-items");
  });

  test("Edit opens one prefilled inline input and Escape or Cancel restores the row", async () => {
    stubFetch(vi.fn()
      .mockResolvedValueOnce(okJson(projectInfo))
      .mockResolvedValueOnce(okJson([
        { id: 4, raw_text: "first raw", suggested_text: "First" },
        { id: 9, raw_text: "second raw", suggested_text: "Second" },
      ])));
    startApp();

    await vi.waitFor(() => expect(document.querySelectorAll("tbody tr")).toHaveLength(2));
    const editButtons = [...document.querySelectorAll<HTMLButtonElement>("tbody button")]
      .filter((button) => button.textContent === "Edit");
    editButtons[0].click();

    const input = document.querySelector<HTMLInputElement>('tbody input[type="text"]')!;
    expect(input.value).toBe("First");
    expect(document.querySelectorAll('tbody input[type="text"]')).toHaveLength(1);
    expect(editButtons[1].disabled).toBe(true);
    input.value = "Changed";
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    expect(document.querySelector('tbody input[type="text"]')).toBeNull();
    expect(document.querySelectorAll("tbody tr")[0].textContent).toContain("First");

    [...document.querySelectorAll<HTMLButtonElement>("tbody button")]
      .find((button) => button.textContent === "Edit")!.click();
    [...document.querySelectorAll<HTMLButtonElement>("tbody button")]
      .find((button) => button.textContent === "Cancel")!.click();
    expect(document.querySelector('tbody input[type="text"]')).toBeNull();
  });

  test("blank edited text is rejected inline without sending or losing the edit", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(okJson(projectInfo))
      .mockResolvedValueOnce(okJson([
        { id: 9, raw_text: "no suggestion", suggested_text: "" },
      ]));
    stubFetch(fetchMock);
    startApp();

    await vi.waitFor(() => expect(document.querySelector("tbody")).not.toBeNull());
    const buttons = [...document.querySelectorAll<HTMLButtonElement>("tbody button")];
    expect(buttons.find((button) => button.textContent === "Accept")!.disabled).toBe(true);
    buttons.find((button) => button.textContent === "Edit")!.click();
    const input = document.querySelector<HTMLInputElement>('tbody input[type="text"]')!;
    input.value = "   ";
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));

    expect(document.querySelector("[role=alert]")?.textContent).toContain("must not be blank");
    expect(input.value).toBe("   ");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  test("Enter completes a Review Item without a Suggestion and refreshes counts", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(okJson(projectInfo))
      .mockResolvedValueOnce(okJson([
        { id: 9, raw_text: "no suggestion", suggested_text: "" },
      ]))
      .mockResolvedValueOnce(okJson({ status: "accepted" }))
      .mockResolvedValueOnce(okJson({ ...projectInfo, mappings: 13, review_items: 3 }))
      .mockResolvedValueOnce(okJson([]));
    stubFetch(fetchMock);
    startApp();

    await vi.waitFor(() => expect(document.querySelector("tbody")).not.toBeNull());
    [...document.querySelectorAll<HTMLButtonElement>("tbody button")]
      .find((button) => button.textContent === "Edit")!.click();
    const input = document.querySelector<HTMLInputElement>('tbody input[type="text"]')!;
    input.value = "  Completed text  ";
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    await vi.waitFor(() => expect(document.querySelector(".empty-state")).not.toBeNull());

    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      "/review-items/9/accept",
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ normalized_text: "  Completed text  " }),
      },
    );
    expect(document.querySelector("[role=status]")?.textContent).toContain("Review Item 9 accepted.");
    expect(document.querySelector("header")?.textContent).toContain("13 Mappings");
    expect(document.querySelector("header")?.textContent).toContain("3 pending Review Items");
  });

  test("a failed Save and Accept preserves the entered text and editing state", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(okJson(projectInfo))
      .mockResolvedValueOnce(okJson([
        { id: 4, raw_text: "first raw", suggested_text: "First" },
      ]))
      .mockResolvedValueOnce(new Response(
        JSON.stringify({ detail: "Mapping could not be saved" }),
        { status: 500, headers: { "Content-Type": "application/json" } },
      ));
    stubFetch(fetchMock);
    startApp();

    await vi.waitFor(() => expect(document.querySelector("tbody")).not.toBeNull());
    [...document.querySelectorAll<HTMLButtonElement>("tbody button")]
      .find((button) => button.textContent === "Edit")!.click();
    const input = document.querySelector<HTMLInputElement>('tbody input[type="text"]')!;
    input.value = "My corrected value";
    [...document.querySelectorAll<HTMLButtonElement>("tbody button")]
      .find((button) => button.textContent === "Save and Accept")!.click();
    await vi.waitFor(() => expect(document.querySelector("[role=alert]")?.textContent).toContain("could not be saved"));

    expect(document.querySelectorAll("tbody tr")).toHaveLength(1);
    expect(document.querySelector<HTMLInputElement>('tbody input[type="text"]')!.value).toBe("My corrected value");
    expect([...document.querySelectorAll<HTMLButtonElement>("tbody button")]
      .find((button) => button.textContent === "Save and Accept")!.disabled).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  test("a stale Save and Accept refreshes the queue instead of restoring the obsolete row", async () => {
    const item = { id: 4, raw_text: "first raw", suggested_text: "First" };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(okJson(projectInfo))
      .mockResolvedValueOnce(okJson([item]))
      .mockResolvedValueOnce(new Response(
        JSON.stringify({ detail: "Review Item 4 is no longer pending" }),
        { status: 409, headers: { "Content-Type": "application/json" } },
      ))
      .mockResolvedValueOnce(okJson({ ...projectInfo, review_items: 3 }))
      .mockResolvedValueOnce(okJson([]));
    stubFetch(fetchMock);
    startApp();

    await vi.waitFor(() => expect(document.querySelector("tbody")).not.toBeNull());
    [...document.querySelectorAll<HTMLButtonElement>("tbody button")]
      .find((button) => button.textContent === "Edit")!.click();
    [...document.querySelectorAll<HTMLButtonElement>("tbody button")]
      .find((button) => button.textContent === "Save and Accept")!.click();

    await vi.waitFor(() => expect(document.querySelector(".empty-state")).not.toBeNull());
    expect(document.querySelector("tbody input[type=text]")).toBeNull();
    expect(fetchMock).toHaveBeenNthCalledWith(4, "/project/info");
    expect(fetchMock).toHaveBeenNthCalledWith(5, "/review-items");
  });

  test("accepting a suggested item removes it, updates counts, and refreshes server state", async () => {
    const remaining = [{ id: 9, raw_text: "second raw", suggested_text: "Second" }];
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(okJson(projectInfo))
      .mockResolvedValueOnce(okJson([
        { id: 4, raw_text: "first raw", suggested_text: "First" },
        ...remaining,
      ]))
      .mockResolvedValueOnce(okJson({ status: "accepted" }))
      .mockResolvedValueOnce(okJson({ ...projectInfo, mappings: 13, review_items: 3 }))
      .mockResolvedValueOnce(okJson(remaining));
    stubFetch(fetchMock);
    startApp();

    await vi.waitFor(() => expect(document.querySelectorAll("tbody tr")).toHaveLength(2));
    document.querySelector<HTMLButtonElement>("tbody button")!.click();
    await vi.waitFor(() => expect(document.querySelectorAll("tbody tr")).toHaveLength(1));

    expect(fetchMock).toHaveBeenNthCalledWith(3, "/review-items/4/accept", {
      method: "POST",
    });
    expect(document.querySelector("[role=status]")?.textContent).toContain("Review Item 4 accepted");
    expect(document.querySelector("header")?.textContent).toContain("13 Mappings");
    expect(document.querySelector("header")?.textContent).toContain("3 pending Review Items");
    expect(fetchMock).toHaveBeenNthCalledWith(4, "/project/info");
    expect(fetchMock).toHaveBeenNthCalledWith(5, "/review-items");
  });

  test("manual Refresh reloads the queue and counts", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(okJson(projectInfo))
      .mockResolvedValueOnce(okJson([]))
      .mockResolvedValueOnce(okJson({ ...projectInfo, review_items: 1 }))
      .mockResolvedValueOnce(okJson([
        { id: 12, raw_text: "new raw", suggested_text: "New" },
      ]));
    stubFetch(fetchMock);
    startApp();

    await vi.waitFor(() => expect(document.querySelector(".empty-state")).not.toBeNull());
    document.querySelector<HTMLButtonElement>("#refresh-review-items")!.click();
    await vi.waitFor(() => expect(document.querySelector("tbody")?.textContent).toContain("new raw"));

    expect(document.querySelector("header")?.textContent).toContain("1 pending Review Items");
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  test("manual Refresh reports a Project failure and still reloads the queue", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(okJson(projectInfo))
      .mockResolvedValueOnce(okJson([]))
      .mockResolvedValueOnce(new Response(
        JSON.stringify({ detail: "Project statistics are unavailable" }),
        { status: 503, headers: { "Content-Type": "application/json" } },
      ))
      .mockResolvedValueOnce(okJson([
        { id: 12, raw_text: "new raw", suggested_text: "New" },
      ]));
    stubFetch(fetchMock);
    startApp();

    await vi.waitFor(() => expect(document.querySelector(".empty-state")).not.toBeNull());
    document.querySelector<HTMLButtonElement>("#refresh-review-items")!.click();

    await vi.waitFor(() => expect(document.querySelector("tbody")?.textContent).toContain("new raw"));
    expect(document.querySelector("[role=alert]")?.textContent)
      .toContain("Project statistics are unavailable");
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  test("returning focus to the tab refreshes without timed polling", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(okJson(projectInfo))
      .mockResolvedValueOnce(okJson([]))
      .mockResolvedValueOnce(okJson(projectInfo))
      .mockResolvedValueOnce(okJson([]));
    stubFetch(fetchMock);
    startApp();

    await vi.runAllTimersAsync();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    window.dispatchEvent(new Event("focus"));
    await vi.runAllTimersAsync();

    expect(fetchMock).toHaveBeenCalledTimes(4);
    vi.useRealTimers();
  });

  test("a failed acceptance preserves the row and explains the error", async () => {
    const item = { id: 4, raw_text: "first raw", suggested_text: "First" };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(okJson(projectInfo))
      .mockResolvedValueOnce(okJson([item]))
      .mockResolvedValueOnce(new Response(
        JSON.stringify({ detail: "Mapping could not be saved" }),
        { status: 500, headers: { "Content-Type": "application/json" } },
      ));
    stubFetch(fetchMock);
    startApp();

    await vi.waitFor(() => expect(document.querySelector("tbody button")).not.toBeNull());
    document.querySelector<HTMLButtonElement>("tbody button")!.click();
    await vi.waitFor(() => expect(document.querySelector("[role=alert]")?.textContent).toContain("could not be saved"));

    expect(document.querySelectorAll("tbody tr")).toHaveLength(1);
    expect(document.querySelector<HTMLButtonElement>("tbody button")!.disabled).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  test("a stale-item conflict explains the conflict and refreshes the queue", async () => {
    const item = { id: 4, raw_text: "first raw", suggested_text: "First" };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(okJson(projectInfo))
      .mockResolvedValueOnce(okJson([item]))
      .mockResolvedValueOnce(new Response(
        JSON.stringify({ detail: "Review Item with id 4 not found" }),
        { status: 409, headers: { "Content-Type": "application/json" } },
      ))
      .mockResolvedValueOnce(okJson({ ...projectInfo, review_items: 3 }))
      .mockResolvedValueOnce(okJson([]));
    stubFetch(fetchMock);
    startApp();

    await vi.waitFor(() => expect(document.querySelector("tbody button")).not.toBeNull());
    document.querySelector<HTMLButtonElement>("tbody button")!.click();
    await vi.waitFor(() => expect(document.querySelector(".empty-state")).not.toBeNull());

    expect(document.querySelector("[role=alert]")?.textContent).toContain("not found");
    expect(fetchMock).toHaveBeenCalledTimes(5);
  });

  test("a queue request error replaces loading with an accessible message", async () => {
    stubFetch(vi.fn()
      .mockResolvedValueOnce(okJson(projectInfo))
      .mockResolvedValueOnce(new Response(
        JSON.stringify({ detail: "Database is unavailable" }),
        { status: 503, headers: { "Content-Type": "application/json" } },
      )));
    startApp();

    await vi.waitFor(() => expect(document.querySelector("#review-queue [role=alert]")?.textContent).toContain("unavailable"));

    expect(document.querySelector("#review-queue [role=status]")).toBeNull();
    expect(document.querySelector("table")).toBeNull();
  });
});
