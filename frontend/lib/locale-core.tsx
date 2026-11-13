"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

const LOCALE_KEY = "procura.locale";

export type LocaleValue = { arabic: boolean; setArabic: (value: boolean) => void; t: (value: string) => string };
export const LocaleContext = createContext<LocaleValue | null>(null);

export function applyLocale(arabic: boolean) {
  const locale = arabic ? "ar" : "en";
  document.documentElement.lang = locale;
  document.documentElement.dir = arabic ? "rtl" : "ltr";
  document.cookie = `${LOCALE_KEY}=${locale}; Path=/; Max-Age=31536000; SameSite=Lax${location.protocol === "https:" ? "; Secure" : ""}`;
}

export function LocaleCoreProvider({ children, initialArabic = false }: { children: React.ReactNode; initialArabic?: boolean }) {
  const [isArabic, setIsArabic] = useState(initialArabic);
  const setArabic = useCallback((value: boolean) => {
    applyLocale(value);
    setIsArabic(value);
    try { window.localStorage.setItem(LOCALE_KEY, value ? "ar" : "en"); } catch { /* Cookies keep the preference when storage is unavailable. */ }
  }, []);
  useEffect(() => {
    let selected = initialArabic;
    try { const stored = window.localStorage.getItem(LOCALE_KEY); if (stored === "ar" || stored === "en") selected = stored === "ar"; } catch {}
    if (selected !== initialArabic) {
      applyLocale(selected);
      setIsArabic(selected);
    }
    const sync = (event: StorageEvent) => {
      if (event.key === LOCALE_KEY && (event.newValue === "ar" || event.newValue === "en")) {
        const value = event.newValue === "ar";
        applyLocale(value);
        setIsArabic(value);
      }
    };
    window.addEventListener("storage", sync);
    return () => window.removeEventListener("storage", sync);
  }, [initialArabic]);
  const value = useMemo<LocaleValue>(() => ({
    arabic: isArabic,
    setArabic,
    t: (text) => text,
  }), [isArabic, setArabic]);
  return <LocaleContext.Provider value={value}>{children}</LocaleContext.Provider>;
}

export function useLocaleCore() {
  const context = useContext(LocaleContext);
  if (!context) throw new Error("LocaleProvider is required");
  return context;
}
