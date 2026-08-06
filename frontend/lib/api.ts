import type {
  ApiMessage,
  AsrStatus,
  BackupResult,
  ChatEvent,
  Conversation,
  ConversationSummary,
  ConsolidateResult,
  DailyUsage,
  DebugPrompt,
  DebugRequestDetail,
  DebugRequestList,
  HealthStatus,
  Memory,
  MemoryNode,
  MemoryStats,
  MemoryVersion,
  PrepareResult,
  RuntimeSettings,
  SearchResults,
  SpeechRequest,
  TtsStatus,
  TtsVoices,
  TranscriptionResult,
  TtsNextRequest,
  TtsNextResult,
  TruncateResult,
} from "./types";

const API_BASE = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000").replace(/\/$/, "");

export function apiBaseLabel() {
  return API_BASE.startsWith("/") ? `同源代理 ${API_BASE}` : API_BASE.replace(/^https?:\/\//, "");
}

export function apiUrl(path: string) {
  return /^https?:\/\//.test(path) ? path : `${API_BASE}${path.startsWith("/") ? path : `/${path}`}`;
}

export class ApiError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

export function errorMessage(cause: unknown, fallback: string) {
  if (cause instanceof ApiError && cause.status === 0) return cause.message;
  if (cause instanceof TypeError) return `无法连接后端服务，请确认 ${apiBaseLabel()} 可用`;
  return cause instanceof Error ? cause.message : fallback;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  const apiKey = process.env.NEXT_PUBLIC_API_KEY;
  if (apiKey) headers.set("X-API-Key", apiKey);
  if (init?.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");

  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, { ...init, headers });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new ApiError(0, errorMessage(cause, "无法连接后端服务"));
  }
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) message = body.detail;
    } catch {
      // Some successful mutation endpoints have no JSON body.
    }
    throw new ApiError(response.status, message);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function listConversations(limit = 50, archived = false) {
  return request<Conversation[]>(`/api/conversations?limit=${limit}&archived=${archived}`);
}

export function getHealth() {
  return request<HealthStatus>("/health");
}

export function getRuntimeSettings() {
  return request<RuntimeSettings>("/api/settings");
}

export function updateRuntimeSettings(changes: Record<string, unknown>) {
  return request<RuntimeSettings>("/api/settings", {
    method: "PATCH",
    body: JSON.stringify(changes),
  });
}

export function createBackup() {
  return request<BackupResult>("/api/jobs/backup", { method: "POST" });
}

export function getDebugPrompt() {
  return request<DebugPrompt>("/api/debug/prompt");
}

export function listDebugRequests(conversationId?: number, limit = 20) {
  const query = new URLSearchParams({ limit: String(limit) });
  if (conversationId !== undefined) query.set("conversation_id", String(conversationId));
  return request<DebugRequestList>(`/api/debug/requests?${query.toString()}`);
}

export function getDebugRequest(id: number) {
  return request<DebugRequestDetail>(`/api/debug/requests/${id}`);
}

export function clearDebugRequests() {
  return request<void>("/api/debug/requests", { method: "DELETE" });
}

export function getTtsStatus() {
  return request<TtsStatus>("/api/tts/status");
}

export function getAsrStatus() {
  return request<AsrStatus>("/api/asr/status");
}

export function getTtsVoices(model: string) {
  return request<TtsVoices>(`/api/tts/voices?model=${encodeURIComponent(model)}`);
}

