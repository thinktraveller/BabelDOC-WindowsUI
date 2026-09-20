import type {
  ApiProfile,
  GlossaryEntryInfo,
  GlossaryInfo,
  GlossaryVersionInfo,
  ConnectionTestResult,
  ParamsSchema,
  Preset,
  StagedFile,
  TaskInfo,
  ValidationResponse,
} from "./types";
import { describeErrorDetail } from "./errors";

export const TOKEN_HEADER = "X-Workbench-Token";

/** 会话令牌：生产环境由窗口注入内存，开发环境用 VITE_WORKBENCH_TOKEN。 */
export function sessionToken(): string {
  const injected = (window as unknown as { __WORKBENCH_TOKEN__?: string })
    .__WORKBENCH_TOKEN__;
  if (injected) {
    return injected;
  }
  return (import.meta.env.VITE_WORKBENCH_TOKEN as string | undefined) ?? "";
}

/**
 * 等待窗口注入会话令牌。
 *
 * 页面可能在 pywebview 完成注入之前就开始请求，此时请求会被后端以 403
 * 拒绝，界面看起来像一直卡在加载中。开发模式（Vite）由 VITE_WORKBENCH_TOKEN
 * 提供令牌，不进入等待。
 */
let tokenWaitFailed = false;

async function waitForToken(timeoutMs = 20000): Promise<string> {
  const devToken = (import.meta.env.VITE_WORKBENCH_TOKEN as string | undefined) ?? "";
  if (devToken) {
    return devToken;
  }
  const existing = sessionToken();
  if (existing || tokenWaitFailed) {
    return existing;
  }
  const inDesktopHost = (): boolean =>
    typeof (window as { pywebview?: unknown }).pywebview !== "undefined";
  const startedAt = Date.now();
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const token = sessionToken();
    if (token) {
      return token;
    }
    // 普通浏览器里没有 pywebview，也没有注入通道：短暂等待后立即放弃，
    // 让界面尽快显示"未授权"而不是干等。
    if (Date.now() - startedAt > 3000 && !inDesktopHost()) {
      return "";
    }
    if (Date.now() >= deadline) {
      tokenWaitFailed = true;
      return "";
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
}

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(message: string, status: number, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = await waitForToken();
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      headers: {
        [TOKEN_HEADER]: token,
        ...(init.headers ?? {}),
      },
    });
  } catch (error) {
    throw new ApiError(
      "无法连接本地服务：请确认 BabelDOC 工作台正在运行，然后重试。",
      0,
      error,
    );
  }
  if (!response.ok) {
    let detail: unknown = undefined;
    try {
      detail = await response.json();
    } catch {
      detail = await response.text().catch(() => "");
    }
    throw new ApiError(messageFromDetail(detail, response.status), response.status, detail);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

const messageFromDetail = describeErrorDetail;

export interface ImportProgress {
  loaded: number;
  total: number;
}

export const api = {
  health: () => request<Record<string, unknown>>("/api/health"),

  listFiles: () => request<{ items: StagedFile[] }>("/api/files"),
  deleteFile: (fileId: string) =>
    request<void>(`/api/files/${fileId}`, { method: "DELETE" }),

  /** 上传走 XMLHttpRequest：需要上传进度，fetch 目前拿不到。 */
  importFiles: (
    files: File[],
    onProgress?: (progress: ImportProgress) => void,
    signal?: AbortSignal,
  ) =>
    new Promise<StagedFile[]>((resolve, reject) => {
      void (async () => {
        // 与其他接口一致：先等窗口注入会话令牌，避免上传被 403 拒绝
        const token = await waitForToken();
        const form = new FormData();
        files.forEach((file) => form.append("files", file, file.name));
        const xhr = new XMLHttpRequest();
        xhr.open("POST", "/api/files/import");
        xhr.setRequestHeader(TOKEN_HEADER, token);
        xhr.upload.onprogress = (event) => {
          if (event.lengthComputable && onProgress) {
            onProgress({ loaded: event.loaded, total: event.total });
          }
        };
      xhr.onerror = () =>
        reject(new ApiError("上传失败：请确认本地服务仍在运行。", 0));
      xhr.onabort = () => reject(new ApiError("已取消上传。", 0));
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            const payload = JSON.parse(xhr.responseText) as { items: StagedFile[] };
            resolve(payload.items);
          } catch (error) {
            reject(new ApiError("解析上传结果失败。", xhr.status, error));
          }
          return;
        }
        let detail: unknown = xhr.responseText;
        try {
          detail = JSON.parse(xhr.responseText);
        } catch {
          /* 保持原始文本 */
        }
        reject(new ApiError(messageFromDetail(detail, xhr.status), xhr.status, detail));
      };
      signal?.addEventListener("abort", () => xhr.abort());
      xhr.send(form);
      })();
    }),

  paramsSchema: () => request<ParamsSchema>("/api/settings/params/schema"),
  validateParams: (params: Record<string, unknown>, pageCount?: number | null) =>
    request<ValidationResponse>("/api/settings/params/validate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ params, page_count: pageCount ?? null }),
    }),

  listProfiles: () =>
    request<{ items: ApiProfile[]; credentials_available: boolean }>(
      "/api/settings/api-profiles",
    ),
  createProfile: (payload: Record<string, unknown>) =>
    request<ApiProfile>("/api/settings/api-profiles", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  updateProfile: (id: number, payload: Record<string, unknown>) =>
    request<ApiProfile>(`/api/settings/api-profiles/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  deleteProfile: (id: number) =>
    request<void>(`/api/settings/api-profiles/${id}`, { method: "DELETE" }),
  setDefaultProfile: (id: number) =>
    request<ApiProfile>(`/api/settings/api-profiles/${id}/default`, { method: "POST" }),
  testProfile: (id: number) =>
    request<ConnectionTestResult>(`/api/settings/api-profiles/${id}/test`, {
      method: "POST",
    }),

  listPresets: () => request<{ items: Preset[] }>("/api/settings/presets"),
  savePreset: (name: string, params: Record<string, unknown>) =>
    request<Preset>("/api/settings/presets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, params }),
    }),
  deletePreset: (id: number) =>
    request<void>(`/api/settings/presets/${id}`, { method: "DELETE" }),

  listTasks: (limit = 100) => request<{ items: TaskInfo[] }>(`/api/tasks?limit=${limit}`),
  getTask: (id: number) => request<TaskInfo>(`/api/tasks/${id}`),
  createTasks: (payload: Record<string, unknown>) =>
    request<{ items: TaskInfo[] }>("/api/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  cancelTask: (id: number) =>
    request<Record<string, unknown>>(`/api/tasks/${id}/cancel`, { method: "POST" }),
  rerunTask: (id: number) =>
    request<TaskInfo>(`/api/tasks/${id}/rerun`, { method: "POST" }),
  deleteTask: (id: number, deleteFiles = false) =>
    request<void>(`/api/tasks/${id}?delete_files=${deleteFiles ? "true" : "false"}`, {
      method: "DELETE",
    }),
  taskFiles: (id: number) =>
    request<{
      task_id: number;
      root: string;
      total_size: number;
      items: { name: string; path: string; exists: boolean; size: number }[];
    }>(`/api/tasks/${id}/files`),
  cleanupTask: (id: number) =>
    request<{ removed: string[]; freed_bytes: number }>(`/api/tasks/${id}/cleanup`, {
      method: "POST",
    }),
  deleteTaskFiles: (id: number) =>
    request<{ removed: string[]; freed_bytes: number }>(`/api/tasks/${id}/files`, {
      method: "DELETE",
    }),
  revealTask: (id: number) =>
    request<{ path: string }>(`/api/tasks/${id}/reveal`, { method: "POST" }),
  openOutput: (id: number, kind: string) =>
    request<{ path: string }>(`/api/tasks/${id}/outputs/${kind}/open`, { method: "POST" }),
  saveOutputAs: (id: number, kind: string, targetDir: string) =>
    request<{ target: string; size: number }>(
      `/api/tasks/${id}/outputs/${kind}/save-as`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target_dir: targetDir }),
      },
    ),

  getAppSettings: () =>
    request<{ default_output_dir: string; retention_days: number; log_level: string }>(
      "/api/settings/app",
    ),
  updateAppSettings: (patch: Record<string, unknown>) =>
    request<{ default_output_dir: string; retention_days: number; log_level: string }>(
      "/api/settings/app",
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      },
    ),

  listGlossaries: () => request<{ items: GlossaryInfo[] }>("/api/glossaries"),
  importGlossaryFromTask: (taskId: number, name?: string) =>
    request<GlossaryInfo>("/api/glossaries/import-task", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task_id: taskId, name: name ?? null }),
    }),
  importGlossaryCsv: (file: File, name: string, tgtLng: string) => {
    const form = new FormData();
    form.append("file", file, file.name);
    form.append("name", name);
    form.append("tgt_lng", tgtLng);
    return request<GlossaryInfo>("/api/glossaries/import", { method: "POST", body: form });
  },
  listGlossaryEntries: (glossaryId: number, filters: Record<string, string | number>) => {
    const search = new URLSearchParams();
    Object.entries(filters).forEach(([key, value]) => {
      if (value !== "" && value !== undefined && value !== null) {
        search.set(key, String(value));
      }
    });
    return request<{ total: number; items: GlossaryEntryInfo[] }>(
      `/api/glossaries/${glossaryId}/entries?${search.toString()}`,
    );
  },
  updateGlossaryEntry: (entryId: number, patch: Record<string, unknown>) =>
    request<GlossaryEntryInfo>(`/api/glossaries/entries/${entryId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }),
  deleteGlossaryEntry: (entryId: number) =>
    request<void>(`/api/glossaries/entries/${entryId}`, { method: "DELETE" }),
  bulkGlossaryStatus: (glossaryId: number, entryIds: number[], status: string) =>
    request<{ updated: number }>(`/api/glossaries/${glossaryId}/entries/bulk-status`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ entry_ids: entryIds, status }),
    }),
  resolveGlossaryConflict: (glossaryId: number, entryId: number, keep: "existing" | "incoming") =>
    request<GlossaryEntryInfo>(`/api/glossaries/${glossaryId}/resolve-conflict`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ entry_id: entryId, keep }),
    }),
  createGlossaryVersion: (glossaryId: number, note?: string) =>
    request<GlossaryVersionInfo>(`/api/glossaries/${glossaryId}/versions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ note: note ?? null }),
    }),
  listGlossaryVersions: (glossaryId?: number, tgtLng?: string) => {
    const search = new URLSearchParams();
    if (glossaryId) {
      search.set("glossary_id", String(glossaryId));
    }
    if (tgtLng) {
      search.set("tgt_lng", tgtLng);
    }
    return request<{ items: GlossaryVersionInfo[] }>(
      `/api/glossaries/versions?${search.toString()}`,
    );
  },
  glossaryExportUrl: (glossaryId: number) => `/api/glossaries/${glossaryId}/export`,

  appStatus: () =>
    request<{
      active_count: number;
      active_tasks: { id: number; status: string; stage: string | null }[];
    }>("/api/app/status"),
  forceInterrupt: () =>
    request<{ interrupted: { task_id: number }[] }>("/api/app/force-interrupt", {
      method: "POST",
    }),
  exportDiagnostics: (destinationDir?: string | null) =>
    request<{
      path: string;
      size: number;
      entries: string[];
      findings: string[];
    }>("/api/app/diagnostics", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ destination_dir: destinationDir ?? null }),
    }),
  assetsUsage: () =>
    request<{
      path: string;
      total_bytes: number;
      groups: Record<string, { files: number; bytes: number }>;
    }>("/api/app/assets/usage"),
  downloadAssets: () =>
    request<{ ok: boolean; returncode: number; stderr?: string }>(
      "/api/app/assets/download",
      { method: "POST" },
    ),
  packAssets: (targetDir?: string | null) =>
    request<{ ok: boolean; stdout?: string; stderr?: string }>("/api/app/assets/pack", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target_dir: targetDir ?? null }),
    }),
  restoreAssets: (packagePath: string) =>
    request<{ ok: boolean; stdout?: string; stderr?: string }>("/api/app/assets/restore", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ package_path: packagePath }),
    }),
};

/** 选择目录：桌面窗口里用原生对话框，浏览器里退回手输路径。 */
export async function chooseDirectory(initial?: string | null): Promise<string | null> {
  const bridge = (
    window as unknown as {
      pywebview?: { api?: { choose_directory?: (value: string | null) => Promise<string | null> } };
    }
  ).pywebview?.api;
  if (bridge?.choose_directory) {
    try {
      return await bridge.choose_directory(initial ?? null);
    } catch {
      /* 对话框失败时退回手输 */
    }
  }
  const manual = window.prompt("请输入目录的完整路径：", initial ?? "");
  return manual && manual.trim() ? manual.trim() : null;
}

export interface TaskStreamHandlers {
  onEvent?: (event: { type: string; payload: Record<string, unknown> }) => void;
  onError?: (message: string) => void;
  signal?: AbortSignal;
}

/** 通过 fetch 读取 SSE（EventSource 不支持自定义请求头）。 */
export async function subscribeTaskEvents(
  taskId: number,
  handlers: TaskStreamHandlers,
): Promise<void> {
  let response: Response;
  try {
    response = await fetch(`/api/tasks/${taskId}/events`, {
      headers: { [TOKEN_HEADER]: sessionToken() },
      signal: handlers.signal,
    });
  } catch (error) {
    if (!handlers.signal?.aborted) {
      handlers.onError?.("事件流连接失败，请检查本地服务是否在运行。");
    }
    return;
  }
  if (!response.ok || !response.body) {
    handlers.onError?.(`事件流不可用（HTTP ${response.status}）`);
    return;
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() ?? "";
    for (const chunk of chunks) {
      const lines = chunk.split("\n");
      const eventLine = lines.find((line) => line.startsWith("event:"));
      const dataLine = lines.find((line) => line.startsWith("data:"));
      if (!eventLine || !dataLine) {
        continue;
      }
      const type = eventLine.slice(6).trim();
      try {
        const payload = JSON.parse(dataLine.slice(5).trim()) as Record<string, unknown>;
        handlers.onEvent?.({ type, payload });
      } catch {
        /* 忽略无法解析的分片 */
      }
    }
  }
}
