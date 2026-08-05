export interface Conversation {
  id: number;
  title: string;
  created_at: string;
  updated_at: string;
  /** null 表示跟随运行时默认值。 */
  thinking: boolean | null;
}

export interface RuntimeSettings {
  provider: string;
  model: string;
  thinking_default: boolean;
  thinking_toggle: boolean;
  /** Optional until the backend exposes editable consolidation settings. */
  consolidate_auto?: boolean;
  consolidate_hour?: number;
  consolidate_model?: string | null;
  timezone?: string;
}

export interface HealthStatus {
  status: string;
  provider?: string;
  model?: string;
}

export interface TruncateResult {
  deleted: number;
}

export type ContentBlock =
  | { type: "text"; text: string }
  | { type: "thinking"; thinking: string; signature?: string }
  | { type: "tool_use"; id: string; name: string; input: Record<string, unknown> }
  | { type: "tool_result"; tool_use_id: string; content: string; is_error?: boolean }
  | { type: string; [key: string]: unknown };

export interface ApiMessage {
  id: number;
  role: "user" | "assistant";
  content: ContentBlock[];
  usage: MessageUsage | null;
  created_at: string;
}

export interface MessageUsage {
  [key: string]: number | boolean | undefined;
  interrupted?: boolean;
}

export type ChatEvent =
  | { type: "thinking_delta"; text: string }
  | { type: "text_delta"; text: string }
  | { type: "tool_use"; name: string; input: Record<string, unknown> }
  | { type: "tool_result"; name: string; ok: boolean; summary: string }
  | { type: "title"; title: string }
  | { type: "done"; usage: MessageUsage }
  | { type: "message_id"; message_id: number }
  | { type: "error"; message: string };

export interface ToolActivity {
  name: string;
  input: Record<string, unknown>;
  ok: boolean;
  summary: string;
}

export type Turn =
  | { kind: "user"; text: string; messageId?: number }
  | { kind: "assistant"; text: string; thinking: string; tools: ToolActivity[]; usage?: MessageUsage; messageId?: number };

export interface MemoryNode {
  path: string;
  is_dir: boolean;
  size: number;
}

export interface Memory {
  path: string;
  content: string;
  created_at: string;
  updated_at: string;
}

export interface MemoryVersion {
  id: number;
  path: string;
  content: string;
  operation: "created" | "modified" | "deleted";
  actor: "chat" | "consolidation" | "manual";
  created_at: string;
}

export interface MemoryUsage {
  path: string;
  reads: number;
  writes: number;
  last_read_at: string | null;
  created_at: string;
  idle_days: number | null;
  content_chars: number;
}

export interface MemoryStats {
  total_memories: number;
  total_reads: number;
  total_writes: number;
  never_read: number;
  missed_reads: number;
  daily: { day: string; reads: number; writes: number }[];
  top: MemoryUsage[];
  unused: MemoryUsage[];
  by_actor: { actor: string; reads: number; writes: number }[];
}

export interface ConsolidateResult {
  date: string;
  summarized_conversations: number;
  tool_calls: number;
  memory_writes: number;
  skipped: boolean;
  failed_summaries: number;
  detail: string;
}

export interface ConversationSummary {
  id: number;
  conversation_id: number;
  conversation_title: string;
  summary: string;
  created_at: string;
}

export interface SearchConversationHit {
  conversation_id: number;
  title: string;
  message_id: number;
  role: "user" | "assistant";
  snippet: string;
  matches: number;
  created_at: string;
}

export interface SearchMemoryHit {
  path: string;
  snippet: string;
}

export interface SearchResults {
  query: string;
  conversations: SearchConversationHit[];
  memories: SearchMemoryHit[];
}

export interface DailyUsage {
  day: string;
  messages: number;
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
}
