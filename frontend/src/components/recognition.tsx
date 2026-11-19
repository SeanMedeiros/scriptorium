"use client";

import { Play, RotateCcw, Square } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { Alert, Badge, Button, Card, CardHeader, Dialog, DialogContent, DialogTrigger, EmptyState, Field, Select, Table, Td, Th } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import type { MessageKey } from "@/i18n/messages";
import { api, ApiError, type DocumentInfo, type JobInfo, type JobList, type JobStatus, type ModelVersionInfo } from "@/lib/api";
import { useResource } from "@/lib/hooks";

const TONE: Record<JobStatus, "neutral" | "accent" | "success" | "danger" | "warn"> = {
  queued: "neutral",
  processing: "accent",
  completed: "success",
  failed: "danger",
  cancelled: "warn",
};

export function JobStatusBadge({ status }: { status: JobStatus }) {
  const { t } = useI18n();
  return (
    <Badge tone={TONE[status]} data-status={status}>
      {t(`jobs.status.${status}` as MessageKey)}
    </Badge>
  );
}

const OPS = ["grayscale", "autocontrast", "deskew", "denoise", "binarize"] as const;

type Rec = {
  model_version_id: string;
  name: string;
  version: string;
  score: number;
  matches: { criterion: string; value: unknown; evidence: string }[];
  mismatches: { criterion: string }[];
  unknown: string[];
  limitations: string[];
  unicode_normalization: string | null;
  transcription_convention: string | null;
};

export type DocMeta = { languages: string[]; script_type: string; date_start: number | null; date_end: number | null };

function Recommendation({ doc, selected, onUse }: { doc: DocMeta; selected: string; onUse: (id: string) => void }) {
  const { t } = useI18n();
  const qs = new URLSearchParams();
  doc.languages.forEach((l) => qs.append("languages", l));
  qs.set("script_type", doc.script_type);
  if (doc.date_start != null) qs.set("period_start", String(doc.date_start));
  if (doc.date_end != null) qs.set("period_end", String(doc.date_end));
  const rec = useResource<{ items: Rec[] }>(`/model-registry/recommend?${qs}`);
  const criteria = [doc.languages.join(", ") || "—", doc.script_type, doc.date_start != null ? `${doc.date_start}–${doc.date_end ?? ""}` : null]
    .filter(Boolean)
    .join(" · ");
  if (!rec.data) return null;
  if (!rec.data.items.length) return <p className="text-xs text-muted">{t("recommend.none")}</p>;
  return (
    <div className="rounded-[var(--radius-md)] border border-rule bg-paper p-3" data-testid="recommendation">
      <div className="mb-2 text-xs font-medium uppercase tracking-[0.07em] text-muted">{t("recommend.title")}</div>
      <p className="mb-2 text-xs text-muted">{t("recommend.basedOn", { criteria })}</p>
      <ol className="space-y-2">
        {rec.data.items.map((r) => (
          <li key={r.model_version_id} className="text-[13px]">
            <div className="flex items-center justify-between gap-2">
              <span className="font-medium text-ink">
                {r.name} · {r.version}
              </span>
              <Button type="button" variant={selected === r.model_version_id ? "primary" : "secondary"} size="sm" onClick={() => onUse(r.model_version_id)}>
                {t("recommend.use")}
              </Button>
            </div>
            <div className="mt-1 flex flex-wrap gap-1">
              {r.matches.map((m) => (
                <Badge key={m.criterion} tone="success">
                  {m.criterion} {t("recommend.match")} ({m.evidence === "curated" ? t("recommend.curated") : t("recommend.source")})
                </Badge>
              ))}
              {r.mismatches.map((m) => (
                <Badge key={m.criterion} tone="danger">
                  {m.criterion} {t("recommend.mismatch")}
                </Badge>
              ))}
              {r.unknown.map((u) => (
                <Badge key={u}>
                  {u} {t("recommend.unknown")}
                </Badge>
              ))}
              {r.unicode_normalization && <Badge>{r.unicode_normalization}</Badge>}
            </div>
            {r.transcription_convention && <div className="mt-0.5 text-xs text-muted">{r.transcription_convention}</div>}
          </li>
        ))}
      </ol>
      <p className="mt-2 text-[11px] italic text-muted">{rec.data.items[0].limitations[0]}</p>
    </div>
  );
}

