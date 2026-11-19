"use client";

import { Download, RefreshCw, Search } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Alert, Badge, Button, Card, CardHeader, Dialog, DialogContent, EmptyState, Field, Input, PageHeader, Select, Table, Tabs, TabsContent, TabsList, TabsTrigger, Td, Textarea, Th } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import type { MessageKey } from "@/i18n/messages";
import { api, ApiError, formatBytes, type ModelVersionInfo } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useResource } from "@/lib/hooks";
import { parseLanguages } from "@/lib/utils";

type RegVersion = ModelVersionInfo & {
  record_id: string | null;
  publication_date: string | null;
  bytes_downloaded: number;
  is_active: boolean;
  source_reported_accuracy: number | null;
  model_card_format: string | null;
};
type RegModel = {
  id: string;
  slug: string;
  name: string;
  task: string;
  source: string;
  family: string | null;
  concept_doi: string | null;
  source_languages: string[];
  source_scripts: string[];
  curated: Record<string, unknown>;
  active_version_id: string | null;
  latest_version_label: string | null;
  update_available: boolean;
  last_update_check_at: string | null;
  versions: RegVersion[];
};
type Preview = {
  record: {
    record_id: string;
    title: string;
    version: string | null;
    doi: string | null;
    publication_date: string | null;
    license_id: string | null;
    description: string;
    creators: { name: string; affiliation: string | null; orcid: string | null }[];
    keywords: string[];
    html_url: string | null;
  };
  is_latest: boolean;
  versions: {
    record_id: string;
    version: string | null;
    publication_date: string | null;
    is_latest: boolean;
    identical_binary_as: string[];
    model_files: { key: string; size: number; checksum: string }[];
    installed: { id: string; status: string } | null;
  }[];
  model_files: { key: string; size: number; checksum: string }[];
  model_card: { format: string | null; valid: boolean; errors: string[] };
  source_reported_accuracy: number | null;
  unicode_normalization: { form: string; quote: string } | null;
  compatibility: { verdict: "likely" | "unknown" | "incompatible"; reasons: string[] };
  license: { id: string | null; notice: string };
};

const statusTone = (s: string) =>
  s === "validated" ? "success" : s === "incompatible" || s === "failed" ? "danger" : s === "downloading" || s === "installing" ? "accent" : "neutral";

export default function ModelsPage() {
  const { t } = useI18n();
  const { user } = useAuth();
  const admin = !!user?.is_platform_admin;
  const models = useResource<RegModel[]>("/model-registry/models");
  // "installed" is transient: Kraken validation follows immediately. Poll for at most 20 minutes.
  const busy = models.data?.some((m) => m.versions.some((v) => ["downloading", "installing", "installed"].includes(v.status)));
  const { reload } = models;

  useEffect(() => {
    if (!busy) return;
    const started = Date.now();
    const h = setInterval(() => (Date.now() - started > 20 * 60_000 ? clearInterval(h) : reload()), 1500);
    return () => clearInterval(h);
  }, [busy, reload]);

  return (
    <>
      <PageHeader title={t("models.title")} subtitle={t("models.subtitle")} />
      <Tabs defaultValue="installed">
        <TabsList>
          <TabsTrigger value="installed">{t("nav.models")}</TabsTrigger>
          {admin && <TabsTrigger value="install">{t("registry.install")}</TabsTrigger>}
          {admin && <TabsTrigger value="discover">{t("registry.discover")}</TabsTrigger>}
        </TabsList>
        <TabsContent value="installed">
          {models.error && <Alert>{models.error.message}</Alert>}
          {models.data?.length === 0 && (
            <Card>
              <EmptyState title={t("models.empty")} />
            </Card>
          )}
          <div className="space-y-6">
            {models.data?.map((m) => (
              <ModelCard key={m.id} model={m} admin={admin} onChange={reload} />
            ))}
          </div>
        </TabsContent>
        {admin && (
          <TabsContent value="install">
            <InstallPanel onInstalled={reload} />
          </TabsContent>
        )}
        {admin && (
          <TabsContent value="discover">
            <DiscoverPanel />
          </TabsContent>
        )}
      </Tabs>
    </>
  );
}

