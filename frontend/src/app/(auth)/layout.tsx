"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { Wordmark } from "@/components/Brand";
import { LocaleSwitcher } from "@/components/LocaleSwitcher";
import { useI18n } from "@/i18n/I18nProvider";
import { useAuth } from "@/lib/auth";

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  const { status } = useAuth();
  const router = useRouter();
  const { t } = useI18n();

  useEffect(() => {
    if (status === "authenticated") router.replace("/dashboard");
  }, [status, router]);

  return (
    <div className="grid min-h-screen lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
      <aside className="relative hidden flex-col justify-between border-r border-rule bg-sunken p-12 lg:flex">
        <Wordmark />
        <div className="max-w-md">
          <p className="font-serif text-[34px] leading-[1.15] text-ink">{t("app.tagline")}</p>
          <div className="mt-6 space-y-2 border-l-2 border-accent/40 pl-4 text-sm leading-relaxed text-ink-2">
            <p>{t("auth.signin.subtitle")}</p>
          </div>
        </div>
        <p className="text-xs text-muted">Institution-controlled processing. Documents are never sent to third-party AI services.</p>
      </aside>
      <main className="flex flex-col px-6 py-8 sm:px-12">
        <div className="flex items-center justify-between">
          <span className="lg:hidden">
            <Wordmark />
          </span>
          <LocaleSwitcher className="ml-auto" />
        </div>
        <div className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center py-10">{children}</div>
      </main>
    </div>
  );
}
