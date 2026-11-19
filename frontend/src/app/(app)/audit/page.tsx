"use client";

import { useState } from "react";
import { Alert, Button, Card, Input, PageHeader, Table, Td, Th } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import type { AuditEntry, Paged } from "@/lib/api";
import { useResource } from "@/lib/hooks";

const PAGE = 50;

export default function AuditPage() {
  const { t, formatDate } = useI18n();
  const [action, setAction] = useState("");
  const [offset, setOffset] = useState(0);
  const qs = new URLSearchParams({ limit: String(PAGE), offset: String(offset), ...(action ? { action } : {}) });
  const { data, error } = useResource<Paged<AuditEntry>>(`/audit-logs?${qs}`);

  return (
    <>
      <PageHeader title={t("audit.title")} subtitle={t("audit.note")} />
      <div className="mb-4 flex items-center gap-3">
        <Input
          className="max-w-xs font-mono text-xs"
          placeholder="project.create"
          aria-label={t("audit.action")}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              setOffset(0);
              setAction((e.target as HTMLInputElement).value.trim());
            }
          }}
        />
        {data && <span className="tabular text-sm text-muted">{data.total}</span>}
      </div>
      {error && <Alert>{error.message}</Alert>}
      <Card>
        <Table>
          <thead>
            <tr>
              <Th>{t("audit.when")}</Th>
              <Th>{t("audit.who")}</Th>
              <Th>{t("audit.action")}</Th>
              <Th>{t("audit.entity")}</Th>
              <Th>{t("audit.ip")}</Th>
            </tr>
          </thead>
          <tbody>
            {data?.items.map((e) => (
              <tr key={e.id}>
                <Td className="tabular whitespace-nowrap text-xs">{formatDate(e.created_at)}</Td>
                <Td className="text-xs">{e.actor_email ?? t("common.none")}</Td>
                <Td className="font-mono text-xs text-ink">{e.action}</Td>
                <Td className="font-mono text-[11px] text-muted">
                  {e.entity_type ? `${e.entity_type}:${e.entity_id?.slice(0, 8)}` : t("common.none")}
                  {Object.keys(e.details).length > 0 && <div className="mt-0.5 text-ink-2">{JSON.stringify(e.details)}</div>}
                </Td>
                <Td className="font-mono text-xs">{e.ip_address ?? t("common.none")}</Td>
              </tr>
            ))}
          </tbody>
        </Table>
        {data && data.items.length === 0 && <p className="px-5 py-6 text-sm text-muted">{t("audit.empty")}</p>}
        {data && data.total > PAGE && (
          <div className="flex justify-end gap-2 px-5 py-3">
            <Button variant="secondary" size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
              ←
            </Button>
            <Button variant="secondary" size="sm" disabled={offset + PAGE >= data.total} onClick={() => setOffset(offset + PAGE)}>
              →
            </Button>
          </div>
        )}
      </Card>
    </>
  );
}
