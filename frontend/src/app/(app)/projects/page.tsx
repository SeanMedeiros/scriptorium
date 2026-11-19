"use client";

import { Plus } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { ProjectForm } from "@/components/ProjectForm";
import { Alert, Badge, Button, Card, Dialog, DialogContent, DialogTrigger, EmptyState, Input, PageHeader } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import type { Paged, Project } from "@/lib/api";
import { useResource } from "@/lib/hooks";
import { formatPeriod } from "@/lib/utils";

function ProjectsInner() {
  const { t, formatDate } = useI18n();
  const router = useRouter();
  const params = useSearchParams();
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [archived, setArchived] = useState(false);
  const [open, setOpen] = useState(params.get("new") === "1");
  const qs = new URLSearchParams({ limit: "100", ...(query ? { q: query } : {}), ...(archived ? { include_archived: "true" } : {}) });
  const { data, error, loading } = useResource<Paged<Project>>(`/projects?${qs}`);

  useEffect(() => {
    const id = setTimeout(() => setQuery(q.trim()), 250);
    return () => clearTimeout(id);
  }, [q]);

  return (
    <>
      <PageHeader
        title={t("projects.title")}
        subtitle={data ? `${data.total}` : undefined}
        action={
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>
              <Button>
                <Plus /> {t("projects.new")}
              </Button>
            </DialogTrigger>
            <DialogContent title={t("projects.new")}>
              <ProjectForm onSaved={(p) => router.push(`/projects/${p.id}`)} />
            </DialogContent>
          </Dialog>
        }
      />
      <div className="mb-4 flex items-center gap-4">
        <Input className="max-w-xs" placeholder={t("common.search")} value={q} onChange={(e) => setQ(e.target.value)} aria-label={t("common.search")} />
        <label className="flex items-center gap-2 text-sm text-ink-2">
          <input type="checkbox" checked={archived} onChange={(e) => setArchived(e.target.checked)} className="accent-[var(--color-accent)]" />
          {t("projects.showArchived")}
        </label>
      </div>
      {error && <Alert>{error.message}</Alert>}
      {data && data.items.length === 0 && !loading && (
        <Card>
          <EmptyState title={t("projects.empty")} body={t("dashboard.empty.body")} />
        </Card>
      )}
      {data && data.items.length > 0 && (
        <Card>
          <ul className="divide-y divide-rule" data-testid="project-list">
            {data.items.map((p) => (
              <li key={p.id}>
                <Link href={`/projects/${p.id}`} className="grid grid-cols-[minmax(0,1fr)_auto] gap-6 px-5 py-4 hover:bg-paper">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="truncate font-serif text-[17px] font-semibold text-ink">{p.title}</span>
                      {p.is_archived && <Badge tone="warn">{t("projects.archived")}</Badge>}
                    </div>
                    <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
                      <span>{p.institution_name ?? t("projects.field.independent")}</span>
                      {formatPeriod(p.period_start, p.period_end) && <span className="tabular">{formatPeriod(p.period_start, p.period_end)}</span>}
                      {p.languages.length > 0 && <span className="font-mono">{p.languages.join(" · ")}</span>}
                    </div>
                    {p.description && <p className="mt-1.5 line-clamp-2 text-sm text-ink-2">{p.description}</p>}
                  </div>
                  <div className="text-right text-xs text-muted">
                    <div>{t("projects.collections", { n: p.collection_count })}</div>
                    <div>{t("projects.members", { n: p.member_count })}</div>
                    <div className="mt-1">{formatDate(p.updated_at, false)}</div>
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </>
  );
}

export default function ProjectsPage() {
  return (
    <Suspense>
      <ProjectsInner />
    </Suspense>
  );
}
