export interface Conversation {
  id: number;
  title: string;
  created_at: string;
  updated_at: string;
  /** null 表示跟随运行时默认值。 */
  thinking: boolean | null;
  /** null 表示尚未固定，第一轮会固定当时的默认模型。 */
  model_profile_id: number | null;
}

export interface ModelCapabilities {
  streaming: boolean;
  tool_calling: boolean;
  thinking: boolean;
  vision: boolean;
  json_mode: boolean;
  [key: string]: boolean;
}

export interface ModelServiceSummary {
  id: number;
  slug: string;
  name: string;
  protocol: "anthropic" | "openai_compatible";
  base_url: string;
  credential_configured: boolean;
  enabled: boolean;
}

export interface ModelProfileSummary {
  id: number;
  slug: string;
  service_id: number;
  service_slug: string;
  service_name: string;
  protocol: "anthropic" | "openai_compatible";
  model_id: string;
  display_name: string;
  enabled: boolean;
  available: boolean;
  reason: string;
  capabilities: ModelCapabilities;
  is_default: boolean;
}

export interface ModelCatalog {
  purpose: "chat" | "consolidation";
  default_profile_id: number | null;
  services: ModelServiceSummary[];
  profiles: ModelProfileSummary[];
}

export interface RuntimeSettings {
  provider: string;
  model: string;
  thinking_default: boolean;
  thinking_toggle: boolean;
  /** 是否已挂载只读知识库（由后端 VAULT_PATH 决定）。 */
  kb_enabled?: boolean;
  values: Record<string, unknown>;
  sources: Record<string, "db" | "env" | "default">;
  fields: RuntimeSettingField[];
  providers: RuntimeProvider[];
  env_only: string[];
  /** 只能改 .env 的项的状态。**secret 类的 value 永远是空串** */
  env_status?: EnvFieldStatus[];
  consolidate_auto?: boolean;
  consolidate_hour?: number;
  consolidate_model?: string | null;
  timezone?: string;
}

