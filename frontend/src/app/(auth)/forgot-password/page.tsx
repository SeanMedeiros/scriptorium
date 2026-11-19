"use client";

import Link from "next/link";
import { useState } from "react";
import { Alert, Button, Field, Input } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import { api, ApiError } from "@/lib/api";

export default function ForgotPasswordPage() {
  const { t } = useI18n();
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    try {
      await api("/auth/password-reset/request", { method: "POST", json: { email: new FormData(e.currentTarget).get("email") } });
      setSent(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.body.message : t("common.error"));
    }
  }

  return (
    <>
      <h1 className="font-serif text-[26px] font-semibold text-ink">{t("auth.reset.title")}</h1>
      <div className="mt-8 space-y-4">
        {sent ? (
          <Alert tone="success">{t("auth.reset.sent")}</Alert>
        ) : (
          <form onSubmit={onSubmit} className="space-y-4">
            {error && <Alert>{error}</Alert>}
            <Field label={t("auth.email")} htmlFor="email">
              <Input id="email" name="email" type="email" required autoFocus />
            </Field>
            <Button type="submit" className="w-full">
              {t("auth.reset.request")}
            </Button>
          </form>
        )}
        <Link href="/login" className="block text-sm text-accent hover:underline">
          ← {t("auth.signin")}
        </Link>
      </div>
    </>
  );
}
