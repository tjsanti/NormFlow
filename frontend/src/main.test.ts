import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { startApp } from "./main";

const projectInfo = {
  workspace: "/Users/example/projects/customer-names",
  database: "/Users/example/projects/customer-names/normflow.db",
  mappings: 12,
  review_items: 4,
};

function okJson(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("Project selection", () => {
  beforeEach(() => {
    document.body.innerHTML = '<div id="app"></div>';
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  test("first run opens a valid Project and remembers its canonical path", async () => {
    const fetchMock = vi.fn().mockResolvedValue(okJson(projectInfo));
    vi.stubGlobal("fetch", fetchMock);
    startApp();

    const input = document.querySelector<HTMLInputElement>("#project-path")!;
    input.value = "~/projects/customer-names";
    document.querySelector<HTMLFormElement>("form")!.requestSubmit();
    await vi.waitFor(() => expect(document.querySelector("header")).not.toBeNull());

    expect(fetchMock).toHaveBeenCalledWith("/workspace/info", {
      headers: { "X-Normflow-Workspace": "~/projects/customer-names" },
    });
    expect(document.body.textContent).toContain("customer-names");
    expect(document.body.textContent).toContain("12 Mappings");
    expect(document.body.textContent).toContain("4 pending Review Items");
    expect(JSON.parse(window.localStorage.getItem("normflow.recentProjects")!)).toEqual([
      projectInfo.workspace,
    ]);
  });

  test("first run displays the API's actionable validation error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ detail: "Not a NormFlow workspace: no database found at /tmp/empty/normflow.db" }),
      { status: 422, headers: { "Content-Type": "application/json" } },
    )));
    startApp();

    document.querySelector<HTMLInputElement>("#project-path")!.value = "/tmp/empty";
    document.querySelector<HTMLFormElement>("form")!.requestSubmit();
    await vi.waitFor(() => expect(document.querySelector("[role=alert]")).not.toBeNull());

    expect(document.querySelector("[role=alert]")!.textContent).toContain("no database found");
    expect(document.querySelector("form")).not.toBeNull();
    expect(window.localStorage.getItem("normflow.recentProjects")).toBeNull();
  });

  test("the most recent valid Project reopens automatically", async () => {
    window.localStorage.setItem("normflow.recentProjects", JSON.stringify([
      projectInfo.workspace,
      "/Users/example/projects/older-project",
    ]));
    const fetchMock = vi.fn().mockResolvedValue(okJson(projectInfo));
    vi.stubGlobal("fetch", fetchMock);

    startApp();
    await vi.waitFor(() => expect(document.querySelector("header")?.textContent).toContain("customer-names"));

    expect(fetchMock).toHaveBeenCalledWith("/workspace/info", {
      headers: { "X-Normflow-Workspace": projectInfo.workspace },
    });
    expect(document.querySelector("form")).toBeNull();
  });

  test("automatic reopening skips a recent path that is no longer a valid Project", async () => {
    const missing = "/Users/example/projects/moved-project";
    window.localStorage.setItem("normflow.recentProjects", JSON.stringify([
      missing,
      projectInfo.workspace,
    ]));
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(
        JSON.stringify({ detail: "Not a NormFlow workspace: no database found" }),
        { status: 422, headers: { "Content-Type": "application/json" } },
      ))
      .mockResolvedValueOnce(okJson(projectInfo))
      .mockResolvedValueOnce(okJson([]));
    vi.stubGlobal("fetch", fetchMock);

    startApp();
    await vi.waitFor(() => expect(document.querySelector("header")?.textContent).toContain("customer-names"));

    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(JSON.parse(window.localStorage.getItem("normflow.recentProjects")!)).toEqual([
      projectInfo.workspace,
    ]);
  });

  test("users can switch to another recent Project", async () => {
    const olderInfo = {
      ...projectInfo,
      workspace: "/Users/example/projects/older-project",
      database: "/Users/example/projects/older-project/normflow.db",
      mappings: 3,
      review_items: 1,
    };
    window.localStorage.setItem("normflow.recentProjects", JSON.stringify([
      projectInfo.workspace,
      olderInfo.workspace,
    ]));
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(okJson(projectInfo))
      .mockResolvedValueOnce(okJson([]))
      .mockResolvedValueOnce(okJson(olderInfo))
      .mockResolvedValueOnce(okJson([]));
    vi.stubGlobal("fetch", fetchMock);
    startApp();
    await vi.waitFor(() => expect(document.querySelector("header")?.textContent).toContain("customer-names"));

    const switchButton = [...document.querySelectorAll("button")]
      .find((button) => button.textContent === "Switch Project")!;
    switchButton.click();
    expect(document.body.textContent).toContain("Recent Projects");
    const olderButton = [...document.querySelectorAll("button")]
      .find((button) => button.textContent?.includes("older-project"))!;
    olderButton.click();
    await vi.waitFor(() => expect(document.querySelector("header")?.textContent).toContain("older-project"));

    expect(document.querySelector("header")?.textContent).toContain("3 Mappings");
    expect(document.querySelector("header")?.textContent).toContain("1 pending Review Item");
    expect(JSON.parse(window.localStorage.getItem("normflow.recentProjects")!)).toEqual([
      olderInfo.workspace,
      projectInfo.workspace,
    ]);
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
    vi.stubGlobal("fetch", fetchMock);
    startApp();

    document.querySelector<HTMLInputElement>("#project-path")!.value = projectInfo.workspace;
    document.querySelector<HTMLFormElement>("form")!.requestSubmit();
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
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/review-items", {
      headers: { "X-Normflow-Workspace": projectInfo.workspace },
    });
  });

  test("only eligible rows can be selected and select-all covers the complete loaded queue", async () => {
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(okJson(projectInfo))
      .mockResolvedValueOnce(okJson([
        { id: 4, raw_text: "first raw", suggested_text: "First" },
        { id: 9, raw_text: "no suggestion", suggested_text: "  " },
        { id: 12, raw_text: "third raw", suggested_text: "Third" },
      ])));
    startApp();

    document.querySelector<HTMLInputElement>("#project-path")!.value = projectInfo.workspace;
    document.querySelector<HTMLFormElement>("form")!.requestSubmit();
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
    vi.stubGlobal("fetch", fetchMock);
    const confirmMock = vi.fn().mockReturnValue(true);
    vi.stubGlobal("confirm", confirmMock);
    startApp();

    document.querySelector<HTMLInputElement>("#project-path")!.value = projectInfo.workspace;
    document.querySelector<HTMLFormElement>("form")!.requestSubmit();
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
        "X-Normflow-Workspace": projectInfo.workspace,
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
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("confirm", vi.fn().mockReturnValue(true));
    startApp();

    document.querySelector<HTMLInputElement>("#project-path")!.value = projectInfo.workspace;
    document.querySelector<HTMLFormElement>("form")!.requestSubmit();
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

  test("Edit opens one prefilled inline input and Escape or Cancel restores the row", async () => {
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(okJson(projectInfo))
      .mockResolvedValueOnce(okJson([
        { id: 4, raw_text: "first raw", suggested_text: "First" },
        { id: 9, raw_text: "second raw", suggested_text: "Second" },
      ])));
    startApp();

    document.querySelector<HTMLInputElement>("#project-path")!.value = projectInfo.workspace;
    document.querySelector<HTMLFormElement>("form")!.requestSubmit();
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
    vi.stubGlobal("fetch", fetchMock);
    startApp();

    document.querySelector<HTMLInputElement>("#project-path")!.value = projectInfo.workspace;
    document.querySelector<HTMLFormElement>("form")!.requestSubmit();
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
    vi.stubGlobal("fetch", fetchMock);
    startApp();

    document.querySelector<HTMLInputElement>("#project-path")!.value = projectInfo.workspace;
    document.querySelector<HTMLFormElement>("form")!.requestSubmit();
    await vi.waitFor(() => expect(document.querySelector("tbody")).not.toBeNull());
    [...document.querySelectorAll<HTMLButtonElement>("tbody button")]
      .find((button) => button.textContent === "Edit")!.click();
    const input = document.querySelector<HTMLInputElement>('tbody input[type="text"]')!;
    input.value = "  Completed text  ";
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    await vi.waitFor(() => expect(document.querySelector(".empty-state")).not.toBeNull());

    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      "/review-items/9/edit-and-accept?normalized_text=%20%20Completed%20text%20%20",
      { method: "POST", headers: { "X-Normflow-Workspace": projectInfo.workspace } },
    );
    expect(document.querySelector("[role=status]")?.textContent).toContain("Review Item 9 accepted with edit");
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
    vi.stubGlobal("fetch", fetchMock);
    startApp();

    document.querySelector<HTMLInputElement>("#project-path")!.value = projectInfo.workspace;
    document.querySelector<HTMLFormElement>("form")!.requestSubmit();
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
    vi.stubGlobal("fetch", fetchMock);
    startApp();

    document.querySelector<HTMLInputElement>("#project-path")!.value = projectInfo.workspace;
    document.querySelector<HTMLFormElement>("form")!.requestSubmit();
    await vi.waitFor(() => expect(document.querySelectorAll("tbody tr")).toHaveLength(2));
    document.querySelector<HTMLButtonElement>("tbody button")!.click();
    await vi.waitFor(() => expect(document.querySelectorAll("tbody tr")).toHaveLength(1));

    expect(fetchMock).toHaveBeenNthCalledWith(3, "/review-items/4/accept", {
      method: "POST",
      headers: { "X-Normflow-Workspace": projectInfo.workspace },
    });
    expect(document.querySelector("[role=status]")?.textContent).toContain("Review Item 4 accepted");
    expect(document.querySelector("header")?.textContent).toContain("13 Mappings");
    expect(document.querySelector("header")?.textContent).toContain("3 pending Review Items");
    expect(fetchMock).toHaveBeenNthCalledWith(4, "/workspace/info", {
      headers: { "X-Normflow-Workspace": projectInfo.workspace },
    });
    expect(fetchMock).toHaveBeenNthCalledWith(5, "/review-items", {
      headers: { "X-Normflow-Workspace": projectInfo.workspace },
    });
  });

  test("manual Refresh reloads the queue and counts", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(okJson(projectInfo))
      .mockResolvedValueOnce(okJson([]))
      .mockResolvedValueOnce(okJson({ ...projectInfo, review_items: 1 }))
      .mockResolvedValueOnce(okJson([
        { id: 12, raw_text: "new raw", suggested_text: "New" },
      ]));
    vi.stubGlobal("fetch", fetchMock);
    startApp();

    document.querySelector<HTMLInputElement>("#project-path")!.value = projectInfo.workspace;
    document.querySelector<HTMLFormElement>("form")!.requestSubmit();
    await vi.waitFor(() => expect(document.querySelector(".empty-state")).not.toBeNull());
    document.querySelector<HTMLButtonElement>("#refresh-review-items")!.click();
    await vi.waitFor(() => expect(document.querySelector("tbody")?.textContent).toContain("new raw"));

    expect(document.querySelector("header")?.textContent).toContain("1 pending Review Items");
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  test("returning focus to the tab refreshes without timed polling", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(okJson(projectInfo))
      .mockResolvedValueOnce(okJson([]))
      .mockResolvedValueOnce(okJson(projectInfo))
      .mockResolvedValueOnce(okJson([]));
    vi.stubGlobal("fetch", fetchMock);
    startApp();

    document.querySelector<HTMLInputElement>("#project-path")!.value = projectInfo.workspace;
    document.querySelector<HTMLFormElement>("form")!.requestSubmit();
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
    vi.stubGlobal("fetch", fetchMock);
    startApp();

    document.querySelector<HTMLInputElement>("#project-path")!.value = projectInfo.workspace;
    document.querySelector<HTMLFormElement>("form")!.requestSubmit();
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
    vi.stubGlobal("fetch", fetchMock);
    startApp();

    document.querySelector<HTMLInputElement>("#project-path")!.value = projectInfo.workspace;
    document.querySelector<HTMLFormElement>("form")!.requestSubmit();
    await vi.waitFor(() => expect(document.querySelector("tbody button")).not.toBeNull());
    document.querySelector<HTMLButtonElement>("tbody button")!.click();
    await vi.waitFor(() => expect(document.querySelector(".empty-state")).not.toBeNull());

    expect(document.querySelector("[role=alert]")?.textContent).toContain("not found");
    expect(fetchMock).toHaveBeenCalledTimes(5);
  });

  test("a queue request error replaces loading with an accessible message", async () => {
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(okJson(projectInfo))
      .mockResolvedValueOnce(new Response(
        JSON.stringify({ detail: "Database is unavailable" }),
        { status: 503, headers: { "Content-Type": "application/json" } },
      )));
    startApp();

    document.querySelector<HTMLInputElement>("#project-path")!.value = projectInfo.workspace;
    document.querySelector<HTMLFormElement>("form")!.requestSubmit();
    await vi.waitFor(() => expect(document.querySelector("#review-queue [role=alert]")?.textContent).toContain("unavailable"));

    expect(document.querySelector("#review-queue [role=status]")).toBeNull();
    expect(document.querySelector("table")).toBeNull();
  });
});
