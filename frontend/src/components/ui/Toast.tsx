"use client";

import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import { Button } from "./Button";
import { cx } from "./cx";

export type Toast = {
  id: number;
  message: string;
  tone: "info" | "success" | "error";
  action?: { label: string; run: () => Promise<void> | void };
};

type ToastInput = Omit<Toast, "id">;

type ToastApi = {
  toasts: Toast[];
  show: (toast: ToastInput, ttlMs?: number) => number;
  dismiss: (id: number) => void;
};

const ToastContext = createContext<ToastApi | null>(null);
const DEFAULT_TTL_MS = 8000;

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const counter = useRef(0);

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }, []);

  const show = useCallback(
    (toast: ToastInput, ttlMs: number = DEFAULT_TTL_MS) => {
      const id = ++counter.current;
      setToasts((current) => [...current, { ...toast, id }]);
      if (ttlMs > 0) window.setTimeout(() => dismiss(id), ttlMs);
      return id;
    },
    [dismiss],
  );

  const value = useMemo(() => ({ toasts, show, dismiss }), [toasts, show, dismiss]);
  return (
    <ToastContext.Provider value={value}>
      {children}
      <ToastViewport />
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const value = useContext(ToastContext);
  if (!value) throw new Error("useToast must be used inside <ToastProvider>");
  return value;
}

/** A tint and a word — the tone is never the only thing that says what happened. */
const TONE: Record<Toast["tone"], string> = {
  info: "border-line bg-surface text-ink",
  success: "border-ok bg-ok-tint text-ink",
  error: "border-risk bg-risk-tint text-ink",
};

function ToastViewport() {
  const { toasts, dismiss } = useToast();
  if (!toasts.length) return null;
  return (
    <div className="print-hide fixed bottom-4 right-4 z-50 flex w-96 max-w-[calc(100vw-2rem)] flex-col gap-2">
      {toasts.map((toast) => (
        <div
          key={toast.id}
          role="status"
          className={cx("flex items-start gap-3 rounded-lg border p-3 text-base shadow-overlay", TONE[toast.tone])}
        >
          <p className="flex-1">{toast.message}</p>
          {toast.action ? (
            <Button
              size="sm"
              variant="primary"
              onClick={async () => {
                await toast.action?.run();
                dismiss(toast.id);
              }}
            >
              {toast.action.label}
            </Button>
          ) : null}
          <Button size="sm" variant="ghost" aria-label="Dismiss" onClick={() => dismiss(toast.id)}>
            <span aria-hidden="true">×</span>
          </Button>
        </div>
      ))}
    </div>
  );
}
