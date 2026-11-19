"use client";

import { Combine } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { BarChart, pct } from "@/components/research";
import { Alert, Badge, Button, Card, CardHeader, PageHeader, Select } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import { api, ApiError, type ComparisonInfo, type Project } from "@/lib/api";
import { diffChars } from "@/lib/diff";
import { useResource } from "@/lib/hooks";
import { cn } from "@/lib/utils";

function Diff({ base, text }: { base: string; text: string }) {
  const parts = useMemo(() => diffChars(base, text), [base, text]);
  return (
    <>
      {parts.map((p, i) =>
        p.op === "eq" ? (
          <span key={i}>{p.text}</span>
        ) : p.op === "ins" ? (
          <ins key={i} className="rounded-sm bg-accent-soft text-accent no-underline">
            {p.text}
          </ins>
        ) : (
          <del key={i} className="text-danger/70">
            {p.text}
          </del>
        ),
      )}
    </>
  );
}

export default function ComparisonView() {
  const { id } = useParams<{ id: string }>();
  const { t } = useI18n();
  const router = useRouter();
  const cmp = useResource<ComparisonInfo>(`/comparisons/${id}`);
  const c = cmp.data;
  const project = useResource<Project>(c ? `/projects/${c.project_id}` : null);
  const [primary, setPrimary] = useState<string | null>(null);
  const [pick, setPick] = useState<Record<string, string>>({}); // line key → model_version_id
  const [diffBase, setDiffBase] = useState<"gt" | string>("gt");
  const [err, setErr] = useState<string | null>(null);
  const running = c?.models.some((m) => m.job_status === "queued" || m.job_status === "processing");
  useEffect(() => {
    if (!running) return;
    const h = setInterval(() => cmp.reload(), 3000);
    return () => clearInterval(h);
  }, [running, cmp]);
  useEffect(() => {
    if (c && !primary) {
      const first = c.models.find((m) => m.recognition_id);
      if (first) setPrimary(first.model_version_id);
    }
    if (c && !c.ground_truth?.lines && diffBase === "gt") setDiffBase(c.models[0]?.model_version_id ?? "gt");
  }, [c, primary, diffBase]);

  if (cmp.error) return <Alert>{cmp.error.message}</Alert>;
  if (!c) return <p className="text-sm text-muted">{t("common.loading")}</p>;
  const done = c.models.filter((m) => m.recognition_id);
  const canCombine = project.data?.permissions.includes("transcribe") && done.length >= 2 && !running;
  const gt = c.ground_truth?.lines ?? null;
  const name = (mid: string) => {
    const m = c.models.find((x) => x.model_version_id === mid);
    return m ? `${m.model} ${m.version}` : mid;
  };

  async function combine() {
    setErr(null);
    const prim = c!.models.find((m) => m.model_version_id === primary)!;
    const selections: Record<string, string> = {};
    for (const [key, mid] of Object.entries(pick)) {
      const m = c!.models.find((x) => x.model_version_id === mid);
      if (m?.recognition_id && mid !== primary) selections[key] = m.recognition_id;
    }
    try {
      const r = await api<{ transcription_id: string }>(`/comparisons/${c!.id}/combine`, {
        method: "POST",
        json: { primary_recognition_id: prim.recognition_id, selections },
      });
      router.push(`/transcriptions/${r.transcription_id}`);
    } catch (e) {
      setErr(e instanceof ApiError ? e.body.message : String(e));
    }
  }

  return (
    <>
      <PageHeader
        eyebrow={
          <Link href={`/pages/${c.page_id}/compare`} className="hover:text-ink">
            {c.document_title} · p. {c.page_sequence}
          </Link>
        }
        title={t("cmp.title")}
        subtitle={t("cmp.sameInput", { seg: c.segmentation_source })}
      />
      {err && (
        <div className="mb-4">
          <Alert>{err}</Alert>
        </div>
      )}
      <div className="mb-5 grid gap-3" style={{ gridTemplateColumns: `repeat(${Math.min(c.models.length, 3)}, minmax(0, 1fr))` }}>
        {c.models.map((m) => (
          <Card key={m.model_version_id} className="p-4" data-testid="cmp-model" data-status={m.job_status ?? ""}>
            <div className="text-sm font-semibold text-ink">{m.model}</div>
            <div className="mb-2 text-xs text-muted">
              {m.version} · {m.doi ?? "—"} · {m.license ?? "—"}
            </div>
            {m.job_status !== "completed" ? (
              <Badge tone={m.job_status === "failed" ? "danger" : "accent"}>
                {t(`jobs.status.${m.job_status}` as never)} {m.progress !== null && m.job_status === "processing" ? `${Math.round(m.progress * 100)} %` : ""}
              </Badge>
            ) : (
              <dl className="grid grid-cols-2 gap-y-1 text-xs">
                <dt className="text-muted">{t("cmp.time")}</dt>
                <dd className="tabular">{((m.duration_ms ?? 0) / 1000).toFixed(1)} s</dd>
                <dt className="text-muted">{t("ev.lines")}</dt>
                <dd className="tabular">{m.line_count}</dd>
                <dt className="text-muted">Kraken</dt>
                <dd className="tabular">{m.kraken}</dd>
                {m.score ? (
                  <>
                    <dt className="text-muted">CER</dt>
                    <dd className="tabular font-semibold" data-testid="cmp-cer">
                      {pct(m.score.cer)}
                    </dd>
                    <dt className="text-muted">WER</dt>
                    <dd className="tabular">{pct(m.score.wer)}</dd>
                  </>
                ) : (
                  <dd className="col-span-2 text-muted">{t("cmp.noGt")}</dd>
                )}
              </dl>
            )}
            {m.job_error && <p className="mt-2 text-xs text-danger">{m.job_error.message}</p>}
          </Card>
        ))}
      </div>
      {done.some((m) => m.score) && (
        <Card className="mb-5">
          <CardHeader title={t("cmp.cerChart")} description={c.ground_truth?.note ?? undefined} />
          <div className="p-5">
            <BarChart label={t("cmp.cerChart")} rows={done.map((m) => ({ key: `${m.model} ${m.version}`, value: m.score?.cer ?? null }))} />
          </div>
        </Card>
      )}
      {c.pairwise.length > 0 && (
        <p className="mb-4 text-xs text-muted" data-testid="pairwise">
          {c.pairwise.map((p) => t("cmp.pairwise", { a: name(p.a), b: name(p.b), d: pct(p.normalised, 1), n: String(p.lines_differing) })).join(" · ")} — {t("cmp.pairwiseNote")}
        </p>
      )}
      <Card>
        <CardHeader
          title={t("cmp.lines")}
          description={t("cmp.lines.help")}
          action={
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <label className="flex items-center gap-2">
                {t("cmp.diffAgainst")}
                <Select value={diffBase} onChange={(e) => setDiffBase(e.target.value)} className="w-56" aria-label={t("cmp.diffAgainst")}>
                  {gt && <option value="gt">{t("cmp.groundTruth")}</option>}
                  {done.map((m) => (
                    <option key={m.model_version_id} value={m.model_version_id}>
                      {m.model} {m.version}
                    </option>
                  ))}
                </Select>
              </label>
              {canCombine && (
                <>
                  <label className="flex items-center gap-2">
                    {t("cmp.primary")}
                    <Select value={primary ?? ""} onChange={(e) => setPrimary(e.target.value)} className="w-56" aria-label={t("cmp.primary")}>
                      {done.map((m) => (
                        <option key={m.model_version_id} value={m.model_version_id}>
                          {m.model} {m.version}
                        </option>
                      ))}
                    </Select>
                  </label>
                  <Button size="sm" onClick={combine}>
                    <Combine /> {t("cmp.combine")}
                  </Button>
                </>
              )}
            </div>
          }
        />
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-rule text-left text-xs text-muted">
                <th className="w-10 px-3 py-2">#</th>
                {gt && <th className="px-3 py-2">{t("cmp.groundTruth")}</th>}
                {done.map((m) => (
                  <th key={m.model_version_id} className="px-3 py-2">
                    {m.model} {m.version}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {c.lines.map((ln) => {
                const base = diffBase === "gt" ? gt?.[ln.key] ?? "" : ln.outputs[diffBase]?.text ?? "";
                const chosen = pick[ln.key] ?? primary;
                return (
                  <tr key={ln.key} className="border-b border-rule/70 align-top" data-testid="cmp-line">
                    <td className="px-3 py-2 text-xs tabular text-muted">{ln.order_index + 1}</td>
                    {gt && <td className="px-3 py-2 font-serif text-ink-2">{gt[ln.key] ?? ""}</td>}
                    {done.map((m) => {
                      const o = ln.outputs[m.model_version_id];
                      const sel = chosen === m.model_version_id;
                      return (
                        <td key={m.model_version_id} className={cn("px-3 py-2 font-serif", sel && canCombine && "bg-accent-soft/40")}>
                          <label className="flex items-start gap-2">
                            {canCombine && (
                              <input
                                type="radio"
                                name={`l-${ln.key}`}
                                className="mt-1"
                                checked={sel}
                                aria-label={t("cmp.useLine", { n: String(ln.order_index + 1), m: `${m.model} ${m.version}` })}
                                onChange={() => setPick((p) => ({ ...p, [ln.key]: m.model_version_id }))}
                              />
                            )}
                            <span>{o ? diffBase === m.model_version_id ? o.text : <Diff base={base} text={o.text} /> : "—"}</span>
                          </label>
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="px-5 py-3 text-xs text-muted">{c.note}</p>
      </Card>
    </>
  );
}