function Attr({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <>
      <dt className="text-muted">{label}</dt>
      <dd className="text-ink-2">{value}</dd>
    </>
  );
}

function ModelCard({ model: m, admin, onChange }: { model: RegModel; admin: boolean; onChange: () => void }) {
  const { t, formatDate } = useI18n();
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [log, setLog] = useState<ModelVersionInfo | null>(null);
  const [curating, setCurating] = useState(false);
  const cur = m.curated as { languages?: string[]; period_start?: number; period_end?: number; script_type?: string; transcription_convention?: string; basis?: string };

  async function act(fn: () => Promise<unknown>) {
    setErr(null);
    try {
      await fn();
      onChange();
    } catch (e) {
      setErr(e instanceof ApiError ? e.body.message : t("common.error"));
    }
  }

  return (
    <Card data-testid={`model-${m.slug}`}>
      <CardHeader
        title={
          <span className="flex flex-wrap items-center gap-2">
            {m.name}
            {m.family && <Badge tone="accent">{m.family}</Badge>}
            <Badge>{t(`models.task.${m.task}` as MessageKey)}</Badge>
            {m.update_available && <Badge tone="warn">{t("registry.updateAvailable", { v: m.latest_version_label ?? "?" })}</Badge>}
            {!m.update_available && m.last_update_check_at && <Badge tone="success">{t("registry.upToDate")}</Badge>}
          </span>
        }
        description={
          m.concept_doi ? (
            <a className="font-mono text-accent hover:underline" href={`https://doi.org/${m.concept_doi}`} target="_blank" rel="noreferrer">
              {m.concept_doi}
            </a>
          ) : (
            m.source
          )
        }
        action={
          admin &&
          m.source === "zenodo" && (
            <Button
              variant="secondary"
              size="sm"
              onClick={() =>
                act(async () => {
                  const r = await api<{ note: string | null; update_available: boolean; latest_version: string }>(`/admin/models/${m.id}/check-updates`, { method: "POST" });
                  setNote(r.note);
                })
              }
            >
              <RefreshCw /> {t("registry.checkUpdates")}
            </Button>
          )
        }
      />
      {err && <Alert>{err}</Alert>}
      {note && <Alert tone="accent">{note}</Alert>}
      <div className="grid gap-6 px-5 py-4 lg:grid-cols-2">
        <div>
          <h3 className="mb-2 text-xs font-medium uppercase tracking-[0.07em] text-muted">{t("registry.declared")}</h3>
          <dl className="grid grid-cols-[130px_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-[13px]">
            <Attr label={t("registry.languages")} value={m.source_languages.length ? <span className="font-mono">{m.source_languages.join(" · ")}</span> : t("recommend.unknown")} />
            <Attr label={t("registry.scripts")} value={m.source_scripts.length ? m.source_scripts.join(", ") : t("recommend.unknown")} />
          </dl>
        </div>
        <div>
          <h3 className="mb-2 flex items-center justify-between text-xs font-medium uppercase tracking-[0.07em] text-muted">
            {t("registry.curated")}
            {admin && (
              <Button variant="link" size="sm" onClick={() => setCurating(true)}>
                {t("registry.editCuration")}
              </Button>
            )}
          </h3>
          <dl className="grid grid-cols-[130px_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-[13px]">
            <Attr label={t("registry.languages")} value={cur.languages?.length ? <span className="font-mono">{cur.languages.join(" · ")}</span> : t("common.none")} />
            <Attr label={t("registry.period")} value={cur.period_start != null ? `${cur.period_start}–${cur.period_end}` : t("common.none")} />
            <Attr label={t("documents.field.script")} value={cur.script_type ?? t("common.none")} />
            <Attr label={t("registry.convention")} value={cur.transcription_convention ?? t("common.none")} />
            {cur.basis && <Attr label={t("registry.basis")} value={<span className="text-xs italic">{cur.basis}</span>} />}
          </dl>
        </div>
      </div>
      <Table>
        <thead>
          <tr>
            <Th>Version</Th>
            <Th>Status</Th>
            <Th>{t("models.license")}</Th>
            <Th>{t("models.normalization")}</Th>
            <Th>{t("registry.accuracy")}</Th>
            <Th>Checksum</Th>
            <Th />
          </tr>
        </thead>
        <tbody>
          {m.versions.map((v) => (
            <tr key={v.id} data-version={v.version}>
              <Td>
                <div className="font-medium text-ink">
                  {v.version} {v.is_active && <Badge tone="success">{t("registry.active")}</Badge>}
                </div>
                <div className="text-xs text-muted">
                  {v.publication_date ?? ""} {v.size_bytes ? `· ${formatBytes(v.size_bytes)}` : ""}
                </div>
              </Td>
              <Td>
                <Badge tone={statusTone(v.status)} data-status={v.status}>
                  {v.status}
                </Badge>
                {v.status === "downloading" && v.size_bytes && (
                  <div className="mt-1.5 w-32">
                    <div className="h-1 overflow-hidden rounded bg-sunken">
                      <div className="h-full bg-accent" style={{ width: `${Math.round((100 * v.bytes_downloaded) / v.size_bytes)}%` }} />
                    </div>
                    <div className="tabular mt-0.5 text-[11px] text-muted">
                      {t("registry.progress", { done: formatBytes(v.bytes_downloaded), total: formatBytes(v.size_bytes) })}
                    </div>
                  </div>
                )}
                {v.kraken_version_validated && <div className="mt-1 text-[11px] text-muted">{t("models.validatedWith", { v: v.kraken_version_validated })}</div>}
                {v.validated_at && <div className="text-[11px] text-muted">{formatDate(v.validated_at)}</div>}
              </Td>
              <Td className="text-xs">{v.license_id ?? t("common.none")}</Td>
              <Td className="text-xs">{v.unicode_normalization ?? t("common.none")}</Td>
              <Td className="text-xs" title={t("registry.accuracyNote")}>
                {v.source_reported_accuracy != null ? `${v.source_reported_accuracy}% *` : t("common.none")}
              </Td>
              <Td className="font-mono text-[11px] text-muted">
                <div>{v.source_checksum}</div>
                {v.sha256 && <div>sha256:{v.sha256.slice(0, 12)}…</div>}
              </Td>
              <Td className="whitespace-nowrap text-right">
                <Button variant="ghost" size="sm" onClick={() => api<ModelVersionInfo>(admin ? `/admin/model-versions/${v.id}` : `/models/${v.id}`).then(setLog)}>
                  {t("models.installLog")}
                </Button>
                {admin && v.status === "validated" && !v.is_active && (
                  <Button variant="secondary" size="sm" onClick={() => act(() => api(`/admin/models/${m.id}/active-version`, { method: "POST", json: { version_id: v.id } }))}>
                    {t("registry.activate")}
                  </Button>
                )}
                {admin && v.status === "failed" && (
                  <Button variant="secondary" size="sm" onClick={() => act(() => api(`/admin/model-versions/${v.id}/retry`, { method: "POST" }))}>
                    {t("registry.retry")}
                  </Button>
                )}
              </Td>
            </tr>
          ))}
        </tbody>
      </Table>
      {m.versions.some((v) => v.source_reported_accuracy != null) && <p className="px-5 py-2 text-[11px] text-muted">* {t("registry.accuracyNote")}</p>}
      <Dialog open={!!log} onOpenChange={(o) => !o && setLog(null)}>
        {log && (
          <DialogContent title={`${log.model_name} · ${log.version}`} className="max-w-3xl">
            <ol className="mb-4 space-y-1 font-mono text-[12px] text-ink-2" data-testid="install-log">
              {log.install_log?.map((e, i) => (
                <li key={i}>
                  <span className="text-muted">{formatDate(e.ts)}</span> {e.message}
                  {e.error ? <span className="text-danger"> {JSON.stringify(e.error)}</span> : null}
                </li>
              ))}
            </ol>
            <pre className="max-h-64 overflow-auto rounded bg-sunken p-3 text-[11px]">{JSON.stringify(log.file_metadata, null, 2)}</pre>
          </DialogContent>
        )}
      </Dialog>
      <Dialog open={curating} onOpenChange={setCurating}>
        <DialogContent title={t("registry.editCuration")}>
          <CurationForm
            model={m}
            onSaved={() => {
              setCurating(false);
              onChange();
            }}
          />
        </DialogContent>
      </Dialog>
    </Card>
  );
}

