"use client";

import { Plus } from "lucide-react";
import { useState } from "react";
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
  Td,
} from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import type { MessageKey } from "@/i18n/messages";
import { InviteUserDialog } from "@/components/InviteUser";
import { api, ApiError, formatBytes, type Institution, type Member } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useResource } from "@/lib/hooks";

export default function InstitutionsPage() {
  const { t } = useI18n();
  const { user, reload: reloadMe } = useAuth();
  const list = useResource<Institution[]>("/institutions");
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function create(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const get = (k: string) => String(f.get(k) || "").trim() || null;
    setError(null);
    try {
      await api("/institutions", {
        method: "POST",
        json: { name: get("name"), slug: get("slug"), country: get("country")?.toUpperCase() ?? null, ror_id: get("ror_id"), website: get("website") },
      });
      setOpen(false);
      list.reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.body.message : t("common.error"));
    }
  }

  return (
    <>
      <PageHeader
        title={t("institutions.title")}
        action={
          user?.is_platform_admin && (
            <Dialog open={open} onOpenChange={setOpen}>
              <DialogTrigger asChild>
                <Button>
                  <Plus /> {t("institutions.new")}
                </Button>
              </DialogTrigger>
              <DialogContent title={t("institutions.new")}>
                <form onSubmit={create} className="space-y-4">
                  {error && <Alert>{error}</Alert>}
                  <Field label={t("institutions.field.name")} htmlFor="name">
                    <Input id="name" name="name" required minLength={2} />
                  </Field>
                  <Field label={t("institutions.field.slug")} htmlFor="slug" hint="a-z, 0-9, -">
                    <Input id="slug" name="slug" required pattern="[a-z0-9][a-z0-9\-]{1,118}" />
                  </Field>
                  <div className="grid grid-cols-2 gap-3">
                    <Field label={t("institutions.field.country")} htmlFor="country" optional={t("common.optional")}>
                      <Input id="country" name="country" maxLength={2} placeholder="PT" />
                    </Field>
                    <Field label={t("institutions.field.ror")} htmlFor="ror_id" optional={t("common.optional")}>
                      <Input id="ror_id" name="ror_id" placeholder="https://ror.org/…" />
                    </Field>
                  </div>
                  <Field label={t("institutions.field.website")} htmlFor="website" optional={t("common.optional")}>
                    <Input id="website" name="website" type="url" />
                  </Field>
                  <div className="flex justify-end">
                    <Button type="submit">{t("common.create")}</Button>
                  </div>
                </form>
              </DialogContent>
            </Dialog>
          )
        }
      />
      {list.error && <Alert>{list.error.message}</Alert>}
      {list.data && list.data.length === 0 && (
        <Card>
          <EmptyState title={t("institutions.empty")} />
        </Card>
      )}
      <div className="space-y-6">
        {list.data?.map((i) => {
          const myRole = user?.institutions.find((m) => m.institution_id === i.id)?.role;
          const isAdmin = user?.is_platform_admin || myRole === "institution_admin";
          return <InstitutionCard key={i.id} inst={i} canManage={!!isAdmin} myRole={myRole} onChange={reloadMe} />;
        })}
      </div>
    </>
  );
}

