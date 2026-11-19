"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Alert, Button, Field, Input } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function RegisterPage() {
  const { t, locale } = useI18n();
  const { login } = useAuth();
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const email = String(f.get("email"));
    const password = String(f.get("password"));
    setBusy(true);
    setError(null);
    try {
      await api("/auth/register", {
        method: "POST",
        json: { email, password, full_name: String(f.get("full_name")), preferred_locale: locale },
      });
      await login(email, password);
      router.replace("/dashboard");
    } catch (err) {
      setError(err instanceof ApiError ? err.body.message : t("common.error"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h1 className="font-serif text-[26px] font-semibold text-ink">{t("auth.register.title")}</h1>
      <p className="mt-1 text-sm text-muted">{t("auth.register.firstAdmin")}</p>
      <form onSubmit={onSubmit} className="mt-8 space-y-4">
        {error && <Alert>{error}</Alert>}
        <Field label={t("auth.fullName")} htmlFor="full_name">
          <Input id="full_name" name="full_name" autoComplete="name" required autoFocus />
        </Field>
        <Field label={t("auth.email")} htmlFor="email">
          <Input id="email" name="email" type="email" autoComplete="email" required />
        </Field>
        <Field label={t("auth.password")} htmlFor="password" hint={t("auth.password.hint")}>
          <Input id="password" name="password" type="password" autoComplete="new-password" minLength={10} required />
        </Field>
        <Button type="submit" className="w-full" disabled={busy}>
          {busy ? t("common.loading") : t("auth.register")}
        </Button>
      </form>
      <p className="mt-6 text-sm text-muted">
        {t("auth.haveAccount")}{" "}
        <Link href="/login" className="text-accent hover:underline">
          {t("auth.signin")}
        </Link>
      </p>
    </>
  );
}
