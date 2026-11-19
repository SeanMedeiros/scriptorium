"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect } from "react";
import { EpochChart, TrainingStatusBadge, type TrainingJobInfo } from "@/components/advanced";
import { pct } from "@/components/research";
import { Alert, Button, Card, CardHeader, PageHeader, Stat, Table, Td, Th } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import { api, formatBytes, type Project } from "@/lib/api";
import { useResource } from "@/lib/hooks";

export default function TrainingJobPage() {
  const { id } = useParams<{ id: string }>();
  const { t, formatDate } = useI18n();
  const job = useResource<TrainingJobInfo>(`/training-jobs/${id}`);
  const j = job.data;
  const project = useResource<Project>(j ? `/projects/${j.project_id}` : null);
  const running = j && !["completed", "failed", "cancelled"].includes(j.status);
  const testPending = j?.evaluation_run_id && (!j.test_evaluation || (j.test_evaluation.pages ?? 0) > j.test_evaluation.pages_scored);
  useEffect(() => {
    if (!running && !testPending) return;
    const h = setInterval(() => job.reload(), 3000);
    return () => clearInterval(h);
  }, [running, testPending, job]);
  if (job.error) return <Alert>{job.error.message}</Alert>;
  if (!j) return <p className="text-sm text-muted">{t("common.loading")}</p>;
  const can = project.data?.permissions.includes("train_models");
  const best = j.epochs.reduce<number | null>((m, e) => (e.val_accuracy != null && (m == null || e.val_accuracy > m) ? e.val_accuracy : m), null);
  return (
    <>
      <PageHeader
        eyebrow={
          <Link href={`/projects/${j.project_id}`} className="hover:text-ink">
            {project.data?.title ?? "…"}
          </Link>
        }
        title={j.output_name}
        subtitle={
          <span className="flex flex-wrap items-center gap-3">
            <TrainingStatusBadge s={j.status} />
            {j.stage && <span className="text-xs text-muted">{j.stage}</span>}
            <span className="text-xs">
              {j.base_model ? t("tr.fineTuneOf", { base: j.base_model }) : t("tr.fromScratch")} · {j.dataset}
            </span>
          </span>
        }
        action={
          running && can ? (
            <Button variant="danger" size="sm" onClick={async () => (await api(`/training-jobs/${j.id}/cancel`, { method: "POST" }), job.reload())}>
              {t("jobs.cancel")}
            </Button>
          ) : null
        }
      />
      {j.error && (
        <div className="mb-4">
          <Alert>{j.error.message}</Alert>
        </div>
      )}
      <div className="mb-5 grid grid-cols-2 gap-3 md:grid-cols-5">
        <Stat label={t("tr.progress")} value={`${Math.round(j.progress * 100)} %`} note={t("tr.epochOf", { n: String(j.epochs.length), total: String(j.config.epochs) })} />
        <Stat label={t("tr.bestValAcc")} value={pct(best)} note={t("tr.valAcc.note")} />
        <Stat label={t("tr.testCer")} value={j.test_evaluation?.cer != null ? pct(j.test_evaluation.cer) : "—"} note={t("tr.testCer.note")} />
        <Stat label={t("tr.trainLines")} value={j.data_summary.train?.lines ?? "—"} note={`${j.data_summary.train?.pages ?? 0} p.`} />
        <Stat label={t("tr.valLines")} value={j.data_summary.validation?.lines ?? "—"} note={`${j.data_summary.validation?.pages ?? 0} p.`} />
      </div>
      <div className="grid gap-5 lg:grid-cols-2">
        <Card>
          <CardHeader title={t("tr.curve")} />
          <div className="p-5">
            <EpochChart epochs={j.epochs} />
            {j.epochs.length > 0 && (
              <Table>
                <thead>
                  <tr>
                    <Th>{t("tr.epoch")}</Th>
                    <Th>{t("tr.valAcc")}</Th>
                    <Th>{t("tr.valWordAcc")}</Th>
                    <Th>{t("tr.loss")}</Th>
                  </tr>
                </thead>
                <tbody>
                  {j.epochs.map((e) => (
                    <tr key={e.epoch} data-testid="epoch-row">
                      <Td className="tabular">{e.epoch + 1}</Td>
                      <Td className="tabular">{pct(e.val_accuracy)}</Td>
                      <Td className="tabular">{pct(e.val_word_accuracy)}</Td>
                      <Td className="tabular">{e.train_loss?.toFixed(3) ?? "—"}</Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            )}
          </div>
        </Card>
        <Card>
          <CardHeader title={t("tr.checkpoints")} description={t("tr.checkpoints.help")} />
          {j.checkpoints.length ? (
            <Table>
              <thead>
                <tr>
                  <Th>{t("tr.checkpoint")}</Th>
                  <Th>{t("tr.epoch")}</Th>
                  <Th>{t("tr.valAcc")}</Th>
                  <Th>{t("im.file")}</Th>
                  <Th />
                </tr>
              </thead>
              <tbody>
                {j.checkpoints.map((c) => (
                  <tr key={c.name} className={c.deleted ? "opacity-50" : ""} data-testid="checkpoint-row">
                    <Td className="font-mono text-xs">
                      {c.name}
                      {c.name === j.best_checkpoint && <span className="ml-2 text-accent">★</span>}
                    </Td>
                    <Td className="tabular">{c.epoch}</Td>
                    <Td className="tabular">{pct(c.val_accuracy)}</Td>
                    <Td className="text-xs">{c.deleted ? t("tr.deleted") : formatBytes(c.bytes)}</Td>
                    <Td>
                      {can && !c.deleted && j.status === "completed" && (
                        <div className="flex gap-1">
                          {c.name !== j.best_checkpoint && (
                            <Button size="sm" variant="secondary" onClick={async () => (await api(`/training-jobs/${j.id}/checkpoints/${c.name}/promote`, { method: "POST" }), job.reload())}>
                              {t("tr.promote")}
                            </Button>
                          )}
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={async () => {
                              if (!window.confirm(t("tr.deleteConfirm"))) return;
                              await api(`/training-jobs/${j.id}/checkpoints/${c.name}`, { method: "DELETE" });
                              job.reload();
                            }}
                          >
                            {t("common.delete")}
                          </Button>
                        </div>
                      )}
                    </Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          ) : (
            <p className="p-5 text-sm text-muted">{t("tr.noCheckpoints")}</p>
          )}
          {j.output_model && (
            <p className="border-t border-rule px-5 py-3 text-sm" data-testid="training-output">
              {t("tr.registered")}: <strong>{j.output_model}</strong>
            </p>
          )}
        </Card>
        <Card>
          <CardHeader title={t("tr.config")} description={t("tr.config.help")} />
          <dl className="grid grid-cols-[160px_minmax(0,1fr)] gap-x-4 gap-y-1 px-5 py-4 text-xs">
            {Object.entries(j.config).map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="text-muted">{k}</dt>
                <dd className="font-mono">{String(v)}</dd>
              </div>
            ))}
            {j.data_manifest?.software && (
              <div className="contents">
                <dt className="text-muted">software</dt>
                <dd className="font-mono">{Object.entries(j.data_manifest.software).map(([k, v]) => `${k} ${v}`).join(" · ")}</dd>
              </div>
            )}
            {j.data_manifest && (
              <div className="contents">
                <dt className="text-muted">{t("tr.excluded")}</dt>
                <dd className="font-mono">{j.data_manifest.excluded_lines}</dd>
              </div>
            )}
            <div className="contents">
              <dt className="text-muted">{t("tr.duration")}</dt>
              <dd className="font-mono">{j.duration_s ? `${Math.round(j.duration_s)} s` : "—"}</dd>
            </div>
          </dl>
        </Card>
        <Card>
          <CardHeader title={t("tr.log")} />
          <ol className="max-h-80 overflow-y-auto px-5 py-3 font-mono text-[11px]" data-testid="training-log">
            {(j.log ?? []).map((l, i) => (
              <li key={i} className={l.level === "error" ? "text-danger" : l.level === "warning" ? "text-warn" : "text-ink-2"}>
                {formatDate(l.ts)} — {l.message}
              </li>
            ))}
          </ol>
        </Card>
      </div>
    </>
  );
}
