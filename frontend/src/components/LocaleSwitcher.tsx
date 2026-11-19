"use client";

import { useI18n } from "@/i18n/I18nProvider";
import { LOCALE_NAMES, LOCALES, type Locale } from "@/i18n/messages";
import { cn } from "@/lib/utils";

export function LocaleSwitcher({ onChange, className }: { onChange?: (l: Locale) => void; className?: string }) {
  const { locale, setLocale } = useI18n();
  return (
    <div className={cn("flex gap-1 text-xs", className)} role="group" aria-label="Language">
      {LOCALES.map((l) => (
        <button
          key={l}
          type="button"
          lang={l}
          title={LOCALE_NAMES[l]}
          aria-pressed={l === locale}
          onClick={() => {
            setLocale(l);
            onChange?.(l);
          }}
          className={cn(
            "rounded px-1.5 py-0.5 uppercase tracking-wide",
            l === locale ? "bg-ink text-paper" : "text-muted hover:bg-sunken hover:text-ink",
          )}
        >
          {l}
        </button>
      ))}
    </div>
  );
}
