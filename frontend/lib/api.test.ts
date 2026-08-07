import { afterEach, describe, expect, it, vi } from "vitest";
import { apiUrl, clearDebugRequests, createBackup, getAsrStatus, getDebugPrompt, getDebugRequest, getNextSpeech, getToolCatalog, getTtsStatus, getTtsVoices, listDebugRequests, listReviewDays, parseSseEventLine, prepareSpeech, restoreMemoryVersion, searchAll, stopSpeech, synthesizeSpeech, transcribeAudio, updateConversation, updateRuntimeSettings, warmupSpeech } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("parseSseEventLine", () => {
  it("parses a complete event and ignores non-data lines", () => {
    const events: unknown[] = [];
    parseSseEventLine(": keep-alive", (event) => events.push(event));
    parseSseEventLine('data: {"type":"text_delta","text":"你好"}\r', (event) => events.push(event));
    expect(events).toEqual([{ type: "text_delta", text: "你好" }]);
  });

  it("rejects malformed JSON", () => {
    expect(() => parseSseEventLine("data: {broken", () => undefined)).toThrow("无法解析");
  });

  it("encodes global search queries", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ query: "76%", conversations: [], memories: [] }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await searchAll("76%");

    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/api/search?q=76%25&limit=20", expect.objectContaining({ headers: expect.any(Headers) }));
  });

  it("updates a conversation without dropping the three-state thinking value", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: 3, title: "新对话", created_at: "", updated_at: "", thinking: null }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await updateConversation(3, { thinking: null });

    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/api/conversations/3", expect.objectContaining({ method: "PATCH", body: JSON.stringify({ thinking: null }) }));
  });

  it("restores a memory by version id", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ path: "/memories/notes.md", content: "restored", created_at: "", updated_at: "" }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await restoreMemoryVersion(42);

    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/api/memories/restore", expect.objectContaining({ method: "POST", body: JSON.stringify({ version_id: 42 }) }));
  });

  it("updates backend settings without restarting the runtime", async () => {
    const response = { values: { deepseek_thinking: true }, sources: { deepseek_thinking: "db" }, fields: [], providers: [], env_only: [], provider: "deepseek", model: "deepseek-v4-flash", thinking_default: true, thinking_toggle: true };
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(response), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await updateRuntimeSettings({ deepseek_thinking: true });

    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/api/settings", expect.objectContaining({ method: "PATCH", body: JSON.stringify({ deepseek_thinking: true }) }));
  });

  it("starts a full backup", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ dump_file: "chat.dump", dump_bytes: 12, memory_files: 2, memory_dir: "/backups/memories", created_at: "", detail: "" }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await createBackup();

    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/api/jobs/backup", expect.objectContaining({ method: "POST" }));
  });

  it("loads debug prompt and request snapshots", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ system: "你是助手", chars: 4, approx_tokens: 4, note: "只含索引" }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ enabled: true, capacity: 20, items: [] }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: 8, at: "", provider: "deepseek", model: "model", conversation_id: 3, iteration: 0, messages: 2, system_chars: 4, tools: 0, usage: {}, stop_reason: "stop", error: "", seconds: 1.2, payload: {}, outline: [] }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getDebugPrompt()).resolves.toMatchObject({ chars: 4 });
    await expect(listDebugRequests(3, 5)).resolves.toMatchObject({ enabled: true });
    await expect(getDebugRequest(8)).resolves.toMatchObject({ id: 8 });
    expect(fetchMock).toHaveBeenNthCalledWith(2, "http://localhost:8000/api/debug/requests?limit=5&conversation_id=3", expect.anything());
    expect(fetchMock).toHaveBeenNthCalledWith(3, "http://localhost:8000/api/debug/requests/8", expect.anything());
  });

  it("loads the complete tool catalog", async () => {
    const catalog = { total: 1, enabled: 1, tools: [{ name: "memory" }] };
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(catalog), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getToolCatalog()).resolves.toEqual(catalog);
    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/api/tools", expect.anything());
  });

  it("loads only dates that have reviewable content", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(["2026-08-06", "2026-08-03"]), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(listReviewDays()).resolves.toEqual(["2026-08-06", "2026-08-03"]);
    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/api/review/days", expect.anything());
  });

  it("clears debug request snapshots", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await clearDebugRequests();

    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/api/debug/requests", expect.objectContaining({ method: "DELETE" }));
  });

  it("reads the TTS status endpoint", async () => {
    const status = { mode: "manual", enabled: true, base_url: "http://127.0.0.1:8001", model: "voice-model", voice: "Vivian", format: "mp3", max_chars: 800, reachable: true, models: [], detail: "" };
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(status), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getTtsStatus()).resolves.toEqual(status);
    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/api/tts/status", expect.objectContaining({ headers: expect.any(Headers) }));
  });

  it("reads the ASR status endpoint", async () => {
    const status = { model: "mlx-community/Qwen3-ASR-1.7B-8bit", language: "Chinese", max_tokens: 512, reachable: true, loaded: false, models: [], cached_models: [], detail: "" };
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(status), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getAsrStatus()).resolves.toEqual(status);
    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/api/asr/status", expect.anything());
  });

  it("loads voices for the selected local model", async () => {
    const catalog = { model: "voice/model", voices: ["Vivian", "Serena"] };
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(catalog), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getTtsVoices("voice/model")).resolves.toEqual(catalog);
    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/api/tts/voices?model=voice%2Fmodel", expect.anything());
  });

  it("returns an audio blob from the TTS speech endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(new Blob(["audio"]), { status: 200, headers: { "Content-Type": "audio/mpeg" } }));
    vi.stubGlobal("fetch", fetchMock);

    const blob = await synthesizeSpeech({ text: "## 你好", voice: "Vivian", instruct: "自然地说" });

    expect(blob).toBeInstanceOf(Blob);
    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/api/tts/speech", expect.objectContaining({ method: "POST", body: JSON.stringify({ text: "## 你好", voice: "Vivian", instruct: "自然地说" }) }));
  });

  it("prepares a one-time streaming audio URL", async () => {
    const payload = { url: "/api/tts/stream/token.mp3", expires_in: 900 };
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(payload), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(prepareSpeech({ text: "你好" })).resolves.toEqual(payload);
    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/api/tts/prepare", expect.objectContaining({ method: "POST", body: JSON.stringify({ text: "你好" }) }));
    expect(apiUrl(payload.url)).toBe("http://localhost:8000/api/tts/stream/token.mp3");
  });

  it("requests the next complete speech segment and controls the queue", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ url: "/api/tts/stream/next.mp3", text: "第一句。", cursor: 4, expires_in: 900 }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ dropped: 2 }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ seconds: 1.2 }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getNextSpeech({ text: "第一句。第二句", cursor: 0 })).resolves.toMatchObject({ cursor: 4 });
    await expect(stopSpeech()).resolves.toEqual({ dropped: 2 });
    await expect(warmupSpeech()).resolves.toEqual({ seconds: 1.2 });
    expect(fetchMock).toHaveBeenNthCalledWith(1, "http://localhost:8000/api/tts/next", expect.objectContaining({ method: "POST", body: JSON.stringify({ text: "第一句。第二句", cursor: 0 }) }));
    expect(fetchMock).toHaveBeenNthCalledWith(2, "http://localhost:8000/api/tts/stop", expect.objectContaining({ method: "POST" }));
    expect(fetchMock).toHaveBeenNthCalledWith(3, "http://localhost:8000/api/tts/warmup", expect.objectContaining({ method: "POST" }));
  });

  it("surfaces the TTS error detail", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "语音服务离线" }), { status: 502, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(synthesizeSpeech({ text: "你好" })).rejects.toThrow("语音服务离线");
  });

  it("uploads a browser recording for transcription without overriding the multipart content type", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ text: "这是语音输入" }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(transcribeAudio(new Blob(["audio"], { type: "audio/webm;codecs=opus" }))).resolves.toEqual({ text: "这是语音输入" });

    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/api/asr/transcriptions", expect.objectContaining({ method: "POST", body: expect.any(FormData), headers: expect.any(Headers) }));
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect((init.headers as Headers).has("Content-Type")).toBe(false);
    const body = init.body as FormData;
    expect(body.get("model")).toBeNull();
    expect((body.get("file") as File).name).toBe("recording.webm");
  });

  it("surfaces an ASR proxy error detail", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "ASR 模型还没有加载完成" }), { status: 503, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(transcribeAudio(new Blob(["audio"], { type: "audio/mp4" }))).rejects.toThrow("ASR 模型还没有加载完成");
  });
});
