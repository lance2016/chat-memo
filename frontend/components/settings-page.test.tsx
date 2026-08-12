import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { I18nProvider } from "@/components/i18n-provider";
import { SettingsPage } from "@/components/settings-page";
import { ToastProvider } from "@/components/toast";
import type { AsrStatus, RuntimeSettings, TtsStatus } from "@/lib/types";

const apiMocks = vi.hoisted(() => ({
  getRuntimeSettings: vi.fn(),
  getHealth: vi.fn(),
  getModelCatalog: vi.fn(),
  getTtsStatus: vi.fn(),
  getAsrStatus: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/api")>(),
  ...apiMocks,
}));

const runtime: RuntimeSettings = {
  provider: "deepseek",
  model: "deepseek-chat",
  thinking_default: false,
  thinking_toggle: true,
  values: {
    provider: "deepseek",
    deepseek_model: "deepseek-chat",
    deepseek_max_tokens: 8192,
    tts_mode: "off",
    tts_model: "Qwen/Qwen3-TTS",
    asr_model: "Qwen/Qwen3-ASR-0.6B",
  },
  sources: {},
  providers: [],
  env_only: [],
  fields: [
    { key: "provider", label: "模型厂商", kind: "enum", choices: ["deepseek"], group: "" },
    { key: "deepseek_model", label: "DeepSeek 模型", kind: "str", choices: [], provider: "deepseek", group: "" },
    { key: "deepseek_max_tokens", label: "输出上限", kind: "int", choices: [], provider: "deepseek", group: "", advanced: true },
    { key: "tts_mode", label: "语音播放", kind: "enum", choices: ["off", "manual", "auto"], group: "tts", capability: "voice" },
    { key: "tts_model", label: "语音模型", kind: "str", choices: [], group: "tts", capability: "voice" },
    { key: "asr_model", label: "识别模型", kind: "str", choices: [], group: "asr", capability: "voice_input" },
  ],
};

const ttsStatus: TtsStatus = {
  mode: "off",
  stream: true,
  enabled: false,
  base_url: "http://localhost:8001",
  model: "Qwen/Qwen3-TTS",
  voice: "Vivian",
  format: "mp3",
  max_chars: 2000,
  reachable: false,
  models: [],
  cached_models: [],
  voices: [],
  detail: "",
};

const asrStatus: AsrStatus = {
  model: "Qwen/Qwen3-ASR-0.6B",
  language: "Auto",
  max_tokens: 512,
  reachable: false,
  loaded: false,
  models: [],
  cached_models: [],
  detail: "",
};

describe("SettingsPage", () => {
  beforeEach(() => {
    cleanup();
    vi.clearAllMocks();
    apiMocks.getRuntimeSettings.mockResolvedValue(runtime);
    apiMocks.getHealth.mockResolvedValue({ status: "ok" });
    apiMocks.getModelCatalog.mockResolvedValue({ purpose: "chat", default_profile_id: null, services: [], profiles: [] });
    apiMocks.getTtsStatus.mockResolvedValue(ttsStatus);
    apiMocks.getAsrStatus.mockResolvedValue(asrStatus);
  });

  it("defers optional voice service probes until the voice section opens", async () => {
    render(<I18nProvider><ToastProvider><SettingsPage /></ToastProvider></I18nProvider>);

    await waitFor(() => expect(apiMocks.getRuntimeSettings).toHaveBeenCalledOnce());
    expect(apiMocks.getTtsStatus).not.toHaveBeenCalled();
    expect(apiMocks.getAsrStatus).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /^语音/ }));
    await waitFor(() => expect(apiMocks.getTtsStatus).toHaveBeenCalledOnce());
    expect(apiMocks.getAsrStatus).toHaveBeenCalledOnce();
  });

  it("keeps legacy provider routing out of the model page", async () => {
    render(<I18nProvider><ToastProvider><SettingsPage /></ToastProvider></I18nProvider>);

    await waitFor(() => expect(apiMocks.getRuntimeSettings).toHaveBeenCalledOnce());
    fireEvent.click(screen.getByRole("button", { name: /^模型/ }));

    await waitFor(() => expect(apiMocks.getModelCatalog).toHaveBeenCalled());
    expect(screen.queryByText("旧版 Provider 设置")).not.toBeInTheDocument();
    expect(screen.queryByText("模型厂商")).not.toBeInTheDocument();
    expect(screen.getByText("回答与工具限制")).toBeInTheDocument();
  });
});
