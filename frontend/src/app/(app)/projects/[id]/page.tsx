"use client";

import { Archive, ArchiveRestore, Pencil, Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { ActivityList } from "@/components/ActivityList";
import { ProjectForm } from "@/components/ProjectForm";
import { JobsPanel } from "@/components/recognition";
import { AnalyticsPanel, AnnotationsPanel, TrainingPanel } from "@/components/advanced";
import { EvaluationPanel, ExportsPanel, ReportPanel } from "@/components/research";
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
  PageHeader,
  Select,
  Table,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  Td,
  Textarea,
  Th,
} from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import type { MessageKey } from "@/i18n/messages";
import { api, ApiError, type AuditEntry, type Collection, type Member, type Paged, type Project } from "@/lib/api";
import { useResource } from "@/lib/hooks";
import { formatPeriod } from "@/lib/utils";

const PROJECT_ROLES = ["principal_investigator", "researcher", "transcription_editor", "reviewer", "read_only"] as const;

export default function ProjectPage() {
  const { id } = useParams<{ id: string }>();
  const { t, formatDate } = useI18n();
  const router = useRouter();
  const project = useResource<Project>(`/projects/${id}`);
  const [editOpen, setEditOpen] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  if (project.error) return <Alert>{project.error.status === 404 ? t("common.notFound") : project.error.message}</Alert>;
  const p = project.data;
  if (!p) return <p className="text-sm text-muted">{t("common.loading")}</p>;
  const can = (perm: string) => p.permissions.includes(perm);

  async function toggleArchive() {
    try {
      project.setData(await api<Project>(`/projects/${p!.id}`, { method: "PATCH", json: { is_archived: !p!.is_archived } }));
    } catch (e) {
      setActionError(e instanceof ApiError ? e.body.message : t("common.error"));
    }
  }

  async function remove() {
    if (!window.confirm(t("common.confirmDelete"))) return;
    try {
      await api(`/projects/${p!.id}`, { method: "DELETE" });
      router.replace("/projects");
    } catch (e) {
      setActionError(e instanceof ApiError && e.code === "project_not_empty" ? t("projects.deleteNotEmpty") : t("common.error"));
    }
  }

  return (
    <>
      <PageHeader
        eyebrow={
          <Link href="/projects" className="hover:text-ink">
            {t("nav.projects")}
          </Link>
        }
        title={p.title}
        subtitle={
          <span className="flex flex-wrap items-center gap-x-4 gap-y-1">
            <span>{p.institution_name ?? t("projects.field.independent")}</span>
            {formatPeriod(p.period_start, p.period_end) && <span className="tabular">{formatPeriod(p.period_start, p.period_end)}</span>}
            {p.languages.length > 0 && <span className="font-mono text-xs">{p.languages.join(" · ")}</span>}
            {p.is_archived && <Badge tone="warn">{t("projects.archived")}</Badge>}
          </span>
        }
        action={
          <div className="flex gap-2">
            {can("edit_metadata") && (
              <Dialog open={editOpen} onOpenChange={setEditOpen}>
                <DialogTrigger asChild>
                  <Button variant="secondary" size="sm">
                    <Pencil /> {t("common.edit")}
                  </Button>
                </DialogTrigger>
                <DialogContent title={t("common.edit")}>
                  <ProjectForm
                    project={p}
                    onSaved={(np) => {
                      project.setData(np);
                      setEditOpen(false);
                    }}
                  />
                </DialogContent>
              </Dialog>
            )}
            {can("edit_metadata") && (
              <Button variant="secondary" size="sm" onClick={toggleArchive}>
                {p.is_archived ? <ArchiveRestore /> : <Archive />}
                {p.is_archived ? t("projects.unarchive") : t("projects.archive")}
              </Button>
            )}
            {can("delete") && (
              <Button variant="danger" size="sm" onClick={remove} aria-label={t("common.delete")}>
                <Trash2 />
              </Button>
            )}
          </div>
        }
      />
      {actionError && (
        <div className="mb-4">
          <Alert>{actionError}</Alert>
        </div>
      )}
      <Tabs defaultValue="collections">
        <TabsList>
          <TabsTrigger value="collections">{t("projects.tab.collections")}</TabsTrigger>
          <TabsTrigger value="overview">{t("projects.tab.overview")}</TabsTrigger>
          <TabsTrigger value="members">{t("projects.tab.members")}</TabsTrigger>
          <TabsTrigger value="jobs">{t("nav.jobs")}</TabsTrigger>
          <TabsTrigger value="evaluation">{t("projects.tab.evaluation")}</TabsTrigger>
          <TabsTrigger value="exports">{t("projects.tab.exports")}</TabsTrigger>
          <TabsTrigger value="training">{t("projects.tab.training")}</TabsTrigger>
          <TabsTrigger value="annotations">{t("projects.tab.annotations")}</TabsTrigger>
          <TabsTrigger value="report">{t("projects.tab.report")}</TabsTrigger>
          <TabsTrigger value="activity">{t("projects.tab.activity")}</TabsTrigger>
        </TabsList>
        <TabsContent value="jobs">
          <JobsPanel projectId={p.id} canRun={can("run_recognition")} />
        </TabsContent>
        <TabsContent value="evaluation">
          <div className="space-y-5">
            <EvaluationPanel project={p} />
            <AnalyticsPanel project={p} />
          </div>
        </TabsContent>
        <TabsContent value="training">
          <TrainingPanel project={p} />
        </TabsContent>
        <TabsContent value="annotations">
          <AnnotationsPanel project={p} />
        </TabsContent>
        <TabsContent value="exports">
          <ExportsPanel project={p} />
        </TabsContent>
        <TabsContent value="report">
          <ReportPanel project={p} />
        </TabsContent>
        <TabsContent value="collections">
          <Collections project={p} onChange={project.reload} />
        </TabsContent>
        <TabsContent value="overview">
          <Card>
            <dl className="grid gap-x-8 gap-y-4 px-5 py-5 text-sm sm:grid-cols-[200px_minmax(0,1fr)]">
              <dt className="text-muted">{t("projects.field.description")}</dt>
              <dd className="whitespace-pre-line text-ink-2">{p.description || t("common.none")}</dd>
              <dt className="text-muted">{t("projects.owner")}</dt>
              <dd className="text-ink-2">{p.owner_name}</dd>
              <dt className="text-muted">{t("projects.field.funding")}</dt>
              <dd className="text-ink-2">{p.funding || t("common.none")}</dd>
              <dt className="text-muted">{t("projects.yourPermissions")}</dt>
              <dd className="flex flex-wrap gap-1">
                {p.permissions.map((x) => (
                  <Badge key={x} tone="accent">
                    {x}
                  </Badge>
                ))}
              </dd>
              <dt className="text-muted">ID</dt>
              <dd className="font-mono text-xs text-muted">{p.id}</dd>
              <dt className="text-muted">{formatDate(p.created_at)}</dt>
              <dd />
            </dl>
          </Card>
        </TabsContent>
        <TabsContent value="members">
          <Members project={p} />
        </TabsContent>
        <TabsContent value="activity">
          <Activity projectId={p.id} />
        </TabsContent>
      </Tabs>
    </>
  );
}

