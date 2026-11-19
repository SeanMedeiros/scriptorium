"use client";

import { Download } from "lucide-react";
import Link from "next/link";
import { IiifButton } from "@/components/advanced";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { IngestBadge, JobsPanel, JobStatusBadge, RunRecognitionDialog } from "@/components/recognition";
import { Alert, Button, Card, CardHeader, PageHeader } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import { formatBytes, getAccessToken, type DocumentInfo, type Project } from "@/lib/api";
import { useResource } from "@/lib/hooks";

export default function DocumentPage() {
  const { id } = useParams<{ id: string }>();
  const { t } = useI18n();
  const doc = useResource<DocumentInfo>(`/documents/${id}`);
  const projectId = doc.data?.project_id ?? null;
  const project = useResource<Project>(projectId ? `/projects/${projectId}` : null);
  const [notice, setNotice] = useState<string | null>(null);
  const [jobsKey, setJobsKey] = useState(0);
  const { reload } = doc;

  useEffect(() => {
    if (doc.data?.ingest_status !== "pending") return;
    const h = setInterval(reload, 2000);
    return () => clearInterval(h);
  }, [doc.data?.ingest_status, reload]);

  if (doc.error) return <Alert>{t("common.notFound")}</Alert>;
  const d = doc.data;
  if (!d) return <p className="text-sm text-muted">{t("common.loading")}</p>;
  const pages = d.pages ?? [];
  const canRun = project.data?.permissions.includes("run_recognition") ?? false;

  async function downloadOriginal() {
    const res = await fetch(`/api/v1/documents/${d!.id}/original`, { headers: { Authorization: `Bearer ${getAccessToken()}` } });
    const blob = await res.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = d!.source?.original_filename ?? d!.title;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  return (
    <>
      <PageHeader
        eyebrow={
          <Link href={`/projects/${d.project_id}/collections/${d.collection_id}`} className="hover:text-ink">
            {d.project_title} / {d.collection_title}
          </Link>
        }
        title={d.title}
        subtitle={
          <span className="flex flex-wrap items-center gap-x-4 gap-y-1">
            <IngestBadge status={d.ingest_status} />
            <span>{t("documents.pages", { n: d.page_count })}</span>
            {d.archival_reference && <span className="font-mono text-xs">{d.archival_reference}</span>}
            {d.languages.length > 0 && <span className="font-mono text-xs">{d.languages.join(" · ")}</span>}
            {d.date_text && <span>{d.date_text}</span>}
          </span>
        }
        action={
          <div className="flex gap-2">
            <Button variant="secondary" onClick={downloadOriginal}>
              <Download /> {t("documents.downloadOriginal")}
            </Button>
            {project.data?.permissions.includes("export") && d.ingest_status === "ready" && <IiifButton documentId={d.id} />}
            {canRun && projectId && d.ingest_status === "ready" && (
              <RunRecognitionDialog
                projectId={projectId}
                pageIds={pages.map((p) => p.id)}
                doc={{ languages: d.languages, script_type: d.script_type, date_start: d.date_start, date_end: d.date_end }}
                onSubmitted={(n) => {
                  setNotice(t("recognition.submitted", { n }));
                  setJobsKey((k) => k + 1);
                  reload();
                }}
              />
            )}
          </div>
        }
      />
      {notice && (
        <div className="mb-4">
          <Alert tone="success">{notice}</Alert>
        </div>
      )}
      {d.ingest_error && <Alert>{d.ingest_error.message}</Alert>}
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
        <Card>
          <CardHeader title={t("recognition.pages")} />
          <ul className="grid grid-cols-2 gap-4 p-5 sm:grid-cols-3 xl:grid-cols-4" data-testid="page-grid">
            {pages.map((p) => (
              <li key={p.id}>
                <Link href={`/pages/${p.id}`} className="group block">
                  <div className="flex aspect-[3/4] items-center justify-center overflow-hidden rounded-[var(--radius-md)] border border-rule bg-sunken">
                    {p.image?.url ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img src={p.image.url} alt={t("page.title", { n: p.sequence })} className="h-full w-full object-contain" loading="lazy" />
                    ) : (
                      <span className="text-xs text-muted">{p.image?.mime_type}</span>
                    )}
                  </div>
                  <div className="mt-1.5 flex items-center justify-between text-xs">
                    <span className="text-ink-2 group-hover:text-accent">{t("page.title", { n: p.sequence })}</span>
                    {p.latest_job_status && <JobStatusBadge status={p.latest_job_status} />}
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        </Card>
        <Card className="self-start">
          <CardHeader title={t("page.provenance")} />
          <dl className="space-y-3 px-5 py-4 text-sm">
            <div>
              <dt className="text-xs text-muted">{t("documents.sha256")}</dt>
              <dd className="break-all font-mono text-[11px] text-ink-2">{d.source?.sha256}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted">File</dt>
              <dd className="text-ink-2">
                {d.source?.original_filename} · {d.source?.mime_type} · {d.source ? formatBytes(d.source.size_bytes) : ""}
              </dd>
            </div>
            {d.provenance && (
              <div>
                <dt className="text-xs text-muted">{t("documents.field.provenance")}</dt>
                <dd className="whitespace-pre-line text-ink-2">{d.provenance}</dd>
              </div>
            )}
            {d.notes && (
              <div>
                <dt className="text-xs text-muted">{t("documents.field.notes")}</dt>
                <dd className="whitespace-pre-line text-ink-2">{d.notes}</dd>
              </div>
            )}
          </dl>
        </Card>
      </div>
      {projectId && (
        <div className="mt-6">
          <JobsPanel key={jobsKey} projectId={projectId} documentId={d.id} canRun={canRun} />
        </div>
      )}
    </>
  );
}
