"use client";

import { useState } from "react";
import { Alert, Badge, Button, Card, Input, PageHeader, Table, Td, Th } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import { InviteUserDialog } from "@/components/InviteUser";
import { api, ApiError, type Institution, type Me, type Paged } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useResource } from "@/lib/hooks";

export default function AdminUsersPage() {
  const { t, formatDate } = useI18n();
  const { user: me } = useAuth();
  const [q, setQ] = useState("");
  const { data, error, reload } = useResource<Paged<Me>>(`/admin/users?limit=200${q ? `&q=${encodeURIComponent(q)}` : ""}`);
  const [actionError, setActionError] = useState<string | null>(null);
  const insts = useResource<Institution[]>("/institutions");

  async function update(id: string, json: Record<string, boolean>) {
    setActionError(null);
    try {
      await api(`/admin/users/${id}`, { method: "PATCH", json });
      reload();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.body.message : t("common.error"));
    }
  }

  if (error?.status === 403) return <Alert>{error.message}</Alert>;

  return (
    <>
      <PageHeader title={t("admin.users")} subtitle={data ? String(data.total) : undefined}
        action={<InviteUserDialog institutions={insts.data ?? []} onDone={reload} />} />
      <Input
        className="mb-4 max-w-xs"
        placeholder={t("common.search")}
        aria-label={t("common.search")}
        onKeyDown={(e) => e.key === "Enter" && setQ((e.target as HTMLInputElement).value.trim())}
      />
      {(error || actionError) && <Alert>{actionError ?? error?.message}</Alert>}
      <Card>
        <Table>
          <thead>
            <tr>
              <Th>{t("auth.fullName")}</Th>
              <Th>{t("auth.email")}</Th>
              <Th>{t("admin.lastLogin")}</Th>
              <Th>{t("members.role")}</Th>
              <Th />
            </tr>
          </thead>
          <tbody>
            {data?.items.map((u) => (
              <tr key={u.id} className={u.is_active ? "" : "opacity-60"}>
                <Td className="font-medium text-ink">{u.full_name}</Td>
                <Td>{u.email}</Td>
                <Td className="tabular text-xs">{u.last_login_at ? formatDate(u.last_login_at) : t("common.none")}</Td>
                <Td className="space-x-1">
                  {u.is_platform_admin && <Badge tone="accent">{t("admin.platformAdmin")}</Badge>}
                  {u.is_active ? <Badge tone="success">{t("admin.active")}</Badge> : <Badge tone="danger">{t("admin.deactivate")}</Badge>}
                </Td>
                <Td className="whitespace-nowrap text-right">
                  {u.id !== me?.id && (
                    <>
                      <Button variant="ghost" size="sm" onClick={() => update(u.id, { is_platform_admin: !u.is_platform_admin })}>
                        {t("admin.platformAdmin")} {u.is_platform_admin ? "−" : "+"}
                      </Button>
                      <Button variant={u.is_active ? "danger" : "secondary"} size="sm" onClick={() => update(u.id, { is_active: !u.is_active })}>
                        {u.is_active ? t("admin.deactivate") : t("admin.activate")}
                      </Button>
                    </>
                  )}
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      </Card>
    </>
  );
}
