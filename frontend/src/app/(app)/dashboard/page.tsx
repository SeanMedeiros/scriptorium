"use client";

import Link from "next/link";
import { pct } from "@/components/research";
import { ActivityList } from "@/components/ActivityList";
import { Alert, Button, Card, CardHeader, EmptyState, PageHeader, Stat } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import { formatBytes, type Dashboard } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useResource } from "@/lib/hooks";

export default function DashboardPage() {
  const { t } = useI18n();
  const { user } = useAuth();
  const { data, error } = useResource<Dashboard>("/dashboard");

  return (
    <>
      <PageHeader eyebrow={t("dashboard.title")} title={t("dashboard.welcome", { name: user?.full_name ?? "" })} subtitle={t("dashboard.source")} />
      {error && <Alert>{error.message}</Alert>}
      {data && (
        <div className="space-y-6">
          <Card className="grid grid-cols-2 divide-x divide-rule md:grid-cols-4" data-testid="dashboard-stats">
            <Stat label={t("dashboard.projects")} value={data.projects} />
            <Stat label={t("dashboard.collections")} value={data.collections} />
            <Stat label={t("dashboard.researchers")} value={data.researchers} />
            <Stat label={t("dashboard.institutions")} value={data.institutions} />
          </Card>
          <Card className="grid grid-cols-2 divide-x divide-rule md:grid-cols-4 xl:grid-cols-7" data-testid="dashboard-processing">
            <Stat label={t("dashboard.documents")} value={data.documents} />
            <Stat label={t("dashboard.pages")} value={data.pages} />
            <Stat label={t("dashboard.processedPages")} value={data.processed_pages} />
            <Stat
              label={t("dashboard.lines")}
              value={data.recognised_lines}
              note={`${t("ws.status.corrected")} ${data.line_status.corrected} · ${t("ws.status.reviewed")} ${data.line_status.reviewed}`}
            />
            <Stat
              label={t("dashboard.jobs")}
              value={(data.jobs.queued ?? 0) + (data.jobs.processing ?? 0)}
              note={`${t("jobs.status.completed")} ${data.jobs.completed ?? 0} · ${t("jobs.status.failed")} ${data.jobs.failed ?? 0}`}
            />
            <Stat label={t("dashboard.models")} value={data.validated_models} />
            <Stat label={t("dashboard.storage")} value={formatBytes(data.storage_bytes)} />
          </Card>

          {data.projects === 0 && (
            <Card>
              <EmptyState
                title={t("dashboard.empty.title")}
                body={t("dashboard.empty.body")}
                action={
                  <Button asChild>
                    <Link href="/projects?new=1">{t("projects.new")}</Link>
                  </Button>
                }
              />
            </Card>
          )}

          <div className="grid gap-6 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
            <Card>
              <CardHeader title={t("dashboard.recent")} />
              <ActivityList items={data.recent_activity} />
            </Card>
            <Card data-testid="dashboard-quality">
              <CardHeader title={t("rep.quality")} description={t("dashboard.quality.note", { gt: String(data.quality?.ground_truth_pages ?? 0) })} />
              {data.quality && data.quality.by_model.length > 0 ? (
                <ul className="divide-y divide-rule text-sm">
                  {data.quality.by_model.map((g) => (
                    <li key={g.key} className="flex justify-between gap-3 px-5 py-2.5">
                      <span className="text-ink-2">{g.key}</span>
                      <span className="tabular text-right text-xs">
                        CER <strong>{pct(g.cer)}</strong> · WER {pct(g.wer)} · {g.pages} p.
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="px-5 py-4 text-sm text-muted">{t("ev.noResults.body")}</p>
              )}
            </Card>
          </div>
        </div>
      )}
    </>
  );
}
