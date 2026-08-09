import type {
  ApiMessage,
  AsrStatus,
  AttachmentMeta,
  BackupResult,
  ChatEvent,
  Conversation,
  ConversationClearResult,
  ConversationContext,
  ConversationSummary,
  ConsolidateResult,
  DailyDigest,
  DailyUsage,
  DebugPrompt,
  DebugRequestDetail,
  DebugRequestList,
  EvalCaseDetail,
  EvalDataset,
  EvalExpect,
  EvalExportResult,
  EvalHistoryEntry,
  EvalRunState,
  HealthStatus,
  Memory,
  MemoryNode,
  MemoryIndexAudit,
  MemoryImportResult,
  MemoryStats,
  MemoryVersion,
  ModelCatalog,
  NotifyStatus,
  ObservabilityStatus,
  NotifyTestResult,
  OpenLoop,
  PrepareResult,
  RuntimeSettings,
  SearchResults,
  Skill,
  SkillCatalog,
  SkillDetail,
  SkillInstallResult,
  SpeechRequest,
  TtsStatus,
  TtsVoices,
  TranscriptionResult,
  TtsNextRequest,
  TtsNextResult,
  TimelineInput,
  TimelineItem,
  TimelineStatus,
  ToolCatalog,
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
  // FormData 必须让浏览器自己写 Content-Type —— 手动设成 application/json
  // 会连 multipart 的 boundary 一起丢掉，后端只会看到一个解析不了的请求体。
  if (init?.body && !headers.has("Content-Type") && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");

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

export function listConversations(limit = 50, archived = false, offset = 0) {
  return request<Conversation[]>(`/api/conversations?limit=${limit}&archived=${archived}&offset=${offset}`);
}

export function listTimeline(params: { from?: string; to?: string; statuses?: TimelineStatus[]; limit?: number; includeOverdue?: boolean } = {}, signal?: AbortSignal) {
  const query = new URLSearchParams();
  if (params.from) query.set("from", params.from);
  if (params.to) query.set("to", params.to);
  if (params.statuses?.length) query.set("status", params.statuses.join(","));
  if (params.limit) query.set("limit", String(params.limit));
  if (params.includeOverdue) query.set("include_overdue", "true");
  const suffix = query.size ? `?${query.toString()}` : "";
  return request<TimelineItem[]>(`/api/timeline${suffix}`, { signal });
}

export function getNotifyStatus(signal?: AbortSignal) {
  return request<NotifyStatus>("/api/notify/status", { signal });
}

export function sendTestNotification() {
  return request<NotifyTestResult>("/api/notify/test", { method: "POST" });
}

export function createTimelineItem(input: TimelineInput) {
  return request<TimelineItem>("/api/timeline", { method: "POST", body: JSON.stringify(input) });
}

export function updateTimelineItem(id: number, changes: Partial<TimelineInput> & { status?: TimelineStatus }) {
  return request<TimelineItem>(`/api/timeline/${id}`, { method: "PATCH", body: JSON.stringify(changes) });
}

export function deleteTimelineItem(id: number) {
  return request<void>(`/api/timeline/${id}`, { method: "DELETE" });
}

export function getHealth() {
  return request<HealthStatus>("/health");
}

export function getRuntimeSettings() {
  return request<RuntimeSettings>("/api/settings");
}

export function getModelCatalog(purpose: "chat" | "consolidation" = "chat") {
  return request<ModelCatalog>(`/api/models?purpose=${purpose}`);
}

export function createModelService(input: {
  name: string;
  slug: string;
  protocol: "anthropic" | "openai_compatible";
  base_url?: string;
  credential_ref?: string;
}) {
  return request<ModelCatalog>("/api/models/services", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateModelService(id: number, changes: Record<string, unknown>) {
  return request<ModelCatalog>(`/api/models/services/${id}`, {
    method: "PATCH",
    body: JSON.stringify(changes),
  });
}

export function createModelProfile(input: {
  service_id: number;
  model_id: string;
  display_name?: string;
  capabilities?: Record<string, boolean>;
  context_window_tokens?: number;
}) {
  return request<ModelCatalog>("/api/models/profiles", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateModelProfile(id: number, changes: Record<string, unknown>) {
  return request<ModelCatalog>(`/api/models/profiles/${id}`, {
    method: "PATCH",
    body: JSON.stringify(changes),
  });
}

export function setDefaultModel(purpose: "chat" | "consolidation", profileId: number | null) {
  return request<ModelCatalog>("/api/models/default", {
    method: "POST",
    body: JSON.stringify({ purpose, profile_id: profileId }),
  });
}

export function getToolCatalog() {
  return request<ToolCatalog>("/api/tools");
}

export function listSkills() {
  return request<SkillCatalog>("/api/skills");
}

export function getSkill(name: string) {
  return request<SkillDetail>(`/api/skills/${encodeURIComponent(name)}`);
}

export function installSkill(source: string, overwrite = true) {
  return request<SkillInstallResult>("/api/skills/install", {
    method: "POST",
    body: JSON.stringify({ source, overwrite }),
  });
}

export function uploadSkill(file: File, overwrite = true) {
  const body = new FormData();
  body.append("file", file);
  // 不设 Content-Type：浏览器要自己带 multipart 的 boundary
  return request<SkillInstallResult>(`/api/skills/upload?overwrite=${overwrite}`, {
    method: "POST",
    body,
  });
}

export function uploadAttachment(file: File, conversationId: number | null) {
  const body = new FormData();
  body.append("file", file);
  const query = conversationId === null ? "" : `?conversation_id=${conversationId}`;
  // 同 uploadSkill：不设 Content-Type，让浏览器自己带 boundary
  return request<AttachmentMeta>(`/api/attachments${query}`, { method: "POST", body });
}

// 取回的 blob URL 按附件 id 缓存。
// **不能直接把接口地址写进 `<img src>`**：那是浏览器自己发的 GET，带不了
// X-API-Key。所以先 authed fetch 一次再 createObjectURL。
// 代价是绕开了浏览器的 HTTP 缓存，所以这里自己存一份 —— 同一张图在气泡里
// 反复渲染（每次 setState 都会重跑）不该每次都重新下载一遍。
const attachmentUrls = new Map<number, Promise<string>>();

export function attachmentObjectUrl(id: number): Promise<string> {
  const cached = attachmentUrls.get(id);
  if (cached) return cached;
  const pending = (async () => {
    const headers = new Headers();
    const apiKey = process.env.NEXT_PUBLIC_API_KEY;
    if (apiKey) headers.set("X-API-Key", apiKey);
    const response = await fetch(`${API_BASE}/api/attachments/${id}`, { headers });
    if (!response.ok) throw new ApiError(response.status, `图片加载失败（${response.status}）`);
    return URL.createObjectURL(await response.blob());
  })();
  // 失败的不留在缓存里，否则一次网络抖动会让这张图这辈子都加载不出来
  pending.catch(() => attachmentUrls.delete(id));
  attachmentUrls.set(id, pending);
  return pending;
}

export function setSkillEnabled(name: string, enabled: boolean) {
  return request<Skill>(`/api/skills/${encodeURIComponent(name)}`, {
    method: "PATCH",
    body: JSON.stringify({ enabled }),
  });
}

export function deleteSkill(name: string) {
  return request<void>(`/api/skills/${encodeURIComponent(name)}`, { method: "DELETE" });
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

export function clearConversations() {
  return request<ConversationClearResult>("/api/conversations", { method: "DELETE" });
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

export function getConversationContext(id: number) {
  return request<ConversationContext>(`/api/conversations/${id}/context`);
}

export function getContextPreview() {
  return request<ConversationContext>("/api/context");
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

export function getObservabilityStatus(signal?: AbortSignal) {
  return request<ObservabilityStatus>("/api/obs/status", { signal });
}

export function getEvalDataset() {
  return request<EvalDataset>("/api/eval/dataset");
}

export function getEvalStatus() {
  return request<EvalRunState | null>("/api/eval/status");
}

export function startEvalRun(options: { judge?: boolean; only?: string } = {}) {
  return request<EvalRunState>("/api/eval/run", {
    method: "POST",
    body: JSON.stringify({ judge: options.judge ?? true, only: options.only ?? "" }),
  });
}

export function acknowledgeEvalRun() {
  return request<EvalRunState | null>("/api/eval/acknowledge", { method: "POST" });
}

export function startNoiseRun(options: { caseId?: string; repeat?: number } = {}) {
  return request<EvalRunState>("/api/eval/noise", {
    method: "POST",
    body: JSON.stringify({ case_id: options.caseId ?? "", repeat: options.repeat ?? 3 }),
  });
}

export function exportEvalDay(day: string) {
  return request<EvalExportResult>(`/api/eval/export?day=${day}`, { method: "POST" });
}

export function getEvalCase(caseId: string) {
  return request<EvalCaseDetail>(`/api/eval/cases/${encodeURIComponent(caseId)}`);
}

export function saveEvalExpect(caseId: string, expect: EvalExpect) {
  return request<EvalCaseDetail>(`/api/eval/cases/${encodeURIComponent(caseId)}/expect`, {
    method: "PUT",
    body: JSON.stringify(expect),
  });
}

export function cancelEvalRun() {
  return request<EvalRunState | null>("/api/eval/cancel", { method: "POST" });
}

export function listEvalRuns(limit = 20) {
  return request<EvalHistoryEntry[]>(`/api/eval/runs?limit=${limit}`);
}

export function getMemoryAudit() {
  return request<MemoryIndexAudit>("/api/memories/audit");
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

export function importMemories(file: File) {
  const body = new FormData();
  body.append("file", file);
  return request<MemoryImportResult>("/api/memories/import", { method: "POST", body });
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

export function getDigest(day: string) {
  // 没整理过的那天返回 null，这是常态，别当错误。
  return request<DailyDigest | null>(`/api/digests?day=${encodeURIComponent(day)}`);
}

export function listReviewDays() {
  return request<string[]>("/api/review/days");
}

export function listOpenLoops(day?: string) {
  const query = day ? `?day=${encodeURIComponent(day)}` : "";
  return request<OpenLoop[]>(`/api/open-loops${query}`);
}

export function createOpenLoop(text: string, openedOn?: string) {
  return request<OpenLoop>("/api/open-loops", {
    method: "POST",
    body: JSON.stringify({ text, opened_on: openedOn ?? null }),
  });
}

export function closeOpenLoop(id: number, note = "") {
  return request<OpenLoop>(`/api/open-loops/${id}/close`, {
    method: "POST",
    body: JSON.stringify({ note }),
  });
}

export function reopenOpenLoop(id: number) {
  return request<OpenLoop>(`/api/open-loops/${id}/reopen`, { method: "POST" });
}

export function dropOpenLoop(id: number) {
  return request<void>(`/api/open-loops/${id}`, { method: "DELETE" });
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
  conversationId: number | null,
  content: string,
  modelProfileId: number | null,
  onEvent: (event: ChatEvent) => void,
  signal?: AbortSignal,
  webSearchEnabled = false,
  attachmentIds: number[] = [],
) {
  const headers = new Headers({ "Content-Type": "application/json" });
  const apiKey = process.env.NEXT_PUBLIC_API_KEY;
  if (apiKey) headers.set("X-API-Key", apiKey);

  let response: Response;
  try {
    response = await fetch(`${API_BASE}/api/chat`, {
      method: "POST",
      headers,
      body: JSON.stringify({ conversation_id: conversationId, content, model_profile_id: modelProfileId, web_search: webSearchEnabled, attachment_ids: attachmentIds }),
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