export function RunRecognitionDialog({
  projectId,
  pageIds,
  onSubmitted,
  doc,
}: {
  projectId: string;
  pageIds: string[];
  onSubmitted: (n: number) => void;
  doc?: DocMeta;
}) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [recModel, setRecModel] = useState<string>("");
  const models = useResource<ModelVersionInfo[]>(open ? "/models?usable=true" : null);
  const [mode, setMode] = useState<"segment_recognize" | "segment">("segment_recognize");
  const [ops, setOps] = useState<Record<string, boolean>>({ grayscale: true });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const recModels = models.data?.filter((m) => m.task === "recognition") ?? [];
  const segModels = models.data?.filter((m) => m.task === "segmentation") ?? [];

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    setBusy(true);
    setError(null);
    try {
      const res = await api<{ jobs: unknown[] }>(`/projects/${projectId}/recognition-jobs`, {
        method: "POST",
        json: {
          job_type: mode,
          page_ids: pageIds,
          recognition_model_version_id: mode === "segment" ? null : f.get("rec_model"),
          segmentation_model_version_id: f.get("seg_model") || null,
          text_direction: f.get("direction"),
          preprocessing: { operations: OPS.filter((o) => ops[o]).map((op) => ({ op })) },
        },
      });
      setOpen(false);
      onSubmitted(res.jobs.length);
    } catch (err) {
      setError(err instanceof ApiError ? err.body.message : t("common.error"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button disabled={pageIds.length === 0}>
          <Play /> {t("recognition.run")}
        </Button>
      </DialogTrigger>
      <DialogContent title={t("recognition.run")} description={t("recognition.run.description")}>
        <form onSubmit={submit} className="space-y-4">
          {error && <Alert>{error}</Alert>}
          <Field label={t("recognition.mode")} htmlFor="mode">
            <Select id="mode" value={mode} onChange={(e) => setMode(e.target.value as typeof mode)}>
              <option value="segment_recognize">{t("recognition.mode.segment_recognize")}</option>
              <option value="segment">{t("recognition.mode.segment")}</option>
            </Select>
          </Field>
          {mode !== "segment" &&
            (models.data && recModels.length === 0 ? (
              <Alert tone="warn">{t("recognition.noModels")}</Alert>
            ) : (
              <>
              {doc && <Recommendation doc={doc} selected={recModel} onUse={setRecModel} />}
              <Field label={t("recognition.model")} htmlFor="rec_model">
                <Select id="rec_model" name="rec_model" required value={recModel || recModels[0]?.id || ""} onChange={(e) => setRecModel(e.target.value)}>
                  {recModels.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.model_name} · {m.version}
                      {m.unicode_normalization ? ` · ${m.unicode_normalization}` : ""}
                    </option>
                  ))}
                </Select>
              </Field>
              </>
            ))}
          <Field label={t("recognition.segModel")} htmlFor="seg_model">
            <Select id="seg_model" name="seg_model">
              {segModels.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.model_name} · {m.version}
                </option>
              ))}
            </Select>
          </Field>
          <fieldset className="space-y-1.5">
            <legend className="mb-1 text-[13px] font-medium text-ink-2">{t("recognition.preprocessing")}</legend>
            {OPS.map((op) => (
              <label key={op} className="flex items-center gap-2 text-sm text-ink-2">
                <input
                  type="checkbox"
                  checked={!!ops[op]}
                  onChange={(e) => setOps({ ...ops, [op]: e.target.checked })}
                  className="accent-[var(--color-accent)]"
                />
                {t(`recognition.op.${op}` as MessageKey)}
              </label>
            ))}
          </fieldset>
          <Field label={t("recognition.direction")} htmlFor="direction">
            <Select id="direction" name="direction" defaultValue="horizontal-lr">
              <option value="horizontal-lr">horizontal-lr</option>
              <option value="horizontal-rl">horizontal-rl</option>
              <option value="vertical-lr">vertical-lr</option>
              <option value="vertical-rl">vertical-rl</option>
            </Select>
          </Field>
          <div className="flex items-center justify-between pt-2">
            <span className="text-sm text-muted">{t("recognition.allPages", { n: pageIds.length })}</span>
            <Button type="submit" disabled={busy || (mode !== "segment" && recModels.length === 0)}>
              {t("recognition.submit", { n: pageIds.length })}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function duration(j: JobInfo): string | null {
  if (!j.started_at || !j.completed_at) return null;
  const s = (new Date(j.completed_at).getTime() - new Date(j.started_at).getTime()) / 1000;
  return s < 60 ? `${s.toFixed(1)} s` : `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`;
}

