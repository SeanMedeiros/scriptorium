"use client";

import { useState } from "react";
import { Alert, Button, Field, Input, Select, Textarea } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import { api, ApiError, type Project } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { parseLanguages } from "@/lib/utils";

function fieldErrors(err: ApiError): Record<string, string> {
  const out: Record<string, string> = {};
  if (Array.isArray(err.body.details)) {
    for (const d of err.body.details as { loc: (string | number)[]; msg: string }[]) {
      out[String(d.loc[d.loc.length - 1])] = d.msg.replace(/^Value error, /, "");
    }
  }
  return out;
}

export function ProjectForm({ project, onSaved }: { project?: Project; onSaved: (p: Project) => void }) {
  const { t } = useI18n();
  const { user } = useAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const institutions = user?.institutions ?? [];

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const num = (k: string) => (f.get(k) ? Number(f.get(k)) : null);
    const body: Record<string, unknown> = {
      title: String(f.get("title")).trim(),
      description: String(f.get("description") || "") || null,
      languages: parseLanguages(String(f.get("languages") || "")),
      period_start: num("period_start"),
      period_end: num("period_end"),
      funding: String(f.get("funding") || "") || null,
    };
    if (!project) body.institution_id = f.get("institution_id") || null;
    setBusy(true);
    setError(null);
    setErrors({});
    try {
      const saved = project
        ? await api<Project>(`/projects/${project.id}`, { method: "PATCH", json: body })
        : await api<Project>("/projects", { method: "POST", json: body });
      onSaved(saved);
    } catch (err) {
      if (err instanceof ApiError) {
        setErrors(fieldErrors(err));
        setError(err.body.message);
      } else setError(t("common.error"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-4">
      {error && <Alert>{error}</Alert>}
      <Field label={t("projects.field.title")} htmlFor="title" error={errors.title}>
        <Input id="title" name="title" defaultValue={project?.title} required minLength={2} />
      </Field>
      {!project && (
        <Field label={t("projects.field.institution")} htmlFor="institution_id">
          <Select id="institution_id" name="institution_id" defaultValue="">
            <option value="">{t("projects.field.independent")}</option>
            {institutions.map((i) => (
              <option key={i.institution_id} value={i.institution_id}>
                {i.institution_name}
              </option>
            ))}
          </Select>
        </Field>
      )}
      <Field label={t("projects.field.description")} htmlFor="description" optional={t("common.optional")}>
        <Textarea id="description" name="description" defaultValue={project?.description ?? ""} />
      </Field>
      <Field
        label={t("projects.field.languages")}
        htmlFor="languages"
        hint={t("projects.field.languages.hint")}
        error={errors.languages}
        optional={t("common.optional")}
      >
        <Input id="languages" name="languages" defaultValue={project?.languages.join(", ")} placeholder="por, lat" />
      </Field>
      <fieldset className="grid grid-cols-2 gap-3">
        <legend className="sr-only">{t("projects.field.period")}</legend>
        <Field label={t("projects.field.periodStart")} htmlFor="period_start" error={errors.period_start}>
          <Input id="period_start" name="period_start" type="number" defaultValue={project?.period_start ?? ""} />
        </Field>
        <Field label={t("projects.field.periodEnd")} htmlFor="period_end" error={errors.period_end}>
          <Input id="period_end" name="period_end" type="number" defaultValue={project?.period_end ?? ""} />
        </Field>
      </fieldset>
      <Field label={t("projects.field.funding")} htmlFor="funding" optional={t("common.optional")}>
        <Input id="funding" name="funding" defaultValue={project?.funding ?? ""} />
      </Field>
      <div className="flex justify-end gap-2 pt-2">
        <Button type="submit" disabled={busy}>
          {busy ? t("common.saving") : project ? t("common.save") : t("common.create")}
        </Button>
      </div>
    </form>
  );
}