export function prepareSpeech(input: SpeechRequest) {
  return request<PrepareResult>("/api/tts/prepare", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function getNextSpeech(input: TtsNextRequest) {
  return request<TtsNextResult>("/api/tts/next", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function stopSpeech() {
  return request<{ dropped: number }>("/api/tts/stop", { method: "POST" });
}

export function warmupSpeech() {
  return request<{ seconds: number }>("/api/tts/warmup", { method: "POST" });
}

export async function synthesizeSpeech(input: SpeechRequest) {
  const headers = new Headers({ "Content-Type": "application/json" });
  const apiKey = process.env.NEXT_PUBLIC_API_KEY;
  if (apiKey) headers.set("X-API-Key", apiKey);

  let response: Response;
  try {
    response = await fetch(`${API_BASE}/api/tts/speech`, {
      method: "POST",
      headers,
      body: JSON.stringify(input),
    });
  } catch (cause) {
    throw new ApiError(0, errorMessage(cause, "无法连接语音服务"));
  }
  if (!response.ok) {
    let message = `语音合成失败（${response.status}）`;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) message = body.detail;
    } catch {
      // Error responses are documented as JSON, but keep a useful fallback for proxies.
    }
    throw new ApiError(response.status, message);
  }
  // Read bytes and construct the Blob in the current realm. `Response.blob()`
  // may return a Blob owned by another realm under Node/jsdom, which then
  // fails `instanceof Blob` checks and can be rejected by browser APIs.
  return new Blob([await response.arrayBuffer()], {
    type: response.headers.get("Content-Type") ?? "audio/mpeg",
  });
}

function recordingExtension(type: string) {
  if (type.includes("mp4")) return "m4a";
  if (type.includes("ogg")) return "ogg";
  if (type.includes("wav")) return "wav";
  return "webm";
}

export async function transcribeAudio(audio: Blob) {
  const headers = new Headers();
  const apiKey = process.env.NEXT_PUBLIC_API_KEY;
  if (apiKey) headers.set("X-API-Key", apiKey);

  const body = new FormData();
  body.append("file", audio, `recording.${recordingExtension(audio.type)}`);

  let response: Response;
  try {
    response = await fetch(`${API_BASE}/api/asr/transcriptions`, {
      method: "POST",
      headers,
      body,
    });
  } catch (cause) {
    throw new ApiError(0, errorMessage(cause, "无法连接语音转写服务"));
  }
  if (!response.ok) {
    let message = `语音转写失败（${response.status}）`;
    try {
      const result = (await response.json()) as { detail?: string };
      if (result.detail) message = result.detail;
    } catch {
      // Proxies may return a non-JSON error page.
    }
    throw new ApiError(response.status, message);
  }
  return response.json() as Promise<TranscriptionResult>;
}

export function createConversation() {
  return request<Conversation>("/api/conversations", { method: "POST" });
}

export function deleteConversation(id: number) {
  return request<void>(`/api/conversations/${id}`, { method: "DELETE" });
}

export function archiveConversation(id: number, archived = true) {
  return request<Conversation>(`/api/conversations/${id}/archive?archived=${archived}`, { method: "POST" });
}

export function updateConversation(id: number, changes: { title?: string; thinking?: boolean | null }) {
  return request<Conversation>(`/api/conversations/${id}`, {
    method: "PATCH",
    body: JSON.stringify(changes),
  });
}

export function listMessages(id: number) {
  return request<ApiMessage[]>(`/api/conversations/${id}/messages`);
}

export function truncateMessages(conversationId: number, after = 0) {
  return request<TruncateResult>(`/api/conversations/${conversationId}/messages?after=${after}`, { method: "DELETE" });
}

export function listSummaries(params: { day?: string; conversationId?: number; limit?: number } = {}) {
  const query = new URLSearchParams();
  if (params.day) query.set("day", params.day);
  if (params.conversationId !== undefined) query.set("conversation_id", String(params.conversationId));
  if (params.limit !== undefined) query.set("limit", String(params.limit));
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return request<ConversationSummary[]>(`/api/summaries${suffix}`);
}

export function searchAll(query: string, limit = 20, signal?: AbortSignal) {
  const params = new URLSearchParams({ q: query, limit: String(limit) });
  return request<SearchResults>(`/api/search?${params.toString()}`, { signal });
}

export function getDailyUsage(days = 7) {
  return request<DailyUsage[]>(`/api/usage?days=${days}`);
}

export function listMemoryNodes() {
  return request<MemoryNode[]>("/api/memories");
}

export function getMemoryStats(days = 30, top = 10) {
  return request<MemoryStats>(`/api/memories/stats?days=${days}&top=${top}`);
}

function memoryRelativePath(path: string) {
  return path.replace(/^\/memories\/?/, "").split("/").filter(Boolean).map(encodeURIComponent).join("/");
}

function memoryUrl(path: string, suffix = "") {
  const relative = memoryRelativePath(path);
  return `/api/memories/${relative}${suffix}`;
}

export function getMemory(path: string) {
  return request<Memory>(memoryUrl(path));
}

export function saveMemory(path: string, content: string) {
  return request<Memory>(memoryUrl(path), {
    method: "PUT",
    body: JSON.stringify({ content }),
  });
}

export function restoreMemoryVersion(versionId: number) {
  return request<Memory>("/api/memories/restore", {
    method: "POST",
    body: JSON.stringify({ version_id: versionId }),
  });
}

export function deleteMemory(path: string) {
  return request<void>(memoryUrl(path), { method: "DELETE" });
}

export function listMemoryVersions(path: string, limit = 50) {
  return request<MemoryVersion[]>(memoryUrl(path, `/versions?limit=${limit}`));
}

export function listAllMemoryVersions(params: { day?: string; actor?: string; limit?: number } = {}) {
  const query = new URLSearchParams();
  if (params.day) query.set("day", params.day);
  if (params.actor) query.set("actor", params.actor);
  if (params.limit !== undefined) query.set("limit", String(params.limit));
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return request<MemoryVersion[]>(`/api/memories/versions${suffix}`);
}

export function consolidate(day?: string) {
  const query = day ? `?day=${encodeURIComponent(day)}` : "";
  return request<ConsolidateResult>(`/api/jobs/consolidate${query}`, { method: "POST" });
}

export function parseSseEventLine(line: string, onEvent: (event: ChatEvent) => void) {
  const normalized = line.endsWith("\r") ? line.slice(0, -1) : line;
  if (!normalized.startsWith("data: ")) return;
  try {
    onEvent(JSON.parse(normalized.slice(6)) as ChatEvent);
  } catch {
    throw new Error("服务端返回了无法解析的聊天事件");
  }
}

export async function streamChat(
  conversationId: number,
  content: string,
  onEvent: (event: ChatEvent) => void,
  signal?: AbortSignal,
) {
  const headers = new Headers({ "Content-Type": "application/json" });
  const apiKey = process.env.NEXT_PUBLIC_API_KEY;
  if (apiKey) headers.set("X-API-Key", apiKey);

  let response: Response;
  try {
    response = await fetch(`${API_BASE}/api/chat`, {
      method: "POST",
      headers,
      body: JSON.stringify({ conversation_id: conversationId, content }),
      signal,
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new ApiError(0, errorMessage(cause, "无法连接后端服务"));
  }
  if (!response.ok || !response.body) throw new ApiError(response.status, `聊天请求失败（${response.status}）`);

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) parseSseEventLine(line, onEvent);
  }
  buffer += decoder.decode();
  if (buffer) parseSseEventLine(buffer, onEvent);
}
