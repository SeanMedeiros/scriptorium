"use client";

// Phase 6: training, institutional models & publication, annotation management, analytics, IIIF.

import { BrainCircuit, Download, FileJson, Plus, Upload } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { BarChart, pct } from "@/components/research";
import {
  Alert,
  Badge,
  Button,
  Card,
  CardHeader,
  Dialog,
  DialogContent,
  DialogTrigger,
  EmptyState,
  Field,
  Input,
  Select,
  Table,
  Td,
  Textarea,
  Th,
} from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import { api, ApiError, download, formatBytes, saveBlob, uploadFiles, type EvalDataset, type ModelVersionInfo, type Project } from "@/lib/api";
import { useResource } from "@/lib/hooks";

const errMsg = (e: unknown) => (e instanceof ApiError ? e.body.message : String(e));

export interface TrainingJobInfo {
  id: string;
  project_id: string;
  dataset_id: string;
  dataset: string | null;
  base_model: string | null;
  output_name: string;
  config: Record<string, unknown> & { epochs: number; quit: string; lrate: number; normalization: string | null; seed: number };
  status: "queued" | "preparing" | "training" | "registering" | "completed" | "failed" | "cancelled";
  progress: number;
  stage: string | null;
  current_epoch: number;
  epochs: { epoch: number; val_accuracy: number | null; val_word_accuracy: number | null; train_loss: number | null; at: string }[];
  checkpoints: { name: string; epoch: number; val_accuracy: number; bytes: number; deleted: boolean }[];
  best_checkpoint: string | null;
  output_model_version_id: string | null;
  output_model: string | null;
  evaluation_run_id: string | null;
  error: { code: string; message: string } | null;
  submitted_at: string;
  started_at: string | null;
  completed_at: string | null;
  duration_s: number | null;
  data_summary: Record<string, { pages: number; lines: number; chars: number }>;
  test_evaluation?: { pages_scored: number; pages: number | null; cer: number | null; wer: number | null };
  data_manifest?: { splits: Record<string, { pages: { gt_page_id: string; lines: number; page_xml_sha256: string }[]; lines: number }>; excluded_lines: number; software: Record<string, string>; normalization: string | null; seed: number };
  log?: { ts: string; level: string; message: string; [k: string]: unknown }[];
}

const STATUS_TONE: Record<string, "neutral" | "accent" | "success" | "danger" | "warn"> = {
  queued: "neutral",
  preparing: "accent",
  training: "accent",
  registering: "accent",
  completed: "success",
  failed: "danger",
  cancelled: "warn",
};

export function TrainingStatusBadge({ s }: { s: TrainingJobInfo["status"] }) {
  const { t } = useI18n();
  return (
    <Badge tone={STATUS_TONE[s]} data-testid="training-status">
      {t(`tr.status.${s}` as never)}
    </Badge>
  );
}