function InstitutionCard({ inst, canManage, myRole, onChange }: { inst: Institution; canManage: boolean; myRole?: string; onChange: () => void }) {
  const { t } = useI18n();
  const members = useResource<Member[]>(`/institutions/${inst.id}/members`);
  const [error, setError] = useState<string | null>(null);

  async function add(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const f = new FormData(form);
    setError(null);
    try {
      await api(`/institutions/${inst.id}/members`, { method: "PUT", json: { email: f.get("email"), role: f.get("role") } });
      form.reset();
      members.reload();
      onChange();
    } catch (err) {
      setError(err instanceof ApiError ? err.body.message : t("common.error"));
    }
  }

  async function remove(m: Member) {
    setError(null);
    try {
      await api(`/institutions/${inst.id}/members/${m.user_id}`, { method: "DELETE" });
      members.reload();
      onChange();
    } catch (err) {
      setError(err instanceof ApiError ? err.body.message : t("common.error"));
    }
  }

  return (
    <Card>
      <CardHeader
        title={inst.name}
        description={
          <span className="flex gap-3">
            <span className="font-mono">{inst.slug}</span>
            {inst.country && <span>{inst.country}</span>}
            {inst.ror_id && <span>{inst.ror_id}</span>}
          </span>
        }
        action={myRole && <Badge tone="accent">{t(`members.role.${myRole}` as MessageKey)}</Badge>}
      />
      {canManage && (
        <form onSubmit={add} className="flex flex-wrap items-end gap-3 border-b border-rule px-5 py-4">
          <Field label={t("auth.email")} htmlFor={`e-${inst.id}`}>
            <Input id={`e-${inst.id}`} name="email" type="email" required className="w-72" />
          </Field>
          <Field label={t("members.role")} htmlFor={`r-${inst.id}`}>
            <Select id={`r-${inst.id}`} name="role" defaultValue="member" className="w-60">
              <option value="member">{t("members.role.member")}</option>
              <option value="institution_admin">{t("members.role.institution_admin")}</option>
            </Select>
          </Field>
          <Button type="submit" variant="secondary">
            {t("members.add")}
          </Button>
        </form>
      )}
      {error && (
        <div className="px-5 pt-3">
          <Alert>{error}</Alert>
        </div>
      )}
      {canManage && (
        <div className="flex flex-wrap items-center gap-3 border-b border-rule px-5 py-3">
          <InviteUserDialog institution={inst} onDone={() => { members.reload(); onChange(); }} />
        </div>
      )}
      <StoragePolicyPanel inst={inst} canManage={canManage} />
      <Table>
        <tbody>
          {members.data?.map((m) => (
            <tr key={m.user_id}>
              <Td className="font-medium text-ink">{m.full_name}</Td>
              <Td>{m.email}</Td>
              <Td>
                <Badge>{t(`members.role.${m.role}` as MessageKey)}</Badge>
              </Td>
              <Td className="text-right">
                {canManage && (
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

type StoragePolicy = {
  max_storage_bytes?: number | null;
  max_upload_bytes?: number | null;
  allowed_upload_types?: string[] | null;
  export_retention_days?: number | null;
  note?: string | null;
};
type StorageInfo = {
  policy: StoragePolicy;
  effective: { max_upload_bytes: number; allowed_mime_types: string[]; max_storage_bytes: number | null; export_retention_days: number };
  usage?: { originals_bytes: number; documents: number; projects: number; export_archives: number; export_bytes: number };
};
const TYPES = ["png", "jpeg", "tiff", "pdf"] as const;
const MB = 1024 * 1024;

function StoragePolicyPanel({ inst, canManage }: { inst: Institution; canManage: boolean }) {
  const { t } = useI18n();
  const info = useResource<StorageInfo>(`/institutions/${inst.id}/storage`);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const d = info.data;
  if (!d) return null;
  const e = d.effective;
  const types = e.allowed_mime_types.map((m) => m.split("/")[1].toUpperCase()).join(", ");
  const days = e.export_retention_days ? t("sp.days", { n: e.export_retention_days }) : t("sp.forever");

  async function save(ev: React.FormEvent<HTMLFormElement>) {
    ev.preventDefault();
    const f = new FormData(ev.currentTarget);
    const num = (k: string, scale = 1) => {
      const v = String(f.get(k) || "").trim();
      return v === "" ? null : Math.round(Number(v) * scale);
    };
    const chosen = TYPES.filter((x) => f.get(`type-${x}`));
    const body: StoragePolicy = {
      max_upload_bytes: num("max_upload", MB),
      max_storage_bytes: num("quota", MB),
      export_retention_days: num("retention"),
      allowed_upload_types: chosen.length === TYPES.length ? null : chosen,
      note: String(f.get("note") || "").trim() || null,
    };
    setError(null);
    setSaved(false);
    try {
      await api(`/institutions/${inst.id}/storage-policy`, { method: "PUT", json: body });
      setSaved(true);
      info.reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.body.message : t("common.error"));
    }
  }

  const p = d.policy;
  return (
    <section className="border-b border-rule px-5 py-4" aria-labelledby={`sp-${inst.id}`}>
      <h3 id={`sp-${inst.id}`} className="text-sm font-semibold text-ink">
        {t("sp.title")}
      </h3>
      <p className="mt-1 text-sm text-ink-muted" data-testid="storage-effective">
        {t("sp.effective", { upload: formatBytes(e.max_upload_bytes), types, days })}
      </p>
      {p.note && <p className="mt-1 text-sm text-ink">{p.note}</p>}
      {d.usage && (
        <p className="mt-1 text-sm text-ink-muted" data-testid="storage-usage">
          {t("sp.usage", {
            bytes: formatBytes(d.usage.originals_bytes),
            docs: d.usage.documents,
            projects: d.usage.projects,
            archives: d.usage.export_archives,
            exportBytes: formatBytes(d.usage.export_bytes),
          })}
          {e.max_storage_bytes ? ` · ${t("sp.ofQuota", { pct: Math.round((100 * d.usage.originals_bytes) / e.max_storage_bytes) })}` : ""}
        </p>
      )}
      {canManage && (
        <form onSubmit={save} className="mt-3 space-y-3">
          <p className="text-xs text-ink-muted">{t("sp.help")}</p>
          {error && <Alert>{error}</Alert>}
          {saved && <Alert tone="success">{t("sp.saved")}</Alert>}
          <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
            <Field label={t("sp.maxUpload")} htmlFor={`spu-${inst.id}`} optional={t("sp.platformDefault")}>
              <Input id={`spu-${inst.id}`} name="max_upload" type="number" min={1} step="any"
                defaultValue={p.max_upload_bytes ? p.max_upload_bytes / MB : ""} />
            </Field>
            <Field label={t("sp.quota")} htmlFor={`spq-${inst.id}`} optional={t("common.optional")}>
              <Input id={`spq-${inst.id}`} name="quota" type="number" min={1} step="any"
                defaultValue={p.max_storage_bytes ? p.max_storage_bytes / MB : ""} />
            </Field>
            <Field label={t("sp.retention")} htmlFor={`spr-${inst.id}`} optional={t("sp.platformDefault")} hint={t("sp.retention.hint")}>
              <Input id={`spr-${inst.id}`} name="retention" type="number" min={0} max={36500}
                defaultValue={p.export_retention_days ?? ""} />
            </Field>
          </div>
          <fieldset>
            <legend className="text-sm font-medium text-ink">{t("sp.types")}</legend>
            <div className="mt-1 flex gap-4">
              {TYPES.map((x) => (
                <label key={x} className="flex items-center gap-1.5 text-sm">
                  <input type="checkbox" name={`type-${x}`} defaultChecked={!p.allowed_upload_types || p.allowed_upload_types.includes(x)} />
                  {x.toUpperCase()}
                </label>
              ))}
            </div>
          </fieldset>
          <Field label={t("sp.note")} htmlFor={`spn-${inst.id}`} optional={t("common.optional")} hint={t("sp.note.hint")}>
            <Input id={`spn-${inst.id}`} name="note" maxLength={2000} defaultValue={p.note ?? ""} />
          </Field>
          <div className="flex justify-end">
            <Button type="submit" variant="secondary">{t("common.save")}</Button>
          </div>
        </form>
      )}
    </section>
  );
}
