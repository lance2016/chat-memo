"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { Check, LoaderCircle, TriangleAlert, X } from "lucide-react";
import { useI18n } from "@/components/i18n-provider";

export type ToastTone = "info" | "success" | "danger";

export type ToastAction = {
  label: string;
  /** 抛异常会被吞掉并交给 onError —— 撤销失败不能把整页搞崩。 */
  run: () => void | Promise<void>;
};

export type ToastInput = {
  message: string;
  description?: string;
  tone?: ToastTone;
  action?: ToastAction;
  /** 毫秒；0 表示不自动消失。带动作的默认给足撤销时间。 */
  duration?: number;
};

type Toast = ToastInput & { id: number };

type ToastApi = {
  push: (toast: ToastInput) => number;
  dismiss: (id: number) => void;
};

const ToastContext = createContext<ToastApi | null>(null);

/** 同屏最多这么多条，再多就把最旧的挤掉 —— 吐司叠成一列墙比没有还糟。 */
const MAX_VISIBLE = 4;
const DEFAULT_DURATION = 4600;
const ACTION_DURATION = 9000;

export function useToast() {
  const api = useContext(ToastContext);
  if (!api) throw new Error("useToast 必须在 ToastProvider 内使用");
  return api;
}

/**
 * 全局轻提示。
 *
 * 存在的理由是**跨页面**：此前所有反馈都是就地文本（聊天页的 error-banner、
 * 设置页的 runtimeMessage、记忆页的 editor-notice），页面一卸载提示就跟着没了 ——
 * 在设置页点保存再立刻切走，你永远不知道到底成没成。
 *
 * 它同时是「撤销」的载体：破坏性操作先执行，把后悔窗口放在这里，
 * 而不是用确认弹窗打断每一次操作。
 */
export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [paused, setPaused] = useState(false);
  const nextIdRef = useRef(1);

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }, []);

  const push = useCallback((input: ToastInput) => {
    const id = nextIdRef.current++;
    setToasts((current) => [...current, { ...input, id }].slice(-MAX_VISIBLE));
    return id;
  }, []);

  const api = useMemo(() => ({ push, dismiss }), [push, dismiss]);

  return <ToastContext.Provider value={api}>
    {children}
    {toasts.length > 0 && <div
      className="toast-region"
      // 指针停在上面时冻结所有倒计时。撤销按钮在鼠标底下消失是最糟的体验。
      onPointerEnter={() => setPaused(true)}
      onPointerLeave={() => setPaused(false)}
      onFocusCapture={() => setPaused(true)}
      onBlurCapture={() => setPaused(false)}
    >
      {toasts.map((toast) => <ToastItem key={toast.id} toast={toast} paused={paused} onDismiss={dismiss} />)}
    </div>}
  </ToastContext.Provider>;
}

function ToastItem({ toast, paused, onDismiss }: { toast: Toast; paused: boolean; onDismiss: (id: number) => void }) {
  const { t } = useI18n();
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState("");
  const duration = toast.duration ?? (toast.action ? ACTION_DURATION : DEFAULT_DURATION);
  const remainingRef = useRef(duration);
  const startedRef = useRef(0);

  useEffect(() => {
    // busy 期间也不能倒计时：撤销请求还在飞，卡片不该在中途消失。
    if (paused || busy || !duration || remainingRef.current <= 0) return;
    startedRef.current = Date.now();
    const timer = window.setTimeout(() => onDismiss(toast.id), remainingRef.current);
    return () => {
      window.clearTimeout(timer);
      remainingRef.current = Math.max(0, remainingRef.current - (Date.now() - startedRef.current));
    };
  }, [busy, duration, onDismiss, paused, toast.id]);

  const runAction = async () => {
    if (!toast.action || busy) return;
    setBusy(true);
    setActionError("");
    try {
      await toast.action.run();
      onDismiss(toast.id);
    } catch {
      // 卡片留在原地并说明失败，比默默消失强 —— 用户以为撤销成功了才是最坏的。
      setActionError(t("toast.actionFailed"));
      setBusy(false);
    }
  };

  const tone = toast.tone ?? "info";
  return <div
    className={`toast toast-${tone}`}
    // 报错要打断，成功提示不该抢读屏的话头。
    role={tone === "danger" ? "alert" : "status"}
    aria-live={tone === "danger" ? "assertive" : "polite"}
  >
    <span className="toast-icon" aria-hidden="true">
      {tone === "danger" ? <TriangleAlert size={15} /> : <Check size={15} />}
    </span>
    <div className="toast-copy">
      <strong>{toast.message}</strong>
      {(actionError || toast.description) && <span>{actionError || toast.description}</span>}
    </div>
    {toast.action && <button className="toast-action" type="button" disabled={busy} onClick={() => void runAction()}>
      {busy && <LoaderCircle size={12} className="spin" />}{toast.action.label}
    </button>}
    <button className="toast-close" type="button" aria-label={t("toast.dismiss")} onClick={() => onDismiss(toast.id)}>
      <X size={14} />
    </button>
  </div>;
}
