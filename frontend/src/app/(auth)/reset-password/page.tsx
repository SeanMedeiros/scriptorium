"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { Alert, Button, Field, Input } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import { api, ApiError } from "@/lib/api";

function ResetForm() {
  const { t } = useI18n();
  const token = useSearchParams().get("token");
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!token) return <Alert>{t("auth.reset.missingToken")}</Alert>;

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    try {
      await api("/auth/password-reset/confirm", {
        method: "POST",
        json: { token, new_password: new FormData(e.currentTarget).get("password") },
      });
      setDone(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.body.message : t("common.error"));
    }
  }

  return done ? (
    <Alert tone="success">{t("auth.reset.done")}</Alert>
  ) : (
    <form onSubmit={onSubmit} className="space-y-4">
      {error && <Alert>{error}</Alert>}
      <Field label={t("auth.reset.newPassword")} htmlFor="password" hint={t("auth.password.hint")}>
        <Input id="password" name="password" type="password" autoComplete="new-password" minLength={10} required autoFocus />
      </Field>
      <Button type="submit" className="w-full">
        {t("auth.reset.confirm")}
      </Button>
    </form>
  );
}

export default function ResetPasswordPage() {
  const { t } = useI18n();
  return (
    <>
      <h1 className="font-serif text-[26px] font-semibold text-ink">{t("auth.reset.title")}</h1>
      <div className="mt-8 space-y-4">
        <Suspense>
          <ResetForm />
        </Suspense>
        <Link href="/login" className="block text-sm text-accent hover:underline">
          ← {t("auth.signin")}
        </Link>
      </div>
    </>
  );
}
