"use client";

import { useState } from "react";
import { Alert, Badge, Button, Card, CardHeader, Field, Input, PageHeader, Select } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import { LOCALE_NAMES, LOCALES, type Locale } from "@/i18n/messages";
import { api, ApiError, type SessionInfo } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useResource } from "@/lib/hooks";

export default function SettingsPage() {
  const { t, formatDate, setLocale } = useI18n();
  const { user, reload } = useAuth();
  const sessions = useResource<SessionInfo[]>("/me/sessions");
  const [profileMsg, setProfileMsg] = useState<{ tone: "success" | "danger"; text: string } | null>(null);
  const [pwMsg, setPwMsg] = useState<{ tone: "success" | "danger"; text: string } | null>(null);
  if (!user) return null;

  async function saveProfile(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    try {
      await api("/me", {
        method: "PATCH",
        json: { full_name: f.get("full_name"), orcid: String(f.get("orcid") || "") || null, preferred_locale: f.get("locale") },
      });
      setLocale(f.get("locale") as Locale);
      await reload();
      setProfileMsg({ tone: "success", text: t("settings.saved") });
    } catch (err) {
      setProfileMsg({ tone: "danger", text: err instanceof ApiError ? err.body.message : t("common.error") });
    }
  }

  async function changePassword(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const f = new FormData(form);
    try {
      await api("/auth/password-change", {
        method: "POST",
        json: { current_password: f.get("current"), new_password: f.get("new") },
      });
      form.reset();
      setPwMsg({ tone: "success", text: t("settings.passwordChanged") });
      sessions.reload();
    } catch (err) {
      setPwMsg({ tone: "danger", text: err instanceof ApiError ? err.body.message : t("common.error") });
    }
  }

  async function revoke(s: SessionInfo) {
    await api(`/me/sessions/${s.family_id}`, { method: "DELETE" });
    sessions.reload();
  }

  return (
    <>
      <PageHeader title={t("settings.title")} subtitle={user.email} />
      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader title={t("settings.profile")} />
          <form onSubmit={saveProfile} className="space-y-4 px-5 py-4">
            {profileMsg && <Alert tone={profileMsg.tone}>{profileMsg.text}</Alert>}
            <Field label={t("auth.fullName")} htmlFor="full_name">
              <Input id="full_name" name="full_name" defaultValue={user.full_name} required />
            </Field>
            <Field label={t("settings.orcid")} htmlFor="orcid" hint="0000-0000-0000-0000" optional={t("common.optional")}>
              <Input id="orcid" name="orcid" defaultValue={user.orcid ?? ""} />
            </Field>
            <Field label={t("settings.language")} htmlFor="locale">
              <Select id="locale" name="locale" defaultValue={user.preferred_locale}>
                {LOCALES.map((l) => (
                  <option key={l} value={l}>
                    {LOCALE_NAMES[l]}
                  </option>
                ))}
              </Select>
            </Field>
            <div className="flex justify-end">
              <Button type="submit">{t("common.save")}</Button>
            </div>
          </form>
        </Card>
        <Card>
          <CardHeader title={t("settings.password")} />
          <form onSubmit={changePassword} className="space-y-4 px-5 py-4">
            {pwMsg && <Alert tone={pwMsg.tone}>{pwMsg.text}</Alert>}
            <Field label={t("settings.currentPassword")} htmlFor="current">
              <Input id="current" name="current" type="password" autoComplete="current-password" required />
            </Field>
            <Field label={t("auth.reset.newPassword")} htmlFor="new" hint={t("auth.password.hint")}>
              <Input id="new" name="new" type="password" autoComplete="new-password" minLength={10} required />
            </Field>
            <div className="flex justify-end">
              <Button type="submit">{t("common.save")}</Button>
            </div>
          </form>
        </Card>
        <Card className="lg:col-span-2">
          <CardHeader title={t("settings.sessions")} />
          <ul className="divide-y divide-rule">
            {sessions.data?.map((s) => (
              <li key={s.id} className="flex items-center gap-4 px-5 py-3 text-sm">
                <div className="min-w-0 flex-1">
                  <div className="truncate text-ink-2">{s.user_agent || t("common.none")}</div>
                  <div className="text-xs text-muted">
                    {t("settings.signedIn", { date: formatDate(s.created_at) })} · <span className="font-mono">{s.ip_address ?? "—"}</span>
                  </div>
                </div>
                {s.current ? (
                  <Badge tone="success">{t("settings.thisSession")}</Badge>
                ) : (
                  <Button variant="secondary" size="sm" onClick={() => revoke(s)}>
                    {t("settings.revoke")}
                  </Button>
                )}
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </>
  );
}
