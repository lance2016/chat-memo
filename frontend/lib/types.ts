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
  values: Record<string, unknown>;
  sources: Record<string, "db" | "env">;
  fields: RuntimeSettingField[];
  providers: RuntimeProvider[];
  env_only: string[];
  consolidate_auto?: boolean;
  consolidate_hour?: number;
  consolidate_model?: string | null;
  timezone?: string;
}

export interface RuntimeSettingField {
  key: string;
  label: string;
  kind: "str" | "text" | "int" | "bool" | "enum";
  choices: string[];
  minimum?: number | null;
  maximum?: number | null;
  provider?: string;
  group: string;
}

export interface RuntimeProvider {
  value: string;
  available: boolean;
  reason: string;
}

export interface HealthStatus {
  status: string;
  provider?: string;
  model?: string;
}

export interface BackupResult {
  dump_file: string;
  dump_bytes: number;
  memory_files: number;
  memory_dir: string;
  created_at: string;
  detail: string;
}

export type TtsMode = "off" | "manual" | "auto";

export interface TtsStatus {
  mode: TtsMode;
  stream: boolean;
  enabled: boolean;
  base_url: string;
  model: string;
  voice: string;
  format: string;
  max_chars: number;
  reachable: boolean;
  models: string[];
  detail: string;
}

export interface DebugPrompt {
  system: string;
  chars: number;
  approx_tokens: number;
  note: string;
}

export interface DebugRequestSummary {
  id: number;
  at: string;
  provider: string;
  model: string;
  conversation_id: number | null;
  iteration: number;
  messages: number;
  system_chars: number;
  tools: number;
  usage: Record<string, number>;
  stop_reason: string;
  error: string;
  seconds: number;
}

export interface DebugRequestDetail extends DebugRequestSummary {
  payload: Record<string, unknown>;
  outline: string[];
}

export interface DebugRequestList {
  enabled: boolean;
  capacity: number;
  items: DebugRequestSummary[];
}

export interface SpeechRequest {
  text: string;
  voice?: string;
  instruct?: string;
  truncate?: boolean;
}

export interface PrepareResult {
  url: string;
  expires_in: number;
}

export interface TtsNextRequest {
  text: string;
  cursor: number;
  flush?: boolean;
}

export interface TtsNextResult {
  url: string | null;
  text: string;
  cursor: number;
  expires_in: number;
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