export interface EnvFieldStatus {
  key: string;
  /** 环境变量名，例如 ANTHROPIC_API_KEY */
  env: string;
  label: string;
  /** secret = 只报配没配，value 恒为空串；plain = value 可显示 */
  kind: "secret" | "plain";
  configured: boolean;
  value: string;
  /** 没配置时的后果。只在真有后果时非空 */
  note: string;
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

export interface ToolSchemaProperty {
  type?: string;
  description?: string;
  enum?: unknown[];
  items?: ToolSchemaProperty;
  [key: string]: unknown;
}

export interface ToolDefinition {
  name: string;
  description: string;
  input_schema: {
    type: string;
    properties: Record<string, ToolSchemaProperty>;
    required?: string[];
    [key: string]: unknown;
  };
  category: string;
  category_label: string;
  enabled: boolean;
  availability: string;
  /** 能跑这个工具的协议，从后端 provider 注册表推导（不再是写死的厂商名） */
  protocols: string[];
  /** 该工具在这个协议上是模型的原生能力；其他协议靠手写 schema 顶上 */
  native_protocol: string | null;
}

export interface ToolCatalog {
  total: number;
  enabled: number;
  tools: ToolDefinition[];
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
  cached_models: Array<{ id: string; size_bytes: number }>;
  voices: string[];
  detail: string;
}

export interface TtsVoices {
  model: string;
  voices: string[];
}

export interface TranscriptionResult {
  text: string;
}

export interface AsrStatus {
  model: string;
  language: string;
  max_tokens: number;
  reachable: boolean;
  loaded: boolean;
  models: string[];
  cached_models: Array<{ id: string; size_bytes: number }>;
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
  model?: string;
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
  | { type: "conversation"; conversation: Conversation }
  | { type: "trace"; trace_id: string }
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

/** 索引与实际记忆文件的一致性。索引漏了某个文件，那条记忆模型就再也读不到了。 */
export interface MemoryIndexAudit {
  ok: boolean;
  issue_count: number;
  summary: string;
  total_files: number;
  index_missing: boolean;
  /** 有内容但索引里没有 —— 模型看不见它们 */
  missing: string[];
  /** 索引指向的文件不存在 */
  orphaned: string[];
  /** [路径, 实际字数] */
  overlong: [string, number][];
  /** [行号, 原文] */
  malformed: [number, string][];
}

export interface ConsolidateResult {
  date: string;
  summarized_conversations: number;
  tool_calls: number;
  memory_writes: number;
  skipped: boolean;
  failed_summaries: number;
  detail: string;
  headline: string;
  title: string;
  new_loops: number;
  closed_loops: number;
  digest_failed: boolean;
}

export interface Echo {
  kind: "recurring" | "followup" | "anniversary";
  text: string;
}

export interface DailyDigest {
  day: string;
  headline: string;
  highlights: string[];
  /** 下面四个是「这是哪一天」。老 digest 没有，后端保证给空值而不是 null。 */
  title: string;
  observation: string;
  quote: string;
  echoes: Echo[];
  model: string;
  created_at: string;
  updated_at: string;
}

export interface OpenLoop {
  id: number;
  text: string;
  opened_on: string;
  closed_on: string | null;
  closed_note: string | null;
  status: "open" | "closed" | "dropped";
  actor: "consolidation" | "manual";
  source_conversation_id: number | null;
}

export type TimelineKind = "todo" | "event" | "reminder" | "birthday" | "travel" | "deadline" | "note";
export type TimelineStatus = "pending" | "confirmed" | "completed" | "cancelled";

export interface TimelineItem {
  id: number;
  title: string;
  details: string;
  kind: TimelineKind;
  status: TimelineStatus;
  starts_at: string;
  /** 用户原话里的时间依据（「明早九点」）。手工录入和老数据为空。 */
  said: string;
  ends_at: string | null;
  all_day: boolean;
  timezone: string;
  location: string;
  recurrence: "none" | "yearly";
  actor: "chat" | "manual";
  source_conversation_id: number | null;
  source_message_id: number | null;
  /** 这一条要不要推送提醒。关掉但保留事项本身。 */
  notify: boolean;
  /** 提前多少分钟提醒。null = 按类型取默认。 */
  lead_minutes: number | null;
  /** 实际推送时刻。null = 这条不提醒。 */
  remind_at: string | null;
  snoozed_until: string | null;
  created_at: string;
  updated_at: string;
}

export interface TimelineInput {
  title: string;
  details?: string;
  kind?: TimelineKind;
  status?: TimelineStatus;
  starts_at: string;
  ends_at?: string | null;
  all_day?: boolean;
  timezone?: string;
  location?: string;
  recurrence?: "none" | "yearly";
  notify?: boolean;
  lead_minutes?: number | null;
}

export interface NotifyChannelStatus {
  name: string;
  enabled: boolean;
  configured: boolean;
  reason: string;
}

export interface NotificationRecord {
  id: number;
  kind: string;
  title: string;
  body: string;
  channels: string;
  error: string;
  attempts: number;
  created_at: string;
  delivered_at: string | null;
}

export interface NotifyStatus {
  enabled: boolean;
  ready: boolean;
  channels: NotifyChannelStatus[];
  recent: NotificationRecord[];
}

export interface NotifyTestResult {
  delivered: boolean;
  channels: string;
  error: string;
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

export interface ConversationContext {
  history_chars: number;
  history_budget_chars: number;
  retained_messages: number;
  retained_turns: number;
  trimmed_messages: number;
  prompt_tokens: number;
  cached_tokens: number;
}

/** 一条评测样本的概览。完整内容在 evals/cases/*.json 里，界面只显示标注状态。 */
export interface EvalCaseSummary {
  id: string;
  date: string;
  note: string;
  conversations: number;
  memory_files: number;
  facts: number;
  corrections: number;
  forbidden: number;
  no_op: boolean;
  /** 标注自查的结果。非空时这条样本不能参与评测 */
  problems: string[];
}

export interface EvalDataset {
  directory: string;
  total: number;
  no_op_cases: number;
  valid: boolean;
  cases: EvalCaseSummary[];
}

export interface EvalSummary {
  total: number;
  usable: number;
  crashed: number;
  judge_failed: number;
  /** null = 这批样本里没有可判定的项，和 0 不是一回事 */
  recall: number | null;
  correction_rate: number | null;
  errors_total: number;
  no_op_respected: number | null;
  index_issues_total: number;
  index_clean_rate: number | null;
  writes_total: number;
  tool_calls_total: number;
  seconds_total: number;
}

export interface EvalCaseScore {
  case_id: string;
  recall: number | null;
  correction_rate: number | null;
  error_count: number;
  no_op_respected: boolean | null;
  index_issues: number;
  memory_writes: number;
  tool_calls: number;
  seconds: number;
  crashed: boolean;
  judge_failed: boolean;
  detail: string;
  usable: boolean;
}

export interface EvalRunState {
  run_id: string;
  /** interrupted = 进程重启把这轮带走了，结果没保存 */
  status: "running" | "done" | "failed" | "interrupted";
  total: number;
  completed: number;
  current_case: string;
  started_at: string;
  finished_at: string;
  detail: string;
  saved_path: string;
  meta: Record<string, string>;
  summary: EvalSummary | null;
  scores: EvalCaseScore[];
}

export interface EvalHistoryEntry {
  name: string;
  created_at: string;
  model: string;
  judged: boolean;
  summary: EvalSummary | null;
}

/** Phoenix 可观测性的状态。**没有写接口** —— 启用与否是启动期决定的。 */
export interface ObservabilityStatus {
  /** off | missing_deps | failed | unreachable | ready —— 离能用还差哪一步 */
  stage: "off" | "missing_deps" | "failed" | "unreachable" | "ready";
  enabled: boolean;
  installed: boolean;
  active: boolean;
  reachable: boolean;
  project: string;
  collector_endpoint: string;
  traced_paths: string[];
  trace_reads: boolean;
  capture_content: boolean;
  detail: string;
  /** 只有需要动手时才非空（比如 Phoenix 容器没起）。开关类问题不给命令 */
  remedy_command: string;
  retention_warning: string;
}