function Collections({ project, onChange }: { project: Project; onChange: () => void }) {
  const { t } = useI18n();
  const list = useResource<Collection[]>(`/projects/${project.id}/collections`);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Collection | null>(null);
  const can = project.permissions.includes("manage_collections");

  async function remove(c: Collection) {
    if (!window.confirm(t("common.confirmDelete"))) return;
    await api(`/projects/${project.id}/collections/${c.id}`, { method: "DELETE" });
    list.reload();
    onChange();
  }

  return (
    <Card>
      <CardHeader
        title={t("projects.tab.collections")}
        description={t("collections.documentsPending")}
        action={
          can && (
            <Dialog
              open={open}
              onOpenChange={(o) => {
                setOpen(o);
                if (!o) setEditing(null);
              }}
            >
              <DialogTrigger asChild>
                <Button size="sm">
                  <Plus /> {t("collections.new")}
                </Button>
              </DialogTrigger>
              <DialogContent title={editing ? t("common.edit") : t("collections.new")}>
                <CollectionForm
                  projectId={project.id}
                  collection={editing}
                  onSaved={() => {
                    setOpen(false);
                    setEditing(null);
                    list.reload();
                    onChange();
                  }}
                />
              </DialogContent>
            </Dialog>
          )
        }
      />
      {list.error && <Alert>{list.error.message}</Alert>}
      {list.data && list.data.length === 0 && <EmptyState title={t("collections.empty")} />}
      {list.data && list.data.length > 0 && (
        <Table data-testid="collection-table">
          <thead>
            <tr>
              <Th>{t("collections.field.title")}</Th>
              <Th>{t("collections.field.archivalReference")}</Th>
              <Th>{t("collections.field.holding")}</Th>
              <Th>{t("collections.field.dc")}</Th>
              <Th className="text-right">
                <span className="sr-only">{t("common.edit")}</span>
              </Th>
            </tr>
          </thead>
          <tbody>
            {list.data.map((c) => (
              <tr key={c.id}>
                <Td>
                  <Link href={`/projects/${project.id}/collections/${c.id}`} className="font-medium text-ink hover:text-accent hover:underline">
                    {c.title}
                  </Link>
                  {c.description && <div className="mt-0.5 line-clamp-2 text-xs text-muted">{c.description}</div>}
                </Td>
                <Td className="font-mono text-xs">{c.archival_reference ?? t("common.none")}</Td>
                <Td>{c.holding_institution ?? t("common.none")}</Td>
                <Td className="text-xs">
                  {Object.entries(c.dc_metadata).map(([k, v]) => (
                    <div key={k}>
                      <span className="font-mono text-muted">dc:{k}</span> {Array.isArray(v) ? v.join(", ") : v}
                    </div>
                  ))}
                </Td>
                <Td className="whitespace-nowrap text-right">
                  {can && (
                    <>
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label={t("common.edit")}
                        onClick={() => {
                          setEditing(c);
                          setOpen(true);
                        }}
                      >
                        <Pencil />
                      </Button>
                      <Button variant="ghost" size="icon" aria-label={t("common.delete")} onClick={() => remove(c)}>
                        <Trash2 />
                      </Button>
                    </>
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

function CollectionForm({ projectId, collection, onSaved }: { projectId: string; collection: Collection | null; onSaved: () => void }) {
  const { t } = useI18n();
  const [error, setError] = useState<string | null>(null);
  const dc = collection?.dc_metadata ?? {};
  const str = (v: unknown) => (Array.isArray(v) ? v.join(", ") : ((v as string) ?? ""));

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const get = (k: string) => String(f.get(k) || "").trim() || null;
    const dcOut: Record<string, string> = { ...(dc as Record<string, string>) };
    for (const k of ["coverage", "date", "rights"]) {
      const v = get(`dc_${k}`);
      if (v) dcOut[k] = v;
      else delete dcOut[k];
    }
    const body = {
      title: get("title"),
      description: get("description"),
      archival_reference: get("archival_reference"),
      holding_institution: get("holding_institution"),
      dc_metadata: dcOut,
    };
    try {
      if (collection) await api(`/projects/${projectId}/collections/${collection.id}`, { method: "PATCH", json: body });
      else await api(`/projects/${projectId}/collections`, { method: "POST", json: body });
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.body.message : t("common.error"));
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-4">
      {error && <Alert>{error}</Alert>}
      <Field label={t("collections.field.title")} htmlFor="c_title">
        <Input id="c_title" name="title" defaultValue={collection?.title} required />
      </Field>
      <Field label={t("collections.field.description")} htmlFor="c_description" optional={t("common.optional")}>
        <Textarea id="c_description" name="description" defaultValue={collection?.description ?? ""} />
      </Field>
      <Field label={t("collections.field.archivalReference")} htmlFor="c_ref" hint={t("collections.field.archivalReference.hint")}>
        <Input id="c_ref" name="archival_reference" defaultValue={collection?.archival_reference ?? ""} />
      </Field>
      <Field label={t("collections.field.holding")} htmlFor="c_holding" optional={t("common.optional")}>
        <Input id="c_holding" name="holding_institution" defaultValue={collection?.holding_institution ?? ""} />
      </Field>
      <fieldset className="space-y-3 rounded-[var(--radius-md)] border border-rule p-3">
        <legend className="px-1 text-xs font-medium uppercase tracking-[0.07em] text-muted">{t("collections.field.dc")}</legend>
        <Field label={t("collections.field.dcCoverage")} htmlFor="dc_coverage">
          <Input id="dc_coverage" name="dc_coverage" defaultValue={str(dc.coverage)} />
        </Field>
        <Field label={t("collections.field.dcDate")} htmlFor="dc_date" hint="ISO 8601, e.g. 1720/1755">
          <Input id="dc_date" name="dc_date" defaultValue={str(dc.date)} />
        </Field>
        <Field label={t("collections.field.dcRights")} htmlFor="dc_rights">
          <Input id="dc_rights" name="dc_rights" defaultValue={str(dc.rights)} />
        </Field>
      </fieldset>
      <div className="flex justify-end">
        <Button type="submit">{collection ? t("common.save") : t("common.create")}</Button>
      </div>
    </form>
  );
}

function Members({ project }: { project: Project }) {
  const { t } = useI18n();
  const list = useResource<Member[]>(`/projects/${project.id}/members`);
  const [error, setError] = useState<string | null>(null);
  const can = project.permissions.includes("manage_members");

  async function add(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const f = new FormData(form);
    setError(null);
    try {
      await api(`/projects/${project.id}/members`, { method: "PUT", json: { email: f.get("email"), role: f.get("role") } });
      form.reset();
      list.reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.body.message : t("common.error"));
    }
  }

  async function remove(m: Member) {
    await api(`/projects/${project.id}/members/${m.user_id}`, { method: "DELETE" });
    list.reload();
  }

  return (
    <Card>
      <CardHeader title={t("projects.tab.members")} />
      {can && (
        <form onSubmit={add} className="flex flex-wrap items-end gap-3 border-b border-rule px-5 py-4">
          <Field label={t("auth.email")} htmlFor="m_email">
            <Input id="m_email" name="email" type="email" required className="w-72" />
          </Field>
          <Field label={t("members.role")} htmlFor="m_role">
            <Select id="m_role" name="role" defaultValue="researcher" className="w-60">
              {PROJECT_ROLES.map((r) => (
                <option key={r} value={r}>
                  {t(`members.role.${r}` as MessageKey)}
                </option>
              ))}
            </Select>
          </Field>
          <Button type="submit" variant="secondary">
            {t("members.add")}
          </Button>
          {error && (
            <div className="w-full">
              <Alert>{error}</Alert>
            </div>
          )}
        </form>
      )}
      <Table>
        <tbody>
          {list.data?.map((m) => (
            <tr key={m.user_id}>
              <Td className="font-medium text-ink">{m.full_name}</Td>
              <Td>{m.email}</Td>
              <Td>
                <Badge tone={m.role === "owner" ? "accent" : "neutral"}>{t(`members.role.${m.role}` as MessageKey)}</Badge>
              </Td>
              <Td className="text-right">
                {can && m.role !== "owner" && (
                  <Button variant="ghost" size="sm" onClick={() => remove(m)}>
                    {t("common.remove")}
                  </Button>
                )}
              </Td>
            </tr>
          ))}
        </tbody>
      </Table>
    </Card>
  );
}

function Activity({ projectId }: { projectId: string }) {
  const { t } = useI18n();
  const { data } = useResource<Paged<AuditEntry>>(`/audit-logs?project_id=${projectId}&limit=100`);
  return (
    <Card>
      <CardHeader title={t("projects.tab.activity")} description={t("audit.note")} />
      {data && <ActivityList items={data.items} />}
    </Card>
  );
}
