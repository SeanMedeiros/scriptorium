"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { format, LOCALES, MESSAGES, type Locale, type MessageKey } from "./messages";

type Ctx = {
  locale: Locale;
  setLocale: (l: Locale) => void;
  t: (key: MessageKey, vars?: Record<string, string | number>) => string;
  formatDate: (iso: string, withTime?: boolean) => string;
};

const I18nContext = createContext<Ctx | null>(null);
const STORAGE_KEY = "scriptorium.locale";

function detect(): Locale {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved && (LOCALES as readonly string[]).includes(saved)) return saved as Locale;
  } catch {
    /* storage unavailable */
  }
  return "en"; // English is the default interface language
}

export function I18nProvider({ children, initial }: { children: React.ReactNode; initial?: Locale }) {
  const [locale, setLocaleState] = useState<Locale>(initial ?? "en");

  useEffect(() => {
    if (!initial) setLocaleState(detect());
  }, [initial]);

  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);

  const setLocale = useCallback((l: Locale) => {
    setLocaleState(l);
    try {
      localStorage.setItem(STORAGE_KEY, l);
    } catch {
      /* ignore */
    }
  }, []);

  const value = useMemo<Ctx>(() => {
    const dict = MESSAGES[locale];
    const intlLocale = { en: "en-GB", pt: "pt-PT", fr: "fr-FR" }[locale];
    return {
      locale,
      setLocale,
      t: (key, vars) => format(dict[key] ?? MESSAGES.en[key] ?? key, vars),
      formatDate: (iso, withTime = true) =>
        new Intl.DateTimeFormat(intlLocale, withTime ? { dateStyle: "medium", timeStyle: "short" } : { dateStyle: "medium" }).format(
          new Date(iso),
        ),
    };
  }, [locale, setLocale]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): Ctx {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error("useI18n must be used inside I18nProvider");
  return ctx;
}
