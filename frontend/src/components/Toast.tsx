"use client";

import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";

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

const TONE: Record<Toast["tone"], string> = {
  info: "border-slate-300 bg-white text-slate-900",
  success: "border-teal-600 bg-white text-slate-900",
  error: "border-red-300 bg-red-50 text-red-900",
};

function ToastViewport() {
  const { toasts, dismiss } = useToast();
  if (!toasts.length) return null;
  return (
    <div className="fixed bottom-4 right-4 z-50 flex w-96 flex-col gap-2" aria-live="polite">
      {toasts.map((toast) => (
        <div
          key={toast.id}
          role="status"
          className={`flex items-start gap-3 rounded border p-3 text-sm shadow-lg ${TONE[toast.tone]}`}
        >
          <p className="flex-1">{toast.message}</p>
          {toast.action ? (
            <button
              type="button"
              className="btn-primary !py-1"
              onClick={async () => {
                await toast.action?.run();
                dismiss(toast.id);
              }}
            >
              {toast.action.label}
            </button>
          ) : null}
          <button
            type="button"
            aria-label="Dismiss"
            className="text-slate-500 hover:text-slate-900"
            onClick={() => dismiss(toast.id)}
          >
            ×
          </button>
        </div>
      ))}
    </div>
  );
}