function CurationForm({ model, onSaved }: { model: RegModel; onSaved: () => void }) {
  const { t } = useI18n();
  const c = model.curated as Record<string, string | number | string[] | undefined>;
  const [err, setErr] = useState<string | null>(null);
  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const num = (k: string) => (f.get(k) ? Number(f.get(k)) : null);
    try {
      await api(`/admin/models/${model.id}/curation`, {
        method: "PATCH",
        json: {
          languages: parseLanguages(String(f.get("languages") || "")),
          period_start: num("ps"),
          period_end: num("pe"),
          script_type: f.get("script") || null,
          transcription_convention: String(f.get("conv") || "") || null,
          basis: String(f.get("basis") || "") || null,
        },
      });
      onSaved();
    } catch (er) {
      setErr(er instanceof ApiError ? er.body.message : t("common.error"));
    }
  }
  return (
    <form onSubmit={submit} className="space-y-3">
      {err && <Alert>{err}</Alert>}
      <Field label={t("registry.languages")} htmlFor="c-langs" hint="ISO 639-3">
        <Input id="c-langs" name="languages" defaultValue={((c.languages as string[]) ?? []).join(", ")} />
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label={t("projects.field.periodStart")} htmlFor="c-ps">
          <Input id="c-ps" name="ps" type="number" defaultValue={(c.period_start as number) ?? ""} />
        </Field>
        <Field label={t("projects.field.periodEnd")} htmlFor="c-pe">
          <Input id="c-pe" name="pe" type="number" defaultValue={(c.period_end as number) ?? ""} />
        </Field>
      </div>
      <Field label={t("documents.field.script")} htmlFor="c-script">
        <Select id="c-script" name="script" defaultValue={(c.script_type as string) ?? ""}>
          <option value="">—</option>
          <option value="manuscript">{t("documents.script.manuscript")}</option>
          <option value="print">{t("documents.script.print")}</option>
          <option value="mixed">{t("documents.script.mixed")}</option>
        </Select>
      </Field>
      <Field label={t("registry.convention")} htmlFor="c-conv">
        <Input id="c-conv" name="conv" defaultValue={(c.transcription_convention as string) ?? ""} />
      </Field>
      <Field label={t("registry.basis")} htmlFor="c-basis" hint="Cite the source of these facts.">
        <Textarea id="c-basis" name="basis" defaultValue={(c.basis as string) ?? ""} className="min-h-[60px]" required />
      </Field>
      <div className="flex justify-end">
        <Button type="submit">{t("common.save")}</Button>
      </div>
    </form>
  );
}