// ------------------------------------------------------------------------------- training
export function TrainingPanel({ project }: { project: Project }) {
  const { t, formatDate } = useI18n();
  const jobs = useResource<TrainingJobInfo[]>(`/projects/${project.id}/training-jobs`);
  const can = project.permissions.includes("train_models");
  // Keep refreshing while a job runs or its automatic test evaluation is still being scored.
  const active = jobs.data?.some(
    (j) =>
      !["completed", "failed", "cancelled"].includes(j.status) ||
      (j.status === "completed" && j.evaluation_run_id && (!j.test_evaluation || (j.test_evaluation.pages ?? 0) > j.test_evaluation.pages_scored)),
  );
  useEffect(() => {
    if (!active) return;
    const h = setInterval(() => jobs.reload(), 3000);
    return () => clearInterval(h);
  }, [active, jobs]);
  return (
    <div className="space-y-5">
      <Card>
        <CardHeader title={t("tr.jobs")} description={t("tr.jobs.help")} action={can && <NewTraining project={project} onDone={jobs.reload} />} />
        {jobs.data && jobs.data.length ? (
          <Table>
            <thead>
              <tr>
                <Th>{t("tr.output")}</Th>
                <Th>{t("tr.base")}</Th>
                <Th>{t("ev.dataset" as never)}</Th>
                <Th>{t("common.status")}</Th>
                <Th>{t("tr.epochs")}</Th>
                <Th>{t("tr.valAcc")}</Th>
                <Th>{t("tr.testCer")}</Th>
                <Th>{t("common.date")}</Th>
              </tr>
            </thead>
            <tbody>
              {jobs.data.map((j) => {
                const best = j.epochs.reduce((m, e) => Math.max(m, e.val_accuracy ?? 0), 0);
                return (
                  <tr key={j.id} data-testid="training-row" data-status={j.status}>
                    <Td>
                      <Link className="font-medium text-accent hover:underline" href={`/training/${j.id}`}>
                        {j.output_name}
                      </Link>
                      {j.output_model && <div className="text-xs text-muted">{j.output_model}</div>}
                    </Td>
                    <Td className="text-xs">{j.base_model ?? t("tr.fromScratch")}</Td>
                    <Td className="text-xs">{j.dataset}</Td>
                    <Td>
                      <TrainingStatusBadge s={j.status} />
                      {["preparing", "training", "registering"].includes(j.status) && <span className="ml-2 text-xs tabular text-muted">{Math.round(j.progress * 100)} %</span>}
                    </Td>
                    <Td className="tabular text-xs">
                      {j.epochs.length} / {j.config.quit === "fixed" ? j.config.epochs : `≤ ${j.config.epochs}`}
                    </Td>
                    <Td className="tabular">{j.epochs.length ? pct(best) : "—"}</Td>
                    <Td className="tabular" data-testid="training-test-cer">
                      {j.test_evaluation?.cer != null ? pct(j.test_evaluation.cer) : "—"}
                    </Td>
                    <Td className="text-xs">{formatDate(j.submitted_at)}</Td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
        ) : (
          <div className="p-5">
            <EmptyState title={t("tr.none")} body={t("tr.none.body")} />
          </div>
        )}
      </Card>
      <InstitutionalModels project={project} refreshKey={jobs.data?.filter((j) => j.status === "completed").length ?? 0} />
    </div>
  );
}

function NewTraining({ project, onDone }: { project: Project; onDone: () => void }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const datasets = useResource<EvalDataset[]>(open ? `/projects/${project.id}/evaluation-datasets` : null);
  const models = useResource<ModelVersionInfo[]>(open ? `/models?task=recognition&usable=true&project_id=${project.id}` : null);
  const [dsId, setDsId] = useState("");
  const [base, setBase] = useState("");
  const [name, setName] = useState("");
  const [epochs, setEpochs] = useState(10);
  const [quit, setQuit] = useState<"early" | "fixed">("early");
  const [lrate, setLrate] = useState(0.0001);
  const [seed, setSeed] = useState(42);
  const [evalTest, setEvalTest] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const ds = datasets.data?.find((d) => d.id === dsId);
  async function go() {
    setErr(null);
    try {
      await api(`/projects/${project.id}/training-jobs`, {
        method: "POST",
        json: { dataset_id: dsId, base_model_version_id: base || null, output_name: name, epochs, quit, lrate, seed, evaluate_test: evalTest },
      });
      setOpen(false);
      onDone();
    } catch (e) {
      setErr(errMsg(e));
    }
  }
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm">
          <BrainCircuit /> {t("tr.new")}
        </Button>
      </DialogTrigger>
      <DialogContent title={t("tr.new")} description={t("tr.new.help")}>
        <div className="space-y-3">
          {err && <Alert>{err}</Alert>}
          <Field htmlFor="tr-ds" label={t("tr.dataset")} hint={ds ? t("tr.splitsHint", { train: String(ds.splits.train ?? 0), validation: String(ds.splits.validation ?? 0), test: String(ds.splits.test ?? 0) }) : undefined}>
            <Select id="tr-ds" value={dsId} onChange={(e) => setDsId(e.target.value)}>
              <option value="">—</option>
              {datasets.data?.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field htmlFor="tr-base" label={t("tr.base")} hint={t("tr.base.hint")}>
            <Select id="tr-base" value={base} onChange={(e) => setBase(e.target.value)}>
              <option value="">{t("tr.fromScratch")}</option>
              {models.data?.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.model_name} · {m.version}
                </option>
              ))}
            </Select>
          </Field>
          <Field htmlFor="tr-name" label={t("tr.output")} hint={t("tr.output.hint")}>
            <Input id="tr-name" value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field htmlFor="tr-quit" label={t("tr.stop")}>
              <Select id="tr-quit" value={quit} onChange={(e) => setQuit(e.target.value as "early" | "fixed")}>
                <option value="early">{t("tr.stop.early")}</option>
                <option value="fixed">{t("tr.stop.fixed")}</option>
              </Select>
            </Field>
            <Field htmlFor="tr-epochs" label={quit === "fixed" ? t("tr.epochs") : t("tr.maxEpochs")}>
              <Input id="tr-epochs" type="number" min={1} max={200} value={epochs} onChange={(e) => setEpochs(Number(e.target.value))} />
            </Field>
            <Field htmlFor="tr-lr" label={t("tr.lrate")}>
              <Input id="tr-lr" type="number" step="0.00001" value={lrate} onChange={(e) => setLrate(Number(e.target.value))} />
            </Field>
            <Field htmlFor="tr-seed" label={t("tr.seed")}>
              <Input id="tr-seed" type="number" value={seed} onChange={(e) => setSeed(Number(e.target.value))} />
            </Field>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={evalTest} onChange={(e) => setEvalTest(e.target.checked)} />
            {t("tr.evalTest")}
          </label>
          <p className="text-xs text-muted">{t("tr.splitRule")}</p>
          <Button onClick={go} disabled={!dsId || name.trim().length < 3}>
            {t("tr.start")}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

/** Validation accuracy per epoch (line chart, exact values listed beside). */
export function EpochChart({ epochs }: { epochs: TrainingJobInfo["epochs"] }) {
  const { t } = useI18n();
  if (!epochs.length) return <p className="text-sm text-muted">{t("tr.noEpochs")}</p>;
  const W = 520;
  const H = 180;
  const P = 36;
  const vals = epochs.map((e) => e.val_accuracy ?? 0);
  const lo = Math.max(0, Math.min(...vals) - 0.02);
  const hi = Math.min(1, Math.max(...vals) + 0.02);
  const x = (i: number) => P + (epochs.length === 1 ? (W - 2 * P) / 2 : (i * (W - 2 * P)) / (epochs.length - 1));
  const y = (v: number) => H - P / 2 - ((v - lo) / Math.max(hi - lo, 0.001)) * (H - P);
  return (
    <figure data-testid="epoch-chart">
      <svg role="img" aria-label={t("tr.chart")} viewBox={`0 0 ${W} ${H}`} className="w-full max-w-[560px]">
        <line x1={P} x2={W - P / 2} y1={H - P / 2} y2={H - P / 2} stroke="var(--color-rule-strong)" />
        <text x={4} y={y(hi) + 4} fontSize="10" fill="var(--color-muted)">
          {pct(hi, 1)}
        </text>
        <text x={4} y={y(lo) + 4} fontSize="10" fill="var(--color-muted)">
          {pct(lo, 1)}
        </text>
        <polyline fill="none" stroke="var(--color-accent)" strokeWidth="2" points={vals.map((v, i) => `${x(i)},${y(v)}`).join(" ")} />
        {vals.map((v, i) => (
          <g key={i}>
            <circle cx={x(i)} cy={y(v)} r="3.5" fill="var(--color-accent)" />
            <text x={x(i)} y={H - 4} fontSize="10" textAnchor="middle" fill="var(--color-muted)">
              {epochs[i].epoch + 1}
            </text>
          </g>
        ))}
      </svg>
      <figcaption className="text-xs text-muted">{t("tr.chart.caption")}</figcaption>
    </figure>
  );
}

// ------------------------------------------------------------------- institutional models
interface InstModel {
  id: string;
  name: string;
  slug: string;
  version: string;
  source: string;
  status: string;
  sha256: string | null;
  size_bytes: number | null;
  license_id: string | null;
  unicode_normalization: string | null;
  kraken_version_validated: string | null;
  is_active: boolean;
  training_job_id: string | null;
  provenance: { kind?: string; base_model?: { name: string; version: string; doi: string | null; license: string | null } | null; attribution_note?: string | null; validation_accuracy?: number | null };
  install_log: { message: string }[];
  publications: { id: string; title: string; approved_at: string | null; package_sha256: string | null; package_bytes: number | null; published_doi: string | null }[];
}

export function InstitutionalModels({ project, refreshKey = 0 }: { project: Project; refreshKey?: number }) {
  const { t } = useI18n();
  const list = useResource<InstModel[]>(`/projects/${project.id}/institutional-models`);
  const reloadList = list.reload;
  useEffect(() => {
    if (refreshKey) reloadList(); // a training job finished: a new version was registered
  }, [refreshKey, reloadList]);
  const can = project.permissions.includes("train_models");
  const isPI = project.permissions.includes("manage_members");
  const pending = list.data?.some((m) => ["installing", "installed", "downloading"].includes(m.status));
  useEffect(() => {
    if (!pending) return;
    const h = setInterval(() => list.reload(), 2500);
    return () => clearInterval(h);
  }, [pending, list]);
  return (
    <Card data-testid="institutional-models">
      <CardHeader title={t("im.title")} description={t("im.help")} action={can && <UploadModel project={project} onDone={list.reload} />} />
      {list.data && list.data.length ? (
        <Table>
          <thead>
            <tr>
              <Th>{t("ev.model")}</Th>
              <Th>{t("im.origin")}</Th>
              <Th>{t("common.status")}</Th>
              <Th>{t("im.file")}</Th>
              <Th>{t("im.publication")}</Th>
              <Th />
            </tr>
          </thead>
          <tbody>
            {list.data.map((m) => (
              <tr key={m.id} data-testid="inst-model-row" data-status={m.status} className="align-top">
                <Td>
                  <div className="font-medium">
                    {m.name} · {m.version} {m.is_active && <Badge tone="accent">{t("im.active")}</Badge>}
                  </div>
                  <div className="text-xs text-muted">
                    {m.unicode_normalization ?? "—"} · {m.license_id ?? t("im.noLicence")}
                  </div>
                </Td>
                <Td className="max-w-[320px] text-xs">
                  {m.provenance.kind === "fine-tuned" && m.provenance.base_model ? (
                    <>
                      {t("im.fineTuned", { base: `${m.provenance.base_model.name} ${m.provenance.base_model.version}` })}
                      {m.provenance.validation_accuracy != null && <div className="text-muted">{t("im.valAcc", { v: pct(m.provenance.validation_accuracy) })}</div>}
                      {m.provenance.attribution_note && <div className="mt-1 text-warn">{m.provenance.attribution_note}</div>}
                      {m.training_job_id && (
                        <Link className="text-accent hover:underline" href={`/training/${m.training_job_id}`}>
                          {t("im.trainingJob")}
                        </Link>
                      )}
                    </>
                  ) : (
                    t(m.provenance.kind === "trained from scratch" ? "im.kind.scratch" : "im.kind.uploaded")
                  )}
                </Td>
                <Td>
                  <Badge tone={m.status === "validated" ? "success" : m.status === "incompatible" || m.status === "failed" ? "danger" : "accent"}>{m.status}</Badge>
                  {m.status === "incompatible" && <p className="mt-1 max-w-[240px] text-xs text-danger">{m.install_log.at(-1)?.message}</p>}
                  {m.kraken_version_validated && <div className="text-xs text-muted">Kraken {m.kraken_version_validated}</div>}
                </Td>
                <Td className="text-xs">
                  {formatBytes(m.size_bytes ?? 0)}
                  <div className="font-mono text-[10px] text-muted">{m.sha256?.slice(0, 16)}…</div>
                </Td>
                <Td className="text-xs">
                  {m.publications.length ? (
                    m.publications.map((p) => (
                      <div key={p.id}>
                        {p.published_doi ? (
                          <a className="text-accent hover:underline" href={`https://doi.org/${p.published_doi}`} target="_blank" rel="noreferrer">
                            {p.published_doi}
                          </a>
                        ) : p.approved_at ? (
                          <Button size="sm" variant="ghost" onClick={async () => saveBlob(await download(`/model-publications/${p.id}/package`))}>
                            <Download /> {t("im.package")}
                          </Button>
                        ) : (
                          isPI && <ApprovePublication pubId={p.id} title={p.title} onDone={list.reload} />
                        )}
                        {!p.approved_at && !isPI && <span className="text-muted">{t("im.awaitingApproval")}</span>}
                      </div>
                    ))
                  ) : (
                    can && m.status === "validated" && <DraftPublication model={m} onDone={list.reload} />
                  )}
                </Td>
                <Td>
                  {m.status === "validated" && (
                    <div className="flex flex-col gap-1">
                      {!m.is_active && can && (
                        <Button size="sm" variant="secondary" onClick={async () => (await api(`/institutional-models/${m.id}/activate`, { method: "POST" }), list.reload())}>
                          {t("im.activate")}
                        </Button>
                      )}
                      <Button size="sm" variant="ghost" onClick={async () => saveBlob(await download(`/institutional-models/${m.id}/download`))}>
                        <Download /> {t("im.weights")}
                      </Button>
                    </div>
                  )}
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      ) : (
        <p className="p-5 text-sm text-muted">{t("im.none")}</p>
      )}
    </Card>
  );
}

function UploadModel({ project, onDone }: { project: Project; onDone: () => void }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [version, setVersion] = useState("1");
  const [license, setLicense] = useState("");
  const [norm, setNorm] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function go() {
    setErr(null);
    setBusy(true);
    const fd = new FormData();
    fd.set("name", name);
    fd.set("version", version);
    if (license) fd.set("license_id", license);
    if (norm) fd.set("unicode_normalization", norm);
    fd.set("file", file!);
    try {
      await uploadFiles(`/projects/${project.id}/institutional-models`, fd);
      setOpen(false);
      onDone();
    } catch (e) {
      setErr(errMsg(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm" variant="secondary">
          <Upload /> {t("im.upload")}
        </Button>
      </DialogTrigger>
      <DialogContent title={t("im.upload")} description={t("im.upload.help")}>
        <div className="space-y-3">
          {err && <Alert>{err}</Alert>}
          <Field htmlFor="im-name" label={t("common.name")}>
            <Input id="im-name" value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <div className="grid grid-cols-3 gap-3">
            <Field htmlFor="im-version" label={t("im.version")}>
              <Input id="im-version" value={version} onChange={(e) => setVersion(e.target.value)} />
            </Field>
            <Field htmlFor="im-licence" label={t("prov.licence")} hint="SPDX, e.g. cc-by-4.0">
              <Input id="im-licence" value={license} onChange={(e) => setLicense(e.target.value)} />
            </Field>
            <Field htmlFor="im-norm" label="Unicode">
              <Select id="im-norm" value={norm} onChange={(e) => setNorm(e.target.value)}>
                <option value="">—</option>
                {["NFC", "NFD", "NFKC", "NFKD"].map((x) => (
                  <option key={x}>{x}</option>
                ))}
              </Select>
            </Field>
          </div>
          <Field htmlFor="im-file" label={t("im.file")} hint={t("im.file.hint")}>
            <Input id="im-file" type="file" accept=".mlmodel,.safetensors" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          </Field>
          <Button onClick={go} disabled={busy || !file || name.trim().length < 3}>
            <Upload /> {busy ? t("im.uploading") : t("im.upload")}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function DraftPublication({ model, onDone }: { model: InstModel; onDone: () => void }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState(`${model.name} ${model.version}`);
  const [desc, setDesc] = useState("");
  const [authors, setAuthors] = useState("");
  const [license, setLicense] = useState(model.provenance.base_model?.license ?? model.license_id ?? "cc-by-4.0");
  const [kw, setKw] = useState("");
  const [err, setErr] = useState<string | null>(null);
  async function go() {
    setErr(null);
    try {
      const list = authors
        .split("\n")
        .map((l) => l.trim())
        .filter(Boolean)
        .map((l) => {
          const [name, affiliation, orcid] = l.split(";").map((x) => x.trim());
          return { name, affiliation: affiliation || null, orcid: orcid || null };
        });
      await api(`/institutional-models/${model.id}/publications`, {
        method: "POST",
        json: { title, description: desc, authors: list, license_id: license, keywords: kw.split(",").map((x) => x.trim()).filter(Boolean) },
      });
      setOpen(false);
      onDone();
    } catch (e) {
      setErr(errMsg(e));
    }
  }
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm" variant="ghost">
          <Plus /> {t("im.prepare")}
        </Button>
      </DialogTrigger>
      <DialogContent title={t("im.prepare")} description={t("im.prepare.help")}>
        <div className="space-y-3">
          {err && <Alert>{err}</Alert>}
          <Field htmlFor="pub-title" label={t("im.pubTitle")}>
            <Input id="pub-title" value={title} onChange={(e) => setTitle(e.target.value)} />
          </Field>
          <Field htmlFor="pub-desc" label={t("projects.field.description")}>
            <Textarea id="pub-desc" rows={4} value={desc} onChange={(e) => setDesc(e.target.value)} />
          </Field>
          <Field htmlFor="pub-authors" label={t("im.authors")} hint={t("im.authors.hint")}>
            <Textarea id="pub-authors" rows={3} value={authors} onChange={(e) => setAuthors(e.target.value)} />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field htmlFor="pub-licence" label={t("prov.licence")}>
              <Input id="pub-licence" value={license} onChange={(e) => setLicense(e.target.value)} />
            </Field>
            <Field htmlFor="pub-kw" label={t("im.keywords")}>
              <Input id="pub-kw" value={kw} onChange={(e) => setKw(e.target.value)} />
            </Field>
          </div>
          <Button onClick={go} disabled={title.length < 5 || desc.length < 20 || !authors.trim()}>
            {t("im.saveDraft")}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function ApprovePublication({ pubId, title, onDone }: { pubId: string; title: string; onDone: () => void }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [statement, setStatement] = useState("");
  const [err, setErr] = useState<string | null>(null);
  async function go() {
    setErr(null);
    try {
      await api(`/model-publications/${pubId}/approve`, { method: "POST", json: { statement } });
      setOpen(false);
      onDone();
    } catch (e) {
      setErr(errMsg(e));
    }
  }
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm" variant="secondary">
          {t("im.approve")}
        </Button>
      </DialogTrigger>
      <DialogContent title={t("im.approve")} description={title}>
        <div className="space-y-3">
          {err && <Alert>{err}</Alert>}
          <p className="text-sm text-ink-2">{t("im.approve.help")}</p>
          <Field htmlFor="pub-statement" label={t("im.statement")}>
            <Textarea id="pub-statement" rows={3} value={statement} onChange={(e) => setStatement(e.target.value)} />
          </Field>
          <Button onClick={go} disabled={statement.trim().length < 10}>
            {t("im.approveBuild")}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

// ------------------------------------------------------------------------------ annotations
interface AnnotationRow {
  id: string;
  kind: string;
  body: string;
  author: string | null;
  created_at: string;
  resolved_at: string | null;
  transcription_id: string;
  page_sequence: number;
  document_title: string;
  line_number: number | null;
}

export function AnnotationsPanel({ project }: { project: Project }) {
  const { t, formatDate } = useI18n();
  const [kind, setKind] = useState("");
  const [status, setStatus] = useState("open");
  const [q, setQ] = useState("");
  const params = new URLSearchParams({ ...(kind ? { kind } : {}), ...(status ? { status } : {}), ...(q ? { q } : {}) });
  const data = useResource<{ items: AnnotationRow[]; counts: { open: number; resolved: number } }>(`/projects/${project.id}/annotations?${params}`);
  return (
    <Card data-testid="annotations-panel">
      <CardHeader
        title={t("an.title")}
        description={data.data ? t("an.counts", { open: String(data.data.counts.open), resolved: String(data.data.counts.resolved) }) : undefined}
        action={
          project.permissions.includes("export") && (
            <Button size="sm" variant="secondary" onClick={async () => saveBlob({ ...(await download(`/projects/${project.id}/annotations/w3c`)), filename: `annotations-${project.slug ?? project.id}.jsonld` })}>
              <FileJson /> {t("an.w3c")}
            </Button>
          )
        }
      />
      <div className="flex flex-wrap gap-3 border-b border-rule px-5 py-3 text-sm">
        <Select aria-label={t("an.kind")} value={kind} onChange={(e) => setKind(e.target.value)} className="w-44">
          <option value="">{t("an.allKinds")}</option>
          {["comment", "query", "palaeography"].map((k) => (
            <option key={k} value={k}>
              {t(`an.kind.${k}` as never)}
            </option>
          ))}
        </Select>
        <Select aria-label={t("common.status")} value={status} onChange={(e) => setStatus(e.target.value)} className="w-40">
          <option value="open">{t("an.open")}</option>
          <option value="resolved">{t("an.resolved")}</option>
          <option value="">{t("an.all")}</option>
        </Select>
        <Input aria-label={t("common.search")} placeholder={t("common.search")} value={q} onChange={(e) => setQ(e.target.value)} className="w-64" />
      </div>
      {data.data && data.data.items.length ? (
        <Table>
          <thead>
            <tr>
              <Th>{t("an.kind")}</Th>
              <Th>{t("an.text")}</Th>
              <Th>{t("ev.page")}</Th>
              <Th>{t("an.author")}</Th>
              <Th>{t("common.date")}</Th>
            </tr>
          </thead>
          <tbody>
            {data.data.items.map((a) => (
              <tr key={a.id} data-testid="annotation-row">
                <Td>
                  <Badge tone={a.kind === "query" ? "warn" : "neutral"}>{t(`an.kind.${a.kind}` as never)}</Badge>
                  {a.resolved_at && <Badge tone="success">{t("an.resolved")}</Badge>}
                </Td>
                <Td className="max-w-[420px]">{a.body}</Td>
                <Td className="text-xs">
                  <Link className="text-accent hover:underline" href={`/transcriptions/${a.transcription_id}`}>
                    {a.document_title} · p. {a.page_sequence}
                    {a.line_number ? ` · l. ${a.line_number}` : ""}
                  </Link>
                </Td>
                <Td className="text-xs">{a.author ?? "—"}</Td>
                <Td className="text-xs">{formatDate(a.created_at)}</Td>
              </tr>
            ))}
          </tbody>
        </Table>
      ) : (
        <p className="p-5 text-sm text-muted">{t("an.none")}</p>
      )}
    </Card>
  );
}

// ------------------------------------------------------------------------------ analytics
interface Analytics {
  results: number;
  models: string[];
  confusions: { ref: string; hyp: string; count: number }[];
  deletions: { char: string; count: number }[];
  insertions: { char: string; count: number }[];
  by_line_length: { bucket: string; lines: number; chars: number; cer: number | null }[];
  calibration: { bin: string; lines: number; chars: number; mean_confidence: number; measured_char_accuracy: number | null }[];
  expected_calibration_error: number | null;
  note: string;
}

function Reliability({ bins }: { bins: Analytics["calibration"] }) {
  const { t } = useI18n();
  const S = 240;
  const P = 30;
  const sc = (v: number) => P + v * (S - P - 8);
  const sy = (v: number) => S - P - v * (S - P - 8);
  const maxN = Math.max(...bins.map((b) => b.lines), 1);
  return (
    <figure data-testid="reliability">
      <svg role="img" aria-label={t("ax.reliability")} viewBox={`0 0 ${S} ${S}`} className="w-full max-w-[300px]">
        <line x1={sc(0)} y1={sy(0)} x2={sc(1)} y2={sy(1)} stroke="var(--color-rule-strong)" strokeDasharray="4 3" />
        <line x1={sc(0)} y1={sy(0)} x2={sc(1)} y2={sy(0)} stroke="var(--color-ink-2)" />
        <line x1={sc(0)} y1={sy(0)} x2={sc(0)} y2={sy(1)} stroke="var(--color-ink-2)" />
        {[0, 0.5, 1].map((v) => (
          <g key={v}>
            <text x={sc(v)} y={S - P + 14} fontSize="9" textAnchor="middle" fill="var(--color-muted)">
              {v}
            </text>
            <text x={P - 4} y={sy(v) + 3} fontSize="9" textAnchor="end" fill="var(--color-muted)">
              {v}
            </text>
          </g>
        ))}
        {bins.map(
          (b) =>
            b.measured_char_accuracy != null && (
              <circle key={b.bin} cx={sc(b.mean_confidence)} cy={sy(b.measured_char_accuracy)} r={3 + (7 * b.lines) / maxN} fill="var(--color-accent)" fillOpacity="0.7" />
            ),
        )}
        <text x={S / 2} y={S - 2} fontSize="9" textAnchor="middle" fill="var(--color-ink-2)">
          {t("ax.meanConf")}
        </text>
      </svg>
      <figcaption className="text-xs text-muted">{t("ax.reliability.caption")}</figcaption>
    </figure>
  );
}

export function AnalyticsPanel({ project }: { project: Project }) {
  const { t } = useI18n();
  const models = useResource<{ groups: { key: string; models: string[] }[]; rows: { model: string; model_version_id: string }[] }>(`/projects/${project.id}/evaluation/results?group_by=model`);
  const opts = Array.from(new Map((models.data?.rows ?? []).map((r) => [r.model_version_id, r.model])).entries());
  const [mv, setMv] = useState("");
  useEffect(() => {
    if (!mv && opts.length) setMv(opts[0][0]);
  }, [opts, mv]);
  const a = useResource<Analytics>(mv ? `/projects/${project.id}/analytics?model_version_id=${mv}` : null);
  if (!opts.length) return null;
  const d = a.data;
  return (
    <Card data-testid="analytics">
      <CardHeader
        title={t("ax.title")}
        description={t("ax.help")}
        action={
          <Select aria-label={t("ev.model")} value={mv} onChange={(e) => setMv(e.target.value)} className="w-72">
            {opts.map(([id, name]) => (
              <option key={id} value={id}>
                {name}
              </option>
            ))}
          </Select>
        }
      />
      {d && (
        <div className="grid gap-6 p-5 lg:grid-cols-3">
          <div>
            <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">{t("ax.confusions")}</h4>
            <Table>
              <thead>
                <tr>
                  <Th>{t("ax.ref")}</Th>
                  <Th>{t("ax.hyp")}</Th>
                  <Th>n</Th>
                </tr>
              </thead>
              <tbody>
                {d.confusions.slice(0, 15).map((c, i) => (
                  <tr key={i} data-testid="confusion-row">
                    <Td className="font-serif text-base">{c.ref}</Td>
                    <Td className="font-serif text-base">{c.hyp}</Td>
                    <Td className="tabular">{c.count}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
            <p className="mt-2 text-xs text-muted">
              {t("ax.deleted")}: {d.deletions.slice(0, 10).map((x) => `${x.char} (${x.count})`).join(" · ") || "—"}
              <br />
              {t("ax.inserted")}: {d.insertions.slice(0, 10).map((x) => `${x.char} (${x.count})`).join(" · ") || "—"}
            </p>
          </div>
          <div>
            <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">{t("ax.byLength")}</h4>
            <BarChart label={t("ax.byLength")} rows={d.by_line_length.map((b) => ({ key: `${b.bucket} (${b.lines})`, value: b.cer }))} />
          </div>
          <div>
            <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">{t("ax.reliability")}</h4>
            {d.calibration.length ? <Reliability bins={d.calibration} /> : <p className="text-sm text-muted">{t("ax.noConf")}</p>}
            <p className="mt-2 text-xs" data-testid="ece">
              ECE: <strong className="tabular">{d.expected_calibration_error != null ? d.expected_calibration_error.toFixed(3) : "—"}</strong>
            </p>
            <p className="mt-1 text-xs text-muted">{d.note}</p>
          </div>
        </div>
      )}
    </Card>
  );
}

export function IiifButton({ documentId }: { documentId: string }) {
  const { t } = useI18n();
  const [err, setErr] = useState<string | null>(null);
  return (
    <>
      <Button
        variant="secondary"
        size="sm"
        onClick={async () => {
          try {
            const d = await download(`/documents/${documentId}/iiif/manifest.json`);
            saveBlob({ ...d, filename: `manifest-${documentId.slice(0, 8)}.json` });
          } catch (e) {
            setErr(errMsg(e));
          }
        }}
      >
        <FileJson /> {t("iiif.manifest")}
      </Button>
      {err && <span className="text-xs text-danger">{err}</span>}
    </>
  );
}

export function TrainingDataExportButton({ datasetId, projectId }: { datasetId: string; projectId: string }) {
  const { t } = useI18n();
  const [msg, setMsg] = useState<string | null>(null);
  return (
    <span className="flex items-center gap-2">
      <Button
        size="sm"
        variant="ghost"
        onClick={async () => {
          try {
            await api(`/evaluation-datasets/${datasetId}/training-data-export`, { method: "POST", json: {} });
            setMsg(t("tde.started"));
          } catch (e) {
            setMsg(errMsg(e));
          }
        }}
      >
        <Download /> {t("tde.button")}
      </Button>
      {msg && (
        <span className="text-xs text-muted" data-testid="tde-msg" data-project={projectId}>
          {msg}
        </span>
      )}
    </span>
  );
}
