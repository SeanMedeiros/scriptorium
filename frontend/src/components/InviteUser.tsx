"use client";

import { UserPlus } from "lucide-react";
import { useState } from "react";
import { Alert, Button, Dialog, DialogContent, DialogTrigger, Field, Input, Select } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import { api, ApiError, type Institution } from "@/lib/api";

/** Invite a new user (account + single-use e-mail link to choose a password, valid 7 days).
 *  With `institution` fixed (institution administrators), the user joins that institution. */
export function InviteUserDialog({ institutions, institution, onDone }: { institutions?: Institution[]; institution?: Institution; onDone?: () => void }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState<string | null>(null);

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    e.stopPropagation(); // the dialog is portalled; never let the submit reach an enclosing form
    const f = new FormData(e.currentTarget);
    const inst = institution?.id ?? (String(f.get("institution_id") || "") || null);
    setError(null);
    try {
      await api("/admin/users", {
        method: "POST",
        json: { email: f.get("email"), full_name: f.get("full_name"), institution_id: inst, institution_role: f.get("role") || "member" },
      });
      setSent(String(f.get("email")));
      setOpen(false);
      onDone?.();
    } catch (err) {
      setError(err instanceof ApiError ? err.body.message : t("common.error"));
    }
  }

  return (
    <>
      {sent && <Alert tone="success">{t("invite.sent", { email: sent })}</Alert>}
      <Dialog open={open} onOpenChange={(o) => { setOpen(o); setError(null); }}>
        <DialogTrigger asChild>
          <Button variant={institution ? "secondary" : "primary"}>
            <UserPlus /> {t("invite.button")}
          </Button>
        </DialogTrigger>
        <DialogContent title={t("invite.button")} description={t("invite.help")}>
          <form onSubmit={submit} className="space-y-4">
            {error && <Alert>{error}</Alert>}
            <Field label={t("auth.fullName")} htmlFor="inv-name">
              <Input id="inv-name" name="full_name" required maxLength={200} />
            </Field>
            <Field label={t("auth.email")} htmlFor="inv-email">
              <Input id="inv-email" name="email" type="email" required />
            </Field>
            {!institution && institutions && (
              <Field label={t("invite.institution")} htmlFor="inv-inst" optional={t("common.optional")}>
                <Select id="inv-inst" name="institution_id" defaultValue="">
                  <option value="">{t("common.none")}</option>
                  {institutions.map((i) => <option key={i.id} value={i.id}>{i.name}</option>)}
                </Select>
              </Field>
            )}
            {(institution || institutions?.length) ? (
              <Field label={t("members.role")} htmlFor="inv-role">
                <Select id="inv-role" name="role" defaultValue="member">
                  <option value="member">{t("members.role.member")}</option>
                  <option value="institution_admin">{t("members.role.institution_admin")}</option>
                </Select>
              </Field>
            ) : null}
            <div className="flex justify-end">
              <Button type="submit">{t("invite.send")}</Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </>
  );
}
