/** Send a captured route to Suunto and recover durable local export operations. */
import type { Candidate } from "./api-types";
import { element } from "./dom.ts";
import { routeGpx } from "./gpx.ts";
import { candidateName } from "./run-results.ts";
import { loadExportProfile } from "./route-export.ts";

interface Operation {
  id: string;
  name: string;
  use_in_watch: boolean;
  state: string;
  error: string | null;
}
const endpoint = "/api/integrations/suunto";
const storageKey = "runmap.suunto.operation";

async function request<T>(path: string, body?: object): Promise<T> {
  const response = await fetch(
    `${endpoint}${path}`,
    body
      ? {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-Runmap-Action": "suunto" },
          body: JSON.stringify(body),
        }
      : undefined,
  );
  if (!response.ok) {
    const data = await response.json();
    throw new Error(typeof data.detail === "string" ? data.detail : "Suunto request failed.");
  }
  return response.json() as Promise<T>;
}

export function operationMessage(operation: Operation): string {
  switch (operation.state) {
    case "running":
      return "Export in progress. Check status shortly.";
    case "selected":
      return "Selected for watch sync. Sync your Suunto app.";
    case "created":
      return "Saved in Suunto. You can select it for watch sync.";
    case "selection-pending":
      return "Saved in Suunto; watch selection is unconfirmed. Retry watch selection.";
    case "post-pending":
    case "created-unverified":
      return "Creation is unconfirmed. Check your Suunto library before starting another export.";
    default:
      return operation.error === "authentication"
        ? "Suunto login failed. Check the local importer credentials and connection."
        : operation.error === "validation"
          ? "Unsupported GPX. Export requires 2–10,000 points and at most 2 MiB."
          : "Export did not complete. Check the local importer installation and connection.";
  }
}

function remember(id: string | null): void {
  try {
    if (id) localStorage.setItem(storageKey, id);
    else localStorage.removeItem(storageKey);
  } catch {
    /* Recovery remains available during this page session. */
  }
}

export function startSuuntoExport(datasetId: string, selected: () => Candidate | undefined): void {
  const button = element<HTMLButtonElement>("run-suunto");
  const dialog = element<HTMLDialogElement>("suunto-dialog");
  const name = element<HTMLInputElement>("suunto-name");
  const watch = element<HTMLInputElement>("suunto-watch");
  const send = element<HTMLButtonElement>("suunto-send");
  const refresh = element<HTMLButtonElement>("suunto-refresh");
  const retry = element<HTMLButtonElement>("suunto-select");
  const fresh = element<HTMLButtonElement>("suunto-new");
  const status = element("suunto-status");
  let candidate: Candidate | undefined;
  let operation: Operation | undefined;
  let id: string | null = null;
  let busy = false;
  try {
    id = localStorage.getItem(storageKey);
  } catch {
    /* Optional persistence. */
  }

  function render(): void {
    send.disabled = busy || Boolean(id) || !candidate || !name.value.trim();
    name.disabled = busy || Boolean(id);
    watch.disabled = busy || Boolean(id);
    refresh.hidden = !id;
    refresh.disabled = busy;
    retry.hidden = !operation || !["created", "selection-pending"].includes(operation.state);
    retry.disabled = busy;
    fresh.hidden = !id;
    fresh.disabled = busy || operation?.state === "running";
    dialog.setAttribute("aria-busy", String(busy));
  }

  async function perform(action: () => Promise<void>): Promise<void> {
    busy = true;
    render();
    try {
      await action();
    } catch (error) {
      status.textContent = `${(error as Error).message}${id ? " Check status before starting another export." : ""}`;
    } finally {
      busy = false;
      render();
    }
  }

  async function check(): Promise<void> {
    if (!id) return;
    operation = await request<Operation>(`/imports/${id}`);
    name.value = operation.name;
    watch.checked = operation.use_in_watch;
    status.textContent = operationMessage(operation);
  }

  void request<{ configured: boolean }>("")
    .then((config) => {
      button.hidden = !config.configured;
    })
    .catch(() => {
      button.hidden = true;
    });
  button.addEventListener("click", () => {
    if (busy) {
      dialog.showModal();
      return;
    }
    candidate = selected();
    if (!candidate) return;
    if (!id)
      name.value = `Runmap · ${candidateName(candidate)} · ${(candidate.distance_m / 1000).toFixed(2)} km`;
    dialog.showModal();
    render();
    if (id) void perform(check);
  });
  name.addEventListener("input", render);
  element("suunto-close").addEventListener("click", () => dialog.close());
  refresh.addEventListener("click", () => {
    void perform(check);
  });
  fresh.addEventListener("click", () => {
    id = null;
    operation = undefined;
    remember(null);
    if (candidate)
      name.value = `Runmap · ${candidateName(candidate)} · ${(candidate.distance_m / 1000).toFixed(2)} km`;
    watch.checked = true;
    status.textContent = "";
    render();
  });
  retry.addEventListener("click", () => {
    void perform(async () => {
      operation = await request<Operation>(`/imports/${id}/select`, {});
      status.textContent = operationMessage(operation);
    });
  });
  send.addEventListener("click", () => {
    const captured = candidate;
    const routeName = name.value.trim();
    const useInWatch = watch.checked;
    if (!captured || id || busy) return;
    void perform(async () => {
      status.textContent = "Loading elevation…";
      const profile = await loadExportProfile(datasetId, captured);
      const gpx = routeGpx(captured, profile);
      if (
        new TextEncoder().encode(gpx).length > 2 * 1024 * 1024 ||
        (gpx.match(/<trkpt /g)?.length ?? 0) > 10000
      ) {
        throw new Error("Route exceeds the Suunto import limits (10,000 points or 2 MiB).");
      }
      id = crypto.randomUUID();
      remember(id);
      status.textContent = "Sending to Suunto…";
      operation = await request<Operation>("/imports", {
        operation_id: id,
        name: routeName,
        gpx,
        use_in_watch: useInWatch,
      });
      status.textContent = operationMessage(operation);
    });
  });
}
