import { afterEach, describe, expect, it, vi } from "vitest";
import { parseSseEventLine, restoreMemoryVersion, searchAll, updateConversation } from "./api";

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
});
