"use client";

// Phase 5 research tools: ground truth & evaluation, reports, exports, provenance.
// Every number shown comes from the API; error rates appear only where ground truth exists.

import { Download, FileCheck2, FileWarning, FlaskConical, Plus, ShieldCheck, Upload } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
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
  Stat,
  Table,
  Td,
  Textarea,
  Th,
} from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import {
  api,
  ApiError,
  download,
  formatBytes,
  saveBlob,
  uploadFiles,
  type Collection,
  type DocumentInfo,
  type Download as Dl,
  type EvalDataset,
  type EvalGroup,
  type EvalResults,
  type EvalRun,
  type ExportJob,
  type GroundTruthPage,
  type Methodology,
  type ModelVersionInfo,
  type Project,
  type ProjectReport,
} from "@/lib/api";
import { useResource } from "@/lib/hooks";
import { TrainingDataExportButton } from "@/components/advanced";

export const pct = (v: number | null | undefined, digits = 2) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(digits)} %`);
const errMsg = (e: unknown) => (e instanceof ApiError ? e.body.message : String(e));

// ------------------------------------------------------------------------------- bar chart
/** Horizontal bars with the exact value printed; the table next to it carries the same numbers. */
export function BarChart({ rows, label, testId }: { rows: { key: string; value: number | null; note?: string }[]; label: string; testId?: string }) {
  const max = Math.max(0.0001, ...rows.map((r) => r.value ?? 0));
  const h = 26;
  const W = 640;
  const L = 220;
  return (
    <figure data-testid={testId} className="overflow-x-auto">
      <svg role="img" aria-label={label} viewBox={`0 0 ${W} ${rows.length * h + 8}`} className="w-full max-w-[680px]" style={{ minWidth: 420 }}>
        {rows.map((r, i) => {
          const w = r.value === null ? 0 : ((W - L - 90) * r.value) / max;
          return (
            <g key={r.key} transform={`translate(0 ${i * h + 4})`}>
              <text x={L - 8} y={h / 2 + 4} textAnchor="end" fontSize="11" fill="var(--color-ink-2)">
                {r.key.length > 34 ? r.key.slice(0, 33) + "…" : r.key}
              </text>
              <rect x={L} y={5} width={Math.max(w, 1)} height={h - 10} fill="var(--color-accent)" rx="2" />
              <text x={L + w + 6} y={h / 2 + 4} fontSize="11" fill="var(--color-ink)" className="tabular">
                {r.value === null ? "—" : pct(r.value)}
                {r.note ? ` · ${r.note}` : ""}
              </text>
            </g>
          );
        })}
      </svg>
      <figcaption className="sr-only">{label}</figcaption>
    </figure>
  );
}

// -------------------------------------------------------------------------- methodology
export function MethodologyFields({ value, onChange }: { value: Omit<Methodology, "version">; onChange: (m: Omit<Methodology, "version">) => void }) {
  const { t } = useI18n();
  const set = (k: keyof Omit<Methodology, "version">, v: unknown) => onChange({ ...value, [k]: v });
  return (
    <fieldset className="space-y-2 rounded border border-rule p-3 text-sm">
      <legend className="px-1 text-xs font-medium text-muted">{t("ev.methodology")}</legend>
      <label className="flex items-center gap-2">
        <span className="w-48 text-ink-2">{t("ev.m.unicode")}</span>
        <Select value={value.unicode} onChange={(e) => set("unicode", e.target.value)} className="w-32" aria-label={t("ev.m.unicode")}>
          {["NFC", "NFD", "NFKC", "none"].map((u) => (
            <option key={u}>{u}</option>
          ))}
        </Select>
      </label>
      {(["casefold", "remove_punctuation", "remove_combining_marks", "exclude_gap_lines"] as const).map((k) => (
        <label key={k} className="flex items-center gap-2">
          <input type="checkbox" checked={value[k]} onChange={(e) => set(k, e.target.checked)} />
          <span>{t(`ev.m.${k}`)}</span>
        </label>
      ))}
      <p className="text-xs text-muted">{t("ev.m.help")}</p>
    </fieldset>
  );
}

const DEFAULT_METHOD = { unicode: "NFC", casefold: false, remove_punctuation: false, remove_combining_marks: false, exclude_gap_lines: true } as const;

// ------------------------------------------------------------------------- evaluation tab
export function EvaluationPanel({ project }: { project: Project }) {
  const { t } = useI18n();
  const datasets = useResource<EvalDataset[]>(`/projects/${project.id}/evaluation-datasets`);
  const [sel, setSel] = useState<string | null>(null);
  const canCurate = project.permissions.includes("review");
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (!sel && datasets.data?.length) setSel(datasets.data[0].id);
  }, [datasets.data, sel]);

  async function create() {
    try {
      const d = await api<EvalDataset>(`/projects/${project.id}/evaluation-datasets`, { method: "POST", json: { name, description: desc || null } });
      setOpen(false);
      setName("");
      setDesc("");
      await datasets.reload();
      setSel(d.id);
    } catch (e) {
      setErr(errMsg(e));
    }
  }

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader
          title={t("ev.datasets")}
          description={t("ev.datasets.help")}
          action={
            canCurate && (
              <Dialog open={open} onOpenChange={setOpen}>
                <DialogTrigger asChild>
                  <Button size="sm">
                    <Plus /> {t("ev.newDataset")}
                  </Button>
                </DialogTrigger>
                <DialogContent title={t("ev.newDataset")}>
                  <div className="space-y-3">
                    {err && <Alert>{err}</Alert>}
                    <Field htmlFor="rs-f1" label={t("common.name")}>
                      <Input id="rs-f1" value={name} onChange={(e) => setName(e.target.value)} />
                    </Field>
                    <Field htmlFor="rs-f2" label={t("projects.field.description")}>
                      <Textarea id="rs-f2" value={desc} onChange={(e) => setDesc(e.target.value)} />
                    </Field>
                    <Button onClick={create} disabled={!name.trim()}>
                      {t("common.create")}
                    </Button>
                  </div>
                </DialogContent>
              </Dialog>
            )
          }
        />
        {datasets.data && datasets.data.length === 0 ? (
          <div className="p-5">
            <EmptyState title={t("ev.noDatasets")} body={t("ev.noDatasets.body")} />
          </div>
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>{t("common.name")}</Th>
                <Th>{t("ev.pages")}</Th>
                <Th>{t("ev.splits")}</Th>
                <Th>{t("ev.lines")}</Th>
              </tr>
            </thead>
            <tbody>
              {datasets.data?.map((d) => (
                <tr key={d.id} className={d.id === sel ? "bg-accent-soft/50" : ""}>
                  <Td>
                    <button className="text-left font-medium text-accent hover:underline" onClick={() => setSel(d.id)}>
                      {d.name}
                    </button>
                  </Td>
                  <Td className="tabular">{d.pages}</Td>
                  <Td className="tabular text-xs">
                    {(["train", "validation", "test"] as const).map((s) => `${t(`ev.split.${s}`)} ${d.splits[s] ?? 0}`).join(" · ")}
                  </Td>
                  <Td className="tabular">{d.lines}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
      {sel && <DatasetDetail key={sel} datasetId={sel} project={project} onChange={datasets.reload} />}
      <ResultsPanel project={project} />
    </div>
  );
}

function DatasetDetail({ datasetId, project, onChange }: { datasetId: string; project: Project; onChange: () => void }) {
  const { t, formatDate } = useI18n();
  const ds = useResource<EvalDataset>(`/evaluation-datasets/${datasetId}`);
  const runs = useResource<EvalRun[]>(`/evaluation-datasets/${datasetId}/runs`);
  const canCurate = project.permissions.includes("review");
  const canRun = project.permissions.includes("run_recognition");
  const running = runs.data?.some((r) => r.status === "running");
  useEffect(() => {
    if (!running) return;
    const h = setInterval(() => runs.reload(), 3000);
    return () => clearInterval(h);
  }, [running, runs]);
  const reload = () => {
    ds.reload();
    runs.reload();
    onChange();
  };

  async function withdraw(g: GroundTruthPage) {
    const reason = window.prompt(t("ev.withdrawReason"));
    if (reason === null) return;
    await api(`/ground-truth/${g.id}/withdraw`, { method: "POST", json: { reason } });
    reload();
  }

  const d = ds.data;
  if (!d) return null;
  return (
    <Card data-testid="dataset-detail">
      <CardHeader
        title={d.name}
        description={d.description ?? undefined}
        action={
          <div className="flex gap-2">
            {project.permissions.includes("export") && d.pages > 0 && <TrainingDataExportButton datasetId={d.id} projectId={project.id} />}
            {canCurate && <AddGroundTruth dataset={d} project={project} onDone={reload} />}
            {canRun && <StartRun dataset={d} onDone={reload} />}
          </div>
        }
      />
      {d.ground_truth && d.ground_truth.length > 0 ? (
        <Table>
          <thead>
            <tr>
              <Th>{t("ev.page")}</Th>
              <Th>{t("ev.split")}</Th>
              <Th>{t("ev.source")}</Th>
              <Th>{t("ev.lines")}</Th>
              <Th>Unicode</Th>
              <Th>{t("ev.note")}</Th>
              <Th />
            </tr>
          </thead>
          <tbody>
            {d.ground_truth.map((g) => (
              <tr key={g.id} className={g.withdrawn ? "opacity-50" : ""} data-testid="gt-row">
                <Td>
                  <Link className="text-accent hover:underline" href={`/pages/${g.page_id}`}>
                    {g.document_title} · p. {g.page_sequence}
                  </Link>
                </Td>
                <Td>
                  <Badge tone={g.split === "test" ? "accent" : g.split === "train" ? "warn" : "neutral"}>{t(`ev.split.${g.split}` as never)}</Badge>
                </Td>
                <Td className="text-xs">
                  {g.source === "transcription" ? t("ev.src.transcription", { n: String(g.source_details.revision) }) : `${g.source.toUpperCase()} · ${String(g.source_details.filename ?? "")}`}
                </Td>
                <Td className="tabular">{g.line_count}</Td>
                <Td className="font-mono text-xs">{g.unicode_form ?? "—"}</Td>
                <Td className="max-w-[320px] whitespace-pre-line text-xs text-muted">{g.withdrawn ? t("ev.withdrawn") : g.note || "—"}</Td>
                <Td>
                  {canCurate && !g.withdrawn && (
                    <Button size="sm" variant="ghost" onClick={() => withdraw(g)}>
                      {t("ev.withdraw")}
                    </Button>
                  )}
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      ) : (
        <div className="p-5">
          <EmptyState title={t("ev.noGt")} body={t("ev.noGt.body")} />
        </div>
      )}
      {runs.data && runs.data.length > 0 && (
        <div className="border-t border-rule">
          <h3 className="px-5 pt-4 text-sm font-semibold text-ink">{t("ev.runs")}</h3>
          <Table>
            <thead>
              <tr>
                <Th>{t("ev.model")}</Th>
                <Th>{t("ev.split")}</Th>
                <Th>{t("common.status")}</Th>
                <Th>CER</Th>
                <Th>WER</Th>
                <Th>{t("ev.chars")}</Th>
                <Th>{t("ev.methodology")}</Th>
                <Th>{t("common.date")}</Th>
              </tr>
            </thead>
            <tbody>
              {runs.data.map((r) => (
                <tr key={r.id} data-testid="run-row" data-status={r.status}>
                  <Td>{r.model}</Td>
                  <Td>{t(`ev.split.${r.split}` as never)}</Td>
                  <Td>
                    {r.status === "completed" ? <Badge tone="success">{t("ev.run.done", { n: String(r.scored_pages), total: String(r.page_count) })}</Badge> : <Badge tone="accent">{t("ev.run.running", { n: String(r.scored_pages), total: String(r.page_count) })}</Badge>}
                    {(r.jobs.failed ?? 0) > 0 && <Badge tone="danger">{t("ev.run.failed", { n: String(r.jobs.failed) })}</Badge>}
                  </Td>
                  <Td className="tabular font-medium" data-testid="run-cer">{pct(r.aggregate?.cer)}</Td>
                  <Td className="tabular">{pct(r.aggregate?.wer)}</Td>
                  <Td className="tabular text-xs">{r.aggregate ? `${r.aggregate.char_errors} / ${r.aggregate.ref_chars}` : "—"}</Td>
                  <Td className="font-mono text-[11px] text-muted">{methodLabel(r.methodology)}</Td>
                  <Td className="text-xs">{formatDate(r.created_at)}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </div>
      )}
    </Card>
  );
}

export function methodLabel(m: Methodology) {
  const f = [m.unicode, m.casefold && "casefold", m.remove_punctuation && "−punct", m.remove_combining_marks && "−marks", !m.exclude_gap_lines && "+gaps"].filter(Boolean);
  return `${m.version?.replace("scriptorium-metrics/", "v") ?? ""} ${f.join(" ")}`;
}

function AddGroundTruth({ dataset, project, onDone }: { dataset: EvalDataset; project: Project; onDone: () => void }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<"import" | "transcription">("import");
  const [split, setSplit] = useState("test");
  const [note, setNote] = useState("");
  const [colId, setColId] = useState("");
  const [docId, setDocId] = useState("");
  const [pageId, setPageId] = useState("");
  const [txId, setTxId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const cols = useResource<Collection[]>(open ? `/projects/${project.id}/collections` : null);
  const docs = useResource<{ items: DocumentInfo[] }>(colId ? `/projects/${project.id}/collections/${colId}/documents?limit=500` : null);
  const doc = useResource<DocumentInfo>(docId ? `/documents/${docId}` : null);
  const txs = useResource<{ id: string; model: string | null; head_revision: number; origin: string }[]>(pageId && mode === "transcription" ? `/pages/${pageId}/transcriptions` : null);

  useEffect(() => {
    if (open) {
      setErr(null);
      setPageId("");
      setFile(null);
      setTxId("");
      setNote("");
    }
  }, [open]);

  async function submit() {
    setErr(null);
    setBusy(true);
    try {
      if (mode === "import") {
        const fd = new FormData();
        fd.set("page_id", pageId);
        fd.set("split", split);
        if (note) fd.set("note", note);
        fd.set("file", file!);
        await uploadFiles(`/evaluation-datasets/${dataset.id}/ground-truth/import`, fd);
      } else {
        await api(`/evaluation-datasets/${dataset.id}/ground-truth/from-transcription`, { method: "POST", json: { transcription_id: txId, split, note: note || null } });
      }
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
          <Upload /> {t("ev.addGt")}
        </Button>
      </DialogTrigger>
      <DialogContent title={t("ev.addGt")} description={t("ev.addGt.help")}>
        <div className="space-y-3">
          {err && <Alert>{err}</Alert>}
          <div className="flex gap-4 text-sm">
            <label className="flex items-center gap-2">
              <input type="radio" checked={mode === "import"} onChange={() => setMode("import")} /> {t("ev.mode.import")}
            </label>
            <label className="flex items-center gap-2">
              <input type="radio" checked={mode === "transcription"} onChange={() => setMode("transcription")} /> {t("ev.mode.transcription")}
            </label>
          </div>
          <Field htmlFor="rs-f3" label={t("projects.tab.collections")}>
            <Select id="rs-f3" value={colId} onChange={(e) => (setColId(e.target.value), setDocId(""), setPageId(""))} aria-label={t("projects.tab.collections")}>
              <option value="">—</option>
              {cols.data?.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.title}
                </option>
              ))}
            </Select>
          </Field>
          <Field htmlFor="rs-f4" label={t("ev.document")}>
            <Select id="rs-f4" value={docId} onChange={(e) => (setDocId(e.target.value), setPageId(""))} aria-label={t("ev.document")}>
              <option value="">—</option>
              {docs.data?.items.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.title}
                </option>
              ))}
            </Select>
          </Field>
          <Field htmlFor="rs-f5" label={t("ev.page")}>
            <Select id="rs-f5" value={pageId} onChange={(e) => setPageId(e.target.value)} aria-label={t("ev.page")}>
              <option value="">—</option>
              {(doc.data?.id === docId ? doc.data?.pages ?? [] : []).map((p) => (
                <option key={p.id} value={p.id}>
                  {p.sequence}
                  {p.label ? ` (${p.label})` : ""}
                </option>
              ))}
            </Select>
          </Field>
          {mode === "import" ? (
            <Field htmlFor="rs-f6" label={t("ev.file")} hint={t("ev.file.hint")}>
              <Input id="rs-f6" type="file" accept=".xml,application/xml,text/xml" onChange={(e) => setFile(e.target.files?.[0] ?? null)} aria-label={t("ev.file")} />
            </Field>
          ) : (
            <Field htmlFor="rs-f7" label={t("ev.transcription")} hint={t("ev.transcription.hint")}>
              <Select id="rs-f7" value={txId} onChange={(e) => setTxId(e.target.value)} aria-label={t("ev.transcription")}>
                <option value="">—</option>
                {txs.data?.map((x) => (
                  <option key={x.id} value={x.id}>
                    {x.model ?? "—"} · r{x.head_revision}
                    {x.origin === "combined" ? ` · ${t("cmp.combined")}` : ""}
                  </option>
                ))}
              </Select>
            </Field>
          )}
          <Field htmlFor="rs-f8" label={t("ev.split")} hint={t("ev.split.hint")}>
            <Select id="rs-f8" value={split} onChange={(e) => setSplit(e.target.value)} aria-label={t("ev.split")}>
              {(["test", "validation", "train"] as const).map((s) => (
                <option key={s} value={s}>
                  {t(`ev.split.${s}`)}
                </option>
              ))}
            </Select>
          </Field>
          <Field htmlFor="rs-f9" label={t("ev.note")}>
            <Textarea id="rs-f9" value={note} onChange={(e) => setNote(e.target.value)} rows={2} />
          </Field>
          <Button onClick={submit} disabled={busy || !pageId || (mode === "import" ? !file : !txId)}>
            {t("ev.addGt")}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function StartRun({ dataset, onDone }: { dataset: EvalDataset; onDone: () => void }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const models = useResource<ModelVersionInfo[]>(open ? `/models?task=recognition&usable=true` : null);
  const [chosen, setChosen] = useState<string[]>([]);
  const [split, setSplit] = useState("test");
  const [method, setMethod] = useState<Omit<Methodology, "version">>({ ...DEFAULT_METHOD });
  const [err, setErr] = useState<string | null>(null);
  async function go() {
    try {
      await api(`/evaluation-datasets/${dataset.id}/runs`, { method: "POST", json: { model_version_ids: chosen, split, normalization: method } });
      setOpen(false);
      onDone();
    } catch (e) {
      setErr(errMsg(e));
    }
  }
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm" disabled={dataset.pages === 0}>
          <FlaskConical /> {t("ev.run")}
        </Button>
      </DialogTrigger>
      <DialogContent title={t("ev.run")} description={t("ev.run.help")}>
        <div className="space-y-3">
          {err && <Alert>{err}</Alert>}
          <fieldset className="space-y-1 text-sm">
            <legend className="mb-1 text-xs font-medium text-muted">{t("ev.models")}</legend>
            {models.data?.map((m) => (
              <label key={m.id} className="flex items-center gap-2">
                <input type="checkbox" checked={chosen.includes(m.id)} onChange={(e) => setChosen((c) => (e.target.checked ? [...c, m.id] : c.filter((x) => x !== m.id)))} />
                {m.model_name} · {m.version}
              </label>
            ))}
          </fieldset>
          <Field htmlFor="rs-f10" label={t("ev.split")}>
            <Select id="rs-f10" value={split} onChange={(e) => setSplit(e.target.value)} aria-label={t("ev.split")}>
              <option value="test">{t("ev.split.test")}</option>
              <option value="validation">{t("ev.split.validation")}</option>
            </Select>
          </Field>
          <MethodologyFields value={method} onChange={setMethod} />
          <Button onClick={go} disabled={!chosen.length}>
            {t("ev.run")}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

const DIMENSIONS = ["model", "document", "page", "collection", "language", "period", "script_type", "document_type", "split", "dataset"] as const;

function ResultsPanel({ project }: { project: Project }) {
  const { t } = useI18n();
  const [by, setBy] = useState<(typeof DIMENSIONS)[number]>("model");
  const res = useResource<EvalResults>(`/projects/${project.id}/evaluation/results?group_by=${by}`);
  const data = res.data;
  return (
    <Card data-testid="eval-results">
      <CardHeader
        title={t("ev.results")}
        description={t("ev.results.help")}
        action={
          <label className="flex items-center gap-2 text-sm">
            {t("ev.groupBy")}
            <Select value={by} onChange={(e) => setBy(e.target.value as typeof by)} className="w-44" aria-label={t("ev.groupBy")}>
              {DIMENSIONS.map((d) => (
                <option key={d} value={d}>
                  {t(`ev.dim.${d}`)}
                </option>
              ))}
            </Select>
          </label>
        }
      />
      {!data || data.rows.length === 0 ? (
        <div className="p-5">
          <EmptyState title={t("ev.noResults")} body={t("ev.noResults.body")} />
        </div>
      ) : (
        <div className="space-y-4 p-5">
          <BarChart testId="cer-chart" label={t("ev.chart", { dim: t(`ev.dim.${by}`) })} rows={data.groups.map((g) => ({ key: g.key, value: g.cer, note: `${g.pages} p.` }))} />
          <GroupTable groups={data.groups} />
          <p className="text-xs text-muted">{t("ev.methodNote", { v: data.methodology_version })}</p>
        </div>
      )}
    </Card>
  );
}

export function GroupTable({ groups }: { groups: EvalGroup[] }) {
  const { t } = useI18n();
  return (
    <Table>
      <thead>
        <tr>
          <Th>{t("ev.group")}</Th>
          <Th>{t("ev.pages")}</Th>
          <Th>CER</Th>
          <Th>WER</Th>
          <Th>{t("ev.charAcc")}</Th>
          <Th>{t("ev.wordAcc")}</Th>
          <Th>{t("ev.chars")}</Th>
          <Th>{t("ev.align")}</Th>
        </tr>
      </thead>
      <tbody>
        {groups.map((g) => (
          <tr key={g.key} data-testid="group-row">
            <Td className="font-medium">
              {g.key}
              {g.mixed_methodology && (
                <Badge tone="warn" className="ml-2">
                  {t("ev.mixed")}
                </Badge>
              )}
            </Td>
            <Td className="tabular">{g.pages}</Td>
            <Td className="tabular font-medium">{pct(g.cer)}</Td>
            <Td className="tabular">{pct(g.wer)}</Td>
            <Td className="tabular">{pct(g.char_accuracy)}</Td>
            <Td className="tabular">{pct(g.word_accuracy)}</Td>
            <Td className="tabular text-xs">
              {g.char_errors} / {g.ref_chars}
            </Td>
            <Td className="text-xs">{g.alignments.map((a) => t(`ev.align.${a}` as never)).join(", ")}</Td>
          </tr>
        ))}
      </tbody>
    </Table>
  );
}

// ---------------------------------------------------------------------------- report tab
export function ReportPanel({ project }: { project: Project }) {
  const { t, formatDate } = useI18n();
  const rep = useResource<ProjectReport>(`/projects/${project.id}/report`);
  const r = rep.data;
  if (!r) return <p className="text-sm text-muted">{t("common.loading")}</p>;
  const c = r.counts;
  return (
    <div className="space-y-5" data-testid="report">
      <div className="flex justify-end">
        <Button size="sm" variant="secondary" onClick={async () => saveBlob(await download(`/projects/${project.id}/report?format=csv`))}>
          <Download /> CSV
        </Button>
      </div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label={t("rep.documents")} value={c.documents} />
        <Stat label={t("rep.pages")} value={`${c.processed_pages} / ${c.pages}`} note={t("rep.processed")} />
        <Stat label={t("rep.revisions")} value={c.revisions} />
        <Stat label={t("rep.reviewed")} value={pct(r.lines.reviewed_share, 1)} note={`${r.lines.reviewed} / ${r.lines.total}`} />
        <Stat label={t("rep.uncertain")} value={r.lines.uncertain} />
        <Stat label={t("rep.illegible")} value={r.lines.illegible} />
        <Stat label={t("rep.gt")} value={c.ground_truth_pages} />
        <Stat label={t("rep.exports")} value={c.exports} />
      </div>
      <Card>
        <CardHeader title={t("rep.models")} />
        {r.models.length ? (
          <Table>
            <thead>
              <tr>
                <Th>{t("ev.model")}</Th>
                <Th>DOI</Th>
                <Th>{t("rep.recognitions")}</Th>
                <Th>{t("rep.pages")}</Th>
              </tr>
            </thead>
            <tbody>
              {r.models.map((m) => (
                <tr key={m.model_version_id}>
                  <Td>{m.model}</Td>
                  <Td className="font-mono text-xs">{m.doi ?? "—"}</Td>
                  <Td className="tabular">{m.recognitions}</Td>
                  <Td className="tabular">{m.pages}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        ) : (
          <p className="p-5 text-sm text-muted">{t("common.none")}</p>
        )}
      </Card>
      <Card>
        <CardHeader title={t("rep.quality")} description={t("rep.quality.help")} />
        {r.evaluation.by_model.length ? (
          <div className="space-y-4 p-5">
            <BarChart label={t("ev.chart", { dim: t("ev.dim.model") })} rows={r.evaluation.by_model.map((g) => ({ key: g.key, value: g.cer, note: `${g.pages} p.` }))} />
            <GroupTable groups={r.evaluation.by_model} />
          </div>
        ) : (
          <p className="p-5 text-sm text-muted">{t("ev.noResults.body")}</p>
        )}
      </Card>
      <Card>
        <CardHeader title={t("rep.contributors")} />
        {r.contributors.length ? (
          <Table>
            <thead>
              <tr>
                <Th>{t("common.name")}</Th>
                <Th>{t("rep.revisions")}</Th>
                <Th>{t("rep.linesChanged")}</Th>
                <Th>{t("rep.lastActivity")}</Th>
              </tr>
            </thead>
            <tbody>
              {r.contributors.map((x) => (
                <tr key={x.email}>
                  <Td>{x.name}</Td>
                  <Td className="tabular">{x.revisions}</Td>
                  <Td className="tabular">{x.lines_changed}</Td>
                  <Td className="text-xs">{x.last_activity ? formatDate(x.last_activity) : "—"}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        ) : (
          <p className="p-5 text-sm text-muted">{t("common.none")}</p>
        )}
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------- exports
export const FORMATS = ["tei", "page", "alto", "hocr", "txt", "json", "docx", "pdf"] as const;
export const LAYERS = ["corrected", "htr", "expanded", "reading", "normalized", "markup"] as const;
const XML = new Set(["tei", "page", "alto", "hocr"]);

export interface TeiOptions {
  abbreviations: "choice" | "abbr" | "expan";
  uncertain_cert: boolean;
  include_facsimile: boolean;
  include_normalized: boolean;
  include_notes: boolean;
}
const DEFAULT_TEI: TeiOptions = { abbreviations: "choice", uncertain_cert: false, include_facsimile: true, include_normalized: false, include_notes: true };

function TeiFields({ value, onChange }: { value: TeiOptions; onChange: (v: TeiOptions) => void }) {
  const { t } = useI18n();
  return (
    <fieldset className="space-y-2 rounded border border-rule p-3 text-sm">
      <legend className="px-1 text-xs font-medium text-muted">{t("exp.tei")}</legend>
      <label className="flex items-center gap-2">
        <span className="w-44">{t("exp.tei.abbr")}</span>
        <Select value={value.abbreviations} onChange={(e) => onChange({ ...value, abbreviations: e.target.value as TeiOptions["abbreviations"] })} className="w-56" aria-label={t("exp.tei.abbr")}>
          <option value="choice">choice / abbr + expan</option>
          <option value="abbr">abbr</option>
          <option value="expan">expan</option>
        </Select>
      </label>
      {(["uncertain_cert", "include_facsimile", "include_normalized", "include_notes"] as const).map((k) => (
        <label key={k} className="flex items-center gap-2">
          <input type="checkbox" checked={value[k]} onChange={(e) => onChange({ ...value, [k]: e.target.checked })} />
          {t(`exp.tei.${k}`)}
        </label>
      ))}
    </fieldset>
  );
}

export function ValidationBadge({ v }: { v: Dl["validation"] }) {
  const { t } = useI18n();
  if (!v) return null;
  return v.valid ? (
    <Alert tone="success">
      <span className="flex items-center gap-2" data-testid="validation-ok">
        <FileCheck2 className="size-4" /> {v.schema ? t("exp.valid", { schema: v.schema }) : t("exp.checked", { method: v.method })}
      </span>
    </Alert>
  ) : (
    <Alert>
      <span className="flex items-center gap-2">
        <FileWarning className="size-4" /> {t("exp.invalid")}
      </span>
      <ul className="mt-1 list-disc pl-5 text-xs">
        {v.errors.map((e) => (
          <li key={e}>{e}</li>
        ))}
      </ul>
    </Alert>
  );
}

/** Single-page export from the workspace (one transcription, optional past revision). */
export function ExportDialog({ transcriptionId, atRevision }: { transcriptionId: string; atRevision?: number | null }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [fmt, setFmt] = useState<(typeof FORMATS)[number]>("tei");
  const [layer, setLayer] = useState<(typeof LAYERS)[number]>("corrected");
  const [tei, setTei] = useState<TeiOptions>(DEFAULT_TEI);
  const [last, setLast] = useState<Dl | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const layers = fmt === "tei" ? (["corrected", "htr"] as const) : fmt === "txt" || fmt === "json" ? LAYERS : LAYERS.filter((l) => l !== "markup");
  useEffect(() => {
    if (!(layers as readonly string[]).includes(layer)) setLayer("corrected");
  }, [fmt, layer, layers]);

  async function go() {
    setErr(null);
    setBusy(true);
    try {
      const q = new URLSearchParams({ format: fmt, layer });
      if (atRevision) q.set("at_revision", String(atRevision));
      if (fmt === "tei") {
        q.set("tei_abbreviations", tei.abbreviations);
        q.set("tei_cert", String(tei.uncertain_cert));
        q.set("tei_facsimile", String(tei.include_facsimile));
        q.set("tei_normalized", String(tei.include_normalized));
        q.set("tei_notes", String(tei.include_notes));
      }
      const d = await download(`/transcriptions/${transcriptionId}/export?${q}`);
      setLast(d);
      saveBlob(d);
    } catch (e) {
      setErr(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => (setOpen(o), setLast(null))}>
      <DialogTrigger asChild>
        <Button variant="ghost" size="sm">
          <Download /> {t("exp.export")}
        </Button>
      </DialogTrigger>
      <DialogContent title={t("exp.exportPage")} description={atRevision ? t("exp.atRevision", { n: String(atRevision) }) : t("exp.exportPage.help")}>
        <div className="space-y-3">
          {err && <Alert>{err}</Alert>}
          <Field htmlFor="rs-f11" label={t("exp.format")}>
            <Select id="rs-f11" value={fmt} onChange={(e) => setFmt(e.target.value as typeof fmt)} aria-label={t("exp.format")}>
              {FORMATS.map((f) => (
                <option key={f} value={f}>
                  {t(`exp.fmt.${f}`)}
                </option>
              ))}
            </Select>
          </Field>
          <Field htmlFor="rs-f12" label={t("exp.layer")} hint={t(`exp.layer.${layer}.hint`)}>
            <Select id="rs-f12" value={layer} onChange={(e) => setLayer(e.target.value as typeof layer)} aria-label={t("exp.layer")}>
              {layers.map((l) => (
                <option key={l} value={l}>
                  {t(`exp.layer.${l}`)}
                </option>
              ))}
            </Select>
          </Field>
          {fmt === "tei" && <TeiFields value={tei} onChange={setTei} />}
          {XML.has(fmt) && <p className="text-xs text-muted">{t("exp.validationNote")}</p>}
          <Button onClick={go} disabled={busy}>
            <Download /> {busy ? t("exp.preparing") : t("exp.download")}
          </Button>
          {last && <ValidationBadge v={last.validation} />}
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function ExportsPanel({ project }: { project: Project }) {
  const { t, formatDate } = useI18n();
  const list = useResource<ExportJob[]>(`/projects/${project.id}/exports`);
  const cols = useResource<Collection[]>(`/projects/${project.id}/collections`);
  const can = project.permissions.includes("export");
  const [scope, setScope] = useState<"collection" | "document">("collection");
  const [colId, setColId] = useState("");
  const [docId, setDocId] = useState("");
  const docs = useResource<{ items: DocumentInfo[] }>(colId ? `/projects/${project.id}/collections/${colId}/documents?limit=500` : null);
  const [formats, setFormats] = useState<string[]>(["tei", "page", "txt"]);
  const [layer, setLayer] = useState<(typeof LAYERS)[number]>("corrected");
  const [uncorrected, setUncorrected] = useState(true);
  const [tei, setTei] = useState<TeiOptions>(DEFAULT_TEI);
  const [err, setErr] = useState<string | null>(null);
  const pending = list.data?.some((e) => e.status === "queued" || e.status === "processing");
  useEffect(() => {
    if (!pending) return;
    const h = setInterval(() => list.reload(), 2500);
    return () => clearInterval(h);
  }, [pending, list]);
  const markupBlocked = layer === "markup" && formats.some((f) => f !== "txt" && f !== "json");
  const teiBlocked = formats.includes("tei") && !["corrected", "htr"].includes(layer);

  async function start() {
    setErr(null);
    try {
      await api(`/projects/${project.id}/exports`, {
        method: "POST",
        json: { scope, scope_id: scope === "collection" ? colId : docId, formats, layer, include_uncorrected: uncorrected, tei },
      });
      list.reload();
    } catch (e) {
      setErr(errMsg(e));
    }
  }

  return (
    <div className="space-y-5">
      {can && (
        <Card>
          <CardHeader title={t("exp.batch")} description={t("exp.batch.help")} />
          <div className="grid gap-4 p-5 md:grid-cols-2">
            <div className="space-y-3">
              {err && <Alert>{err}</Alert>}
              <Field htmlFor="rs-f13" label={t("exp.scope")}>
                <Select id="rs-f13" value={scope} onChange={(e) => setScope(e.target.value as typeof scope)} aria-label={t("exp.scope")}>
                  <option value="collection">{t("exp.scope.collection")}</option>
                  <option value="document">{t("exp.scope.document")}</option>
                </Select>
              </Field>
              <Field htmlFor="rs-f14" label={t("projects.tab.collections")}>
                <Select id="rs-f14" value={colId} onChange={(e) => (setColId(e.target.value), setDocId(""))} aria-label={t("projects.tab.collections")}>
                  <option value="">—</option>
                  {cols.data?.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.title}
                    </option>
                  ))}
                </Select>
              </Field>
              {scope === "document" && (
                <Field htmlFor="rs-f15" label={t("ev.document")}>
                  <Select id="rs-f15" value={docId} onChange={(e) => setDocId(e.target.value)} aria-label={t("ev.document")}>
                    <option value="">—</option>
                    {docs.data?.items.map((d) => (
                      <option key={d.id} value={d.id}>
                        {d.title}
                      </option>
                    ))}
                  </Select>
                </Field>
              )}
              <Field htmlFor="rs-f16" label={t("exp.layer")} hint={t(`exp.layer.${layer}.hint`)}>
                <Select id="rs-f16" value={layer} onChange={(e) => setLayer(e.target.value as typeof layer)} aria-label={t("exp.layer")}>
                  {LAYERS.map((l) => (
                    <option key={l} value={l}>
                      {t(`exp.layer.${l}`)}
                    </option>
                  ))}
                </Select>
              </Field>
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={uncorrected} onChange={(e) => setUncorrected(e.target.checked)} />
                {t("exp.includeUncorrected")}
              </label>
            </div>
            <div className="space-y-3">
              <fieldset className="grid grid-cols-2 gap-1 text-sm">
                <legend className="mb-1 text-xs font-medium text-muted">{t("exp.formats")}</legend>
                {FORMATS.map((f) => (
                  <label key={f} className="flex items-center gap-2">
                    <input type="checkbox" checked={formats.includes(f)} onChange={(e) => setFormats((x) => (e.target.checked ? [...x, f] : x.filter((y) => y !== f)))} />
                    {t(`exp.fmt.${f}`)}
                  </label>
                ))}
              </fieldset>
              {formats.includes("tei") && <TeiFields value={tei} onChange={setTei} />}
              {(markupBlocked || teiBlocked) && <Alert tone="warn">{markupBlocked ? t("exp.markupOnly") : t("exp.teiLayers")}</Alert>}
              <Button onClick={start} disabled={!formats.length || (scope === "collection" ? !colId : !docId) || markupBlocked || teiBlocked}>
                <Download /> {t("exp.start")}
              </Button>
            </div>
          </div>
        </Card>
      )}
      <Card>
        <CardHeader title={t("exp.history")} />
        {list.data && list.data.length ? (
          <Table>
            <thead>
              <tr>
                <Th>{t("exp.scope")}</Th>
                <Th>{t("exp.formats")}</Th>
                <Th>{t("exp.layer")}</Th>
                <Th>{t("common.status")}</Th>
                <Th>{t("exp.contents")}</Th>
                <Th>{t("exp.validation")}</Th>
                <Th>{t("common.date")}</Th>
                <Th />
              </tr>
            </thead>
            <tbody>
              {list.data.map((e) => (
                <tr key={e.id} data-testid="export-row" data-status={e.status}>
                  <Td>
                    {t(`exp.scope.${e.scope}` as never)} · {e.options.title}
                  </Td>
                  <Td className="font-mono text-xs">{e.formats.join(", ")}</Td>
                  <Td className="text-xs">{t(`exp.layer.${e.options.layer}` as never)}</Td>
                  <Td>
                    <Badge tone={e.status === "completed" ? "success" : e.status === "failed" ? "danger" : "accent"}>{t(`exp.status.${e.status}`)}</Badge>
                    {e.error && <p className="mt-1 text-xs text-danger">{e.error.message}</p>}
                  </Td>
                  <Td className="text-xs">
                    {e.status === "completed" && (
                      <>
                        {t("exp.summary", { files: String(e.summary.files), pages: String(e.summary.pages) })}
                        {(e.summary.uncorrected_pages ?? 0) > 0 && <div className="text-warn">{t("exp.uncorrectedPages", { n: String(e.summary.uncorrected_pages) })}</div>}
                        {(e.summary.skipped_pages?.length ?? 0) > 0 && <div className="text-muted">{t("exp.skipped", { n: String(e.summary.skipped_pages!.length) })}</div>}
                      </>
                    )}
                  </Td>
                  <Td className="text-xs" data-testid="export-validation">
                    {e.summary.validation &&
                      (e.summary.validation.invalid.length === 0 ? (
                        <span className="flex items-center gap-1 text-success">
                          <ShieldCheck className="size-3.5" /> {t("exp.allValid", { n: String(e.summary.validation.checked) })}
                        </span>
                      ) : (
                        <span className="text-danger">{t("exp.someInvalid", { n: String(e.summary.validation.invalid.length) })}</span>
                      ))}
                  </Td>
                  <Td className="text-xs">{formatDate(e.created_at)}</Td>
                  <Td>
                    {e.status === "completed" && (
                      <Button size="sm" variant="secondary" onClick={async () => saveBlob(await download(`/exports/${e.id}/download`))}>
                        <Download /> ZIP · {formatBytes(e.size_bytes ?? 0)}
                      </Button>
                    )}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        ) : (
          <p className="p-5 text-sm text-muted">{t("exp.none")}</p>
        )}
      </Card>
    </div>
  );
}

// ----------------------------------------------------------------------------- provenance
interface Provenance {
  document: Record<string, unknown> & { title: string; archival_reference: string | null };
  page: { sequence: number; label: string | null; image: { filename: string | null; sha256: string; width: number; height: number; processed_image_sha256: string | null } };
  segmentation: { id: string; source: string; model: { name: string; version: string } | null; software: Record<string, string>; config: Record<string, unknown>; created_at: string };
  recognition: {
    id: string;
    created_at: string;
    duration_ms: number | null;
    software: Record<string, string>;
    config: Record<string, unknown>;
    model: { name: string; version: string; doi: string | null; zenodo_record: string | null; sha256: string | null; source_checksum: string | null; license: string | null; authors: string[]; unicode_normalization: string | null; source_reported_accuracy: number | null };
  } | null;
  transcription: { revision: number; revisions: number; contributors: string[]; policy: Record<string, unknown>; origin: string; last_modified: string | null };
  combined_from: string[];
  quality: { lines: number; reviewed: number; corrected: number; unreviewed: number; uncertain: number; illegible: number; reviewed_text_share: number | null; corrected_characters?: number; corrected_words?: number; evaluation: { model: string; cer: number | null; wer: number | null; date: string }[] };
}

export function ProvenanceDialog({ transcriptionId }: { transcriptionId: string }) {
  const { t, formatDate } = useI18n();
  const [open, setOpen] = useState(false);
  const pv = useResource<Provenance>(open ? `/transcriptions/${transcriptionId}/provenance` : null);
  const p = pv.data;
  const row = (k: string, v: React.ReactNode) => (
    <>
      <dt className="text-muted">{k}</dt>
      <dd className="break-all text-ink-2">{v ?? "—"}</dd>
    </>
  );
  const m = p?.recognition?.model;
  const q = p?.quality;
  const reported = useMemo(() => (m?.source_reported_accuracy ? `${m.source_reported_accuracy} % (${t("prov.sourceReported")})` : null), [m, t]);
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="ghost" size="sm">
          <ShieldCheck /> {t("prov.title")}
        </Button>
      </DialogTrigger>
      <DialogContent title={t("prov.title")} description={t("prov.help")} className="max-w-3xl">
        {!p ? (
          <p className="text-sm text-muted">{t("common.loading")}</p>
        ) : (
          <dl className="grid max-h-[65vh] grid-cols-[180px_minmax(0,1fr)] gap-x-4 gap-y-1.5 overflow-y-auto text-xs" data-testid="provenance">
            <dt className="col-span-2 mt-1 text-[11px] font-semibold uppercase tracking-wide text-ink">{t("prov.source")}</dt>
            {row(t("ev.document"), p.document.title)}
            {row(t("prov.archivalRef"), p.document.archival_reference)}
            {row(t("ev.page"), `${p.page.sequence}${p.page.label ? ` (${p.page.label})` : ""}`)}
            {row(t("prov.image"), `${p.page.image.filename ?? ""} · ${p.page.image.width}×${p.page.image.height}`)}
            {row("SHA-256", <span className="font-mono">{p.page.image.sha256}</span>)}
            {p.page.image.processed_image_sha256 && row(t("prov.processed"), <span className="font-mono">{p.page.image.processed_image_sha256}</span>)}
            <dt className="col-span-2 mt-3 text-[11px] font-semibold uppercase tracking-wide text-ink">{t("prov.recognition")}</dt>
            {row(t("prov.segmentation"), `${p.segmentation.source}${p.segmentation.model ? ` · ${p.segmentation.model.name} ${p.segmentation.model.version}` : ""} · Kraken ${p.segmentation.software?.kraken ?? "—"}`)}
            {m && row(t("ev.model"), `${m.name} · ${m.version}`)}
            {m && row("DOI", m.doi ? <a className="text-accent hover:underline" href={`https://doi.org/${m.doi}`} target="_blank" rel="noreferrer">{m.doi}</a> : null)}
            {m && row(t("prov.authors"), m.authors.join(", ") || null)}
            {m && row(t("prov.licence"), m.license)}
            {m && row(t("prov.modelFile"), <span className="font-mono">sha256 {m.sha256} · {m.source_checksum}</span>)}
            {m && row("Unicode", m.unicode_normalization)}
            {m && reported && row(t("prov.reportedAcc"), reported)}
            {p.recognition && row("Kraken / torch", `${p.recognition.software?.kraken ?? "—"} / ${p.recognition.software?.torch ?? "—"}`)}
            {p.recognition && row(t("prov.config"), <span className="font-mono">{JSON.stringify(p.recognition.config)}</span>)}
            {p.recognition && row(t("prov.date"), formatDate(p.recognition.created_at))}
            {p.combined_from.length > 0 && row(t("cmp.combinedFrom"), p.combined_from.join(" + "))}
            <dt className="col-span-2 mt-3 text-[11px] font-semibold uppercase tracking-wide text-ink">{t("prov.editorial")}</dt>
            {row(t("ws.revisions"), `${p.transcription.revision} (${p.transcription.revisions})`)}
            {row(t("prov.contributors"), p.transcription.contributors.join(", ") || null)}
            {row(t("prov.policy"), <span className="font-mono">{Object.entries(p.transcription.policy).map(([k, v]) => `${k}=${v}`).join(" · ")}</span>)}
            {q && row(t("prov.lines"), t("prov.linesValue", { lines: String(q.lines), reviewed: String(q.reviewed), corrected: String(q.corrected), unreviewed: String(q.unreviewed), uncertain: String(q.uncertain), illegible: String(q.illegible) }))}
            {q && row(t("prov.reviewedShare"), pct(q.reviewed_text_share, 1))}
            {q && q.corrected_characters !== undefined && row(t("prov.correctedVsHtr"), t("prov.correctedValue", { c: String(q.corrected_characters), w: String(q.corrected_words) }))}
            {q && q.evaluation.length > 0 && row(t("rep.quality"), q.evaluation.map((e) => `${e.model}: CER ${pct(e.cer)} · WER ${pct(e.wer)}`).join("; "))}
          </dl>
        )}
      </DialogContent>
    </Dialog>
  );
}