function InstallPanel({ onInstalled, initial }: { onInstalled: () => void; initial?: string }) {
  const { t } = useI18n();
  const catalog = useResource<{ identifier: string; family: string }[]>("/admin/model-registry/catalog");
  const [identifier, setIdentifier] = useState(initial ?? "");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [accepted, setAccepted] = useState(false);
  const [done, setDone] = useState(false);
  const [file, setFile] = useState<string>("");

  const load = useCallback(
    async (id: string) => {
      setLoading(true);
      setErr(null);
      setPreview(null);
      setAccepted(false);
      setDone(false);
      try {
        const p = await api<Preview>(`/admin/model-registry/preview?identifier=${encodeURIComponent(id)}`);
        setPreview(p);
        setFile(p.model_files[0]?.key ?? "");
      } catch (e) {
        setErr(e instanceof ApiError ? e.body.message : t("common.error"));
      } finally {
        setLoading(false);
      }
    },
    [t],
  );

  async function install() {
    setErr(null);
    try {
      await api(`/admin/model-registry/install`, { method: "POST", json: { identifier: preview!.record.record_id, file_key: file, accept_license: accepted } });
      setDone(true);
      onInstalled();
    } catch (e) {
      setErr(e instanceof ApiError ? e.body.message : t("common.error"));
    }
  }

  const r = preview?.record;
  return (
    <div className="space-y-5">
      <Card>
        <CardHeader title={t("registry.install")} />
        <form
          className="flex flex-wrap items-end gap-3 px-5 py-4"
          onSubmit={(e) => {
            e.preventDefault();
            load(identifier);
          }}
        >
          <Field label={t("registry.identifier")} htmlFor="ident">
            <Input id="ident" value={identifier} onChange={(e) => setIdentifier(e.target.value)} className="w-96 font-mono text-xs" placeholder="10.5281/zenodo.14543904" />
          </Field>
          <Button type="submit" disabled={!identifier || loading}>
            {loading ? t("common.loading") : t("registry.preview")}
          </Button>
          {catalog.data && (
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="text-muted">{t("registry.catalog")}:</span>
              {catalog.data.map((c) => (
                <Button
                  key={c.identifier}
                  type="button"
                  variant="link"
                  size="sm"
                  onClick={() => {
                    setIdentifier(c.identifier);
                    load(c.identifier);
                  }}
                >
                  {c.family}
                </Button>
              ))}
            </div>
          )}
        </form>
      </Card>
      {err && <Alert>{err}</Alert>}
      {done && <Alert tone="success">{t("registry.installing")}</Alert>}
      {preview && r && (
        <Card data-testid="registry-preview">
          <CardHeader
            title={`${r.title} · ${r.version ?? r.record_id}`}
            description={
              <span className="font-mono">
                {r.doi} · {r.publication_date}
              </span>
            }
          />
          {!preview.is_latest && (
            <div className="px-5 pt-3">
              <Alert tone="warn">{t("registry.notLatest")}</Alert>
            </div>
          )}
          <div className="grid gap-6 px-5 py-4 lg:grid-cols-2">
            <dl className="grid grid-cols-[150px_minmax(0,1fr)] content-start gap-x-3 gap-y-2 text-[13px]">
              <Attr label={t("registry.authors")} value={r.creators.map((c) => c.name).join("; ")} />
              <Attr label={t("registry.licence")} value={<Badge tone="accent">{r.license_id ?? "—"}</Badge>} />
              <Attr
                label={t("registry.card")}
                value={
                  preview.model_card.format
                    ? t(preview.model_card.valid ? "registry.card.valid" : "registry.card.invalid", { f: preview.model_card.format })
                    : t("registry.card.none")
                }
              />
              <Attr label={t("registry.accuracy")} value={preview.source_reported_accuracy != null ? <span title={t("registry.accuracyNote")}>{preview.source_reported_accuracy}% *</span> : t("common.none")} />
              <Attr
                label={t("registry.normalization")}
                value={preview.unicode_normalization ? <span><Badge>{preview.unicode_normalization.form}</Badge> <span className="text-xs italic">“{preview.unicode_normalization.quote}”</span></span> : t("common.none")}
              />
              <Attr
                label={t("registry.compat")}
                value={
                  <span>
                    <Badge tone={preview.compatibility.verdict === "likely" ? "success" : preview.compatibility.verdict === "incompatible" ? "danger" : "neutral"}>
                      {t(`registry.compat.${preview.compatibility.verdict}` as MessageKey)}
                    </Badge>
                    <ul className="mt-1 list-disc pl-4 text-xs text-muted">
                      {preview.compatibility.reasons.map((x) => (
                        <li key={x}>{x}</li>
                      ))}
                    </ul>
                  </span>
                }
              />
            </dl>
            <div>
              <h3 className="mb-2 text-xs font-medium uppercase tracking-[0.07em] text-muted">{t("registry.versions")}</h3>
              <ul className="divide-y divide-rule text-[13px]" data-testid="preview-versions">
                {preview.versions.map((v) => (
                  <li key={v.record_id} className="py-1.5">
                    <span className="font-medium text-ink">{v.version ?? v.record_id}</span> <span className="text-xs text-muted">{v.publication_date}</span>{" "}
                    {v.is_latest && <Badge tone="accent">{t("registry.latest")}</Badge>}{" "}
                    {v.installed && <Badge tone="success">{t("registry.installed")}</Badge>}
                    {v.identical_binary_as.length > 0 && <div className="text-xs text-muted">{t("registry.identical", { v: v.identical_binary_as.join(", ") })}</div>}
                  </li>
                ))}
              </ul>
            </div>
          </div>
          <p className="mx-5 mb-3 max-h-28 overflow-y-auto whitespace-pre-line text-xs text-muted">{r.description}</p>
          <div className="space-y-3 border-t border-rule px-5 py-4">
            {preview.model_files.length > 1 && (
              <Field label={t("registry.files")} htmlFor="mfile">
                <Select id="mfile" value={file} onChange={(e) => setFile(e.target.value)}>
                  {preview.model_files.map((f) => (
                    <option key={f.key} value={f.key}>
                      {f.key}
                    </option>
                  ))}
                </Select>
              </Field>
            )}
            {preview.model_files.map((f) => (
              <div key={f.key} className="font-mono text-[11px] text-muted">
                {f.key} · {formatBytes(f.size)} · {f.checksum}
              </div>
            ))}
            <Alert tone="accent">{preview.license.notice}</Alert>
            <label className="flex items-start gap-2 text-sm text-ink-2">
              <input type="checkbox" checked={accepted} onChange={(e) => setAccepted(e.target.checked)} className="mt-1 accent-[var(--color-accent)]" />
              {t("registry.acceptLicence", { l: r.license_id ?? "—" })}
            </label>
            <div className="flex justify-end">
              <Button onClick={install} disabled={!accepted || !file || preview.compatibility.verdict === "incompatible" || done}>
                <Download /> {t("registry.installSelected")}
              </Button>
            </div>
          </div>
        </Card>
      )}
    </div>
  );
}