export function JobsPanel({ projectId, documentId, canRun }: { projectId: string; documentId?: string; canRun: boolean }) {
  const { t, formatDate } = useI18n();
  const qs = new URLSearchParams({ limit: "200", ...(documentId ? { document_id: documentId } : {}) });
  const jobs = useResource<JobList>(`/projects/${projectId}/recognition-jobs?${qs}`);
  const [logJob, setLogJob] = useState<JobInfo | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const active = (jobs.data?.counts.queued ?? 0) + (jobs.data?.counts.processing ?? 0) > 0;
  const { reload } = jobs;

  useEffect(() => {
    if (!active) return;
    const id = setInterval(reload, 3000);
    return () => clearInterval(id);
  }, [active, reload]);

  async function act(j: JobInfo, action: "cancel" | "retry") {
    setErr(null);
    try {
      await api(`/recognition-jobs/${j.id}/${action}`, { method: "POST" });
      reload();
    } catch (e) {
      setErr(e instanceof ApiError ? e.body.message : t("common.error"));
    }
  }

  async function openLog(j: JobInfo) {
    setLogJob(await api<JobInfo>(`/recognition-jobs/${j.id}`));
  }

  return (
    <Card>
      <CardHeader
        title={t("jobs.title")}
        description={
          jobs.data && (
            <span className="flex flex-wrap gap-3" data-testid="job-counts">
              {(Object.keys(jobs.data.counts) as JobStatus[]).map((s) => (
                <span key={s}>
                  {t(`jobs.status.${s}` as MessageKey)}: <span className="tabular font-medium text-ink-2">{jobs.data!.counts[s]}</span>
                </span>
              ))}
            </span>
          )
        }
      />
      {err && <Alert>{err}</Alert>}
      {jobs.data && jobs.data.items.length === 0 && <EmptyState title={t("jobs.empty")} />}
      {jobs.data && jobs.data.items.length > 0 && (
        <Table data-testid="job-table">
          <thead>
            <tr>
              <Th>{t("documents.title")}</Th>
              <Th>{t("page.model")}</Th>
              <Th>Status</Th>
              <Th>{t("jobs.submitted")}</Th>
              <Th>{t("jobs.duration")}</Th>
              <Th />
            </tr>
          </thead>
          <tbody>
            {jobs.data.items.map((j) => (
              <tr key={j.id} data-job-id={j.id}>
                <Td>
                  <div className="text-ink">{j.document_title}</div>
                  <div className="text-xs text-muted">{t("jobs.page", { n: j.page_sequence ?? "?" })}</div>
                </Td>
                <Td className="text-xs">{j.recognition_model ?? t("recognition.mode.segment")}</Td>
                <Td>
                  <JobStatusBadge status={j.status} />
                  {j.status === "processing" && (
                    <div className="mt-1.5 h-1 w-28 overflow-hidden rounded bg-sunken" aria-label={`${Math.round(j.progress * 100)}%`}>
                      <div className="h-full bg-accent transition-all" style={{ width: `${Math.round(j.progress * 100)}%` }} />
                    </div>
                  )}
                  {j.stage && <div className="mt-1 text-[11px] text-muted">{j.stage}</div>}
                  {j.error && (
                    <div className="mt-1 max-w-xs text-xs text-danger">
                      <span className="font-mono">{j.error.code}</span> — {j.error.message}
                    </div>
                  )}
                </Td>
                <Td className="tabular whitespace-nowrap text-xs">{formatDate(j.submitted_at)}</Td>
                <Td className="tabular text-xs">{duration(j) ?? t("common.none")}</Td>
                <Td className="whitespace-nowrap text-right">
                  {j.status === "completed" && (
                    <Button variant="link" size="sm" asChild>
                      <Link href={`/pages/${j.page_id}`}>{t("jobs.viewResult")}</Link>
                    </Button>
                  )}
                  <Button variant="ghost" size="sm" onClick={() => openLog(j)}>
                    {t("jobs.log")}
                  </Button>
                  {canRun && (j.status === "queued" || j.status === "processing") && (
                    <Button variant="ghost" size="sm" onClick={() => act(j, "cancel")}>
                      <Square /> {t("jobs.cancel")}
                    </Button>
                  )}
                  {canRun && (j.status === "failed" || j.status === "cancelled") && (
                    <Button variant="ghost" size="sm" onClick={() => act(j, "retry")}>
                      <RotateCcw /> {t("jobs.retry")}
                    </Button>
                  )}
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
      <Dialog open={!!logJob} onOpenChange={(o) => !o && setLogJob(null)}>
        {logJob && (
          <DialogContent title={`${t("jobs.log")} · ${logJob.id.slice(0, 8)}`} className="max-w-3xl">
            <ol className="space-y-1 font-mono text-[12px]">
              {logJob.log?.map((e, i) => (
                <li key={i} className={e.level === "error" ? "text-danger" : "text-ink-2"}>
                  <span className="text-muted">{formatDate(e.ts)}</span> [{e.level}] {e.message}
                  {Object.entries(e)
                    .filter(([k]) => !["ts", "level", "message", "traceback", "software"].includes(k))
                    .map(([k, v]) => (
                      <span key={k} className="text-muted">
                        {" "}
                        {k}={typeof v === "object" ? JSON.stringify(v) : String(v)}
                      </span>
                    ))}
                </li>
              ))}
            </ol>
          </DialogContent>
        )}
      </Dialog>
    </Card>
  );
}

export function IngestBadge({ status }: { status: DocumentInfo["ingest_status"] }) {
  const { t } = useI18n();
  const tone = status === "ready" ? "success" : status === "failed" ? "danger" : "neutral";
  return <Badge tone={tone}>{t(`documents.ingest.${status}` as MessageKey)}</Badge>;
}

