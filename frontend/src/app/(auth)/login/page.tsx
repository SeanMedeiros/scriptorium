"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Alert, Button, Field, Input } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function LoginPage() {
  const { t } = useI18n();
  const { login } = useAuth();
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    setBusy(true);
    setError(null);
    try {
      await login(String(f.get("email")), String(f.get("password")));
      router.replace("/dashboard");
    } catch (err) {
      setError(err instanceof ApiError ? err.body.message : t("common.error"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h1 className="font-serif text-[26px] font-semibold text-ink">{t("auth.signin.title")}</h1>
      <p className="mt-1 text-sm text-muted">{t("auth.signin.subtitle")}</p>
      <form onSubmit={onSubmit} className="mt-8 space-y-4" noValidate>
        {error && <Alert>{error}</Alert>}
        <Field label={t("auth.email")} htmlFor="email">
          <Input id="email" name="email" type="email" autoComplete="username" required autoFocus />
        </Field>
        <Field label={t("auth.password")} htmlFor="password">
          <Input id="password" name="password" type="password" autoComplete="current-password" required />
        </Field>
        <Button type="submit" className="w-full" disabled={busy}>
          {busy ? t("common.loading") : t("auth.signin")}
        </Button>
      </form>
      <div className="mt-6 flex justify-between text-sm">
        <Link href="/forgot-password" className="text-accent hover:underline">
          {t("auth.forgot")}
        </Link>
        <span className="text-muted">
          {t("auth.noAccount")}{" "}
          <Link href="/register" className="text-accent hover:underline">
            {t("auth.register")}
          </Link>
        </span>
      </div>
    </>
  );
}
