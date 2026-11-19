"use client";

import { FileUp } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { Alert, Button, Card, CardHeader, Dialog, DialogContent, DialogTrigger, EmptyState, Field, Input, PageHeader, Select, Table, Td, Th, Textarea } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import { ApiError, uploadFiles, type Collection, type DocumentInfo, type Paged, type Project } from "@/lib/api";
import { useResource } from "@/lib/hooks";
import { IngestBadge } from "@/components/recognition";
import { parseLanguages } from "@/lib/utils";

export default function CollectionPage() {
  const { id, cid } = useParams<{ id: string; cid: string }>();
  const { t, formatDate } = useI18n();
  const project = useResource<Project>(`/projects/${id}`);
  const collection = useResource<Collection>(`/projects/${id}/collections/${cid}`);
  const docs = useResource<Paged<DocumentInfo>>(`/projects/${id}/collections/${cid}/documents?limit=500`);
  const pending = docs.data?.items.some((d) => d.ingest_status === "pending");
  const { reload } = docs;

  useEffect(() => {
    if (!pending) return;
    const h = setInterval(reload, 2000);
    return () => clearInterval(h);
  }, [pending, reload]);

  if (collection.error) return <Alert>{t("common.notFound")}</Alert>;
  const canUpload = project.data?.permissions.includes("upload");

  return (
    <>
      <PageHeader
        eyebrow={
          <Link href={`/projects/${id}`} className="hover:text-ink">
            {project.data?.title ?? t("nav.projects")}
          </Link>
        }
        title={collection.data?.title ?? ""}
        subtitle={collection.data?.archival_reference && <span className="font-mono text-xs">{collection.data.archival_reference}</span>}
        action={canUpload && <UploadDialog projectId={id} collectionId={cid} onDone={reload} />}
      />
      <Card>
        <CardHeader title={t("documents.title")} />
        {docs.data?.items.length === 0 && <EmptyState title={t("documents.empty")} body={t("documents.upload.hint")} />}
        {docs.data && docs.data.items.length > 0 && (
          <Table data-testid="document-table">
            <thead>
              <tr>
                <Th>{t("projects.field.title")}</Th>
                <Th>{t("documents.field.reference")}</Th>
                <Th>{t("recognition.pages")}</Th>
                <Th>Status</Th>
                <Th>{t("audit.when")}</Th>
              </tr>
            </thead>
            <tbody>
              {docs.data.items.map((d) => (
                <tr key={d.id}>
                  <Td>
                    <Link href={`/documents/${d.id}`} className="font-medium text-ink hover:text-accent hover:underline">
                      {d.title}
                    </Link>
                    <div className="text-xs text-muted">
                      {d.source?.original_filename} · {d.source?.mime_type}
                    </div>
                  </Td>
                  <Td className="font-mono text-xs">{d.archival_reference ?? t("common.none")}</Td>
                  <Td className="tabular">{d.page_count}</Td>
                  <Td>
                    <IngestBadge status={d.ingest_status} />
                    {d.ingest_error && <div className="mt-1 text-xs text-danger">{d.ingest_error.message}</div>}
                  </Td>
                  <Td className="tabular text-xs">{formatDate(d.created_at)}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </>
  );
}

function UploadDialog({ projectId, collectionId, onDone }: { projectId: string; collectionId: string; onDone: () => void }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const get = (k: string) => String(f.get(k) || "").trim() || null;
    const metadata = {
      script_type: get("script_type") ?? "manuscript",
      document_type: get("document_type"),
      languages: parseLanguages(String(f.get("languages") || "")),
      date_text: get("date_text"),
      date_start: get("date_start") ? Number(get("date_start")) : null,
      date_end: get("date_end") ? Number(get("date_end")) : null,
      archival_reference: get("archival_reference"),
      provenance: get("provenance"),
      notes: get("notes"),
      ...(files.length === 1 && get("title") ? { title: get("title") } : {}),
    };
    const form = new FormData();
    files.forEach((file) => form.append("files", file));
    form.append("metadata", JSON.stringify(metadata));
    setBusy(true);
    setError(null);
    try {
      await uploadFiles(`/projects/${projectId}/collections/${collectionId}/documents`, form);
      setOpen(false);
      setFiles([]);
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.body.message : t("common.error"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button>
          <FileUp /> {t("documents.upload")}
        </Button>
      </DialogTrigger>
      <DialogContent title={t("documents.upload")} description={t("documents.upload.hint")}>
        <form onSubmit={submit} className="space-y-4">
          {error && <Alert>{error}</Alert>}
          <div className="rounded-[var(--radius-md)] border border-dashed border-rule-strong px-4 py-5 text-center">
            <input
              ref={input}
              type="file"
              multiple
              accept=".png,.jpg,.jpeg,.tif,.tiff,.pdf"
              className="sr-only"
              id="files"
              data-testid="file-input"
              onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
            />
            <Button type="button" variant="secondary" onClick={() => input.current?.click()}>
              {t("documents.choose")}
            </Button>
            {files.length > 0 && (
              <p className="mt-2 text-sm text-ink-2">
                {t("documents.selected", { n: files.length })}: {files.map((f) => f.name).join(", ")}
              </p>
            )}
          </div>
          {files.length <= 1 && (
            <Field label={t("projects.field.title")} htmlFor="d_title" optional={t("common.optional")}>
              <Input id="d_title" name="title" />
            </Field>
          )}
          <div className="grid grid-cols-2 gap-3">
            <Field label={t("documents.field.script")} htmlFor="script_type">
              <Select id="script_type" name="script_type" defaultValue="manuscript">
                <option value="manuscript">{t("documents.script.manuscript")}</option>
                <option value="print">{t("documents.script.print")}</option>
                <option value="mixed">{t("documents.script.mixed")}</option>
              </Select>
            </Field>
            <Field label={t("projects.field.languages")} htmlFor="d_languages" hint="ISO 639-3">
              <Input id="d_languages" name="languages" placeholder="por" />
            </Field>
            <Field label={t("documents.field.type")} htmlFor="document_type" optional={t("common.optional")}>
              <Input id="document_type" name="document_type" />
            </Field>
            <Field label={t("documents.field.date")} htmlFor="date_text" optional={t("common.optional")}>
              <Input id="date_text" name="date_text" />
            </Field>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Field label={t("projects.field.periodStart")} htmlFor="date_start" optional={t("common.optional")}>
              <Input id="date_start" name="date_start" type="number" />
            </Field>
            <Field label={t("projects.field.periodEnd")} htmlFor="date_end" optional={t("common.optional")}>
              <Input id="date_end" name="date_end" type="number" />
            </Field>
          </div>
          <Field label={t("documents.field.reference")} htmlFor="archival_reference" optional={t("common.optional")}>
            <Input id="archival_reference" name="archival_reference" />
          </Field>
          <Field label={t("documents.field.provenance")} htmlFor="provenance" optional={t("common.optional")}>
            <Textarea id="provenance" name="provenance" className="min-h-[60px]" />
          </Field>
          <Field label={t("documents.field.notes")} htmlFor="notes" optional={t("common.optional")}>
            <Textarea id="notes" name="notes" className="min-h-[60px]" />
          </Field>
          <div className="flex justify-end">
            <Button type="submit" disabled={busy || files.length === 0}>
              {busy ? t("documents.uploading") : t("documents.upload")}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