function DiscoverPanel() {
  const { t } = useI18n();
  const [q, setQ] = useState("CATMuS");
  const [data, setData] = useState<{ total: number; items: { record_id: string; title: string; version: string | null; license_id: string | null; resource_type: string | null; creators: string[]; files: { key: string; kraken_model: boolean }[]; precheck: { verdict: string; reasons: string[] } }[] } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [previewId, setPreviewId] = useState<string | null>(null);

  async function search(e?: React.FormEvent) {
    e?.preventDefault();
    setErr(null);
    try {
      setData(await api(`/admin/model-registry/discover?q=${encodeURIComponent(q)}`));
    } catch (er) {
      setErr(er instanceof ApiError ? er.body.message : t("common.error"));
    }
  }

  if (previewId) return <InstallPanel key={previewId} initial={previewId} onInstalled={() => undefined} />;
  return (
    <Card>
      <CardHeader title={t("registry.discover")} />
      <form onSubmit={search} className="flex gap-3 px-5 py-4">
        <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("registry.search")} className="max-w-md" aria-label={t("registry.search")} />
        <Button type="submit">
          <Search /> {t("common.search")}
        </Button>
      </form>
      {err && <Alert>{err}</Alert>}
      {data && data.items.length === 0 && <EmptyState title={t("registry.noResults")} />}
      {data && data.items.length > 0 && (
        <Table data-testid="discover-results">
          <tbody>
            {data.items.map((i) => (
              <tr key={i.record_id}>
                <Td>
                  <div className="font-medium text-ink">{i.title}</div>
                  <div className="text-xs text-muted">
                    {i.version ?? "—"} · {i.resource_type} · {i.license_id} · {i.creators.slice(0, 3).join("; ")}
                  </div>
                  <div className="mt-1 font-mono text-[11px] text-muted">{i.files.map((f) => f.key).join(", ")}</div>
                </Td>
                <Td>
                  <Badge tone={i.precheck.verdict === "likely" ? "success" : i.precheck.verdict === "incompatible" ? "danger" : "neutral"}>
                    {t(`registry.compat.${i.precheck.verdict}` as MessageKey)}
                  </Badge>
                </Td>
                <Td className="text-right">
                  {i.precheck.verdict !== "incompatible" && (
                    <Button variant="secondary" size="sm" onClick={() => setPreviewId(i.record_id)}>
                      {t("registry.preview")}
                    </Button>
                  )}
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </Card>
  );
}
