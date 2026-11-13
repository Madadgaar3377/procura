"use client";
import {useLocale} from '@/lib/locale';

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { IconAlertCircle, IconCheck, IconInfoCircle, IconX } from "@tabler/icons-react";

type ToastKind = "success" | "error" | "info";
type ToastItem = { id: number; kind: ToastKind; title: string; description?: string };
type ToastContextValue = {
  success: (title: string, description?: string) => void;
  error: (title: string, description?: string) => void;
  info: (title: string, description?: string) => void;
};

const ToastContext = createContext<ToastContextValue | null>(null);

export function ToastProvider({ children }: { children: React.ReactNode }) {
 const {t}=useLocale();
  const [items, setItems] = useState<ToastItem[]>([]);
  const nextId = useRef(0);

  const push = useCallback((kind: ToastKind, title: string, description?: string) => {
    const id = ++nextId.current;
    setItems((current) => [...current.slice(-3), { id, kind, title, description }]);
  }, []);

  const dismiss = useCallback((id: number) => {
    setItems((current) => current.filter((item) => item.id !== id));
  }, []);

  const value = useMemo<ToastContextValue>(() => ({
    success: (title, description) => push("success", title, description),
    error: (title, description) => push("error", title, description),
    info: (title, description) => push("info", title, description),
  }), [push]);

  return <ToastContext.Provider value={value}>
    {children}
    <div className="toast-region" role="region" aria-label={t("Notifications")} aria-live="polite" aria-relevant="additions text">
      {items.map((item) => <Toast key={item.id} item={item} dismiss={dismiss} />)}
    </div>
  </ToastContext.Provider>;
}

function Toast({ item, dismiss }: { item: ToastItem; dismiss: (id: number) => void }) {
 const {t}=useLocale();
  useEffect(() => {
    const timer = window.setTimeout(() => dismiss(item.id), item.kind === "error" ? 7000 : 4800);
    return () => window.clearTimeout(timer);
  }, [dismiss, item.id, item.kind]);

  const Icon = item.kind === "success" ? IconCheck : item.kind === "error" ? IconAlertCircle : IconInfoCircle;
  return <article className={`toast toast-${item.kind}`} role={item.kind === "error" ? "alert" : "status"}>
    <span className="toast-icon" aria-hidden="true"><Icon size={18} stroke={1.8}/></span>
    <span className="toast-copy"><strong>{t(item.title)}</strong>{item.description && <span>{t(item.description)}</span>}</span>
    <button className="toast-close" type="button" aria-label={t("Dismiss notification")} onClick={() => dismiss(item.id)}><IconX size={16}/></button>
  </article>;
}

export function useToast() {
  const value = useContext(ToastContext);
  if (!value) throw new Error("ToastProvider is required");
  return value;
}
