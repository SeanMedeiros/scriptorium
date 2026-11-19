"use client";

import { useI18n } from "@/i18n/I18nProvider";
import type { AuditEntry } from "@/lib/api";

function describe(e: AuditEntry): string {
  const d = e.details as Record<string, unknown>;
  const name = (d.title as string) ?? (d.name as string) ?? (d.role as string) ?? "";
  return name ? `${e.action} · ${name}` : e.action;
}

export function ActivityList({ items }: { items: AuditEntry[] }) {
  const { t, formatDate } = useI18n();
  if (!items.length) return <p className="px-5 py-6 text-sm text-muted">{t("dashboard.noActivity")}</p>;
  return (
    <ol className="divide-y divide-rule">
      {items.map((e) => (
        <li key={e.id} className="flex items-baseline gap-4 px-5 py-2.5 text-sm">
          <time className="tabular w-36 shrink-0 text-xs text-muted" dateTime={e.created_at}>
            {formatDate(e.created_at)}
          </time>
          <span className="min-w-0 flex-1 truncate font-mono text-[12.5px] text-ink-2">{describe(e)}</span>
          <span className="truncate text-xs text-muted">{e.actor_email ?? t("common.none")}</span>
        </li>
      ))}
    </ol>
  );
}
