"use client";

import { Boxes, Building2, FolderKanban, LayoutDashboard, LogOut, ScrollText, UserCog, Users } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";
import { Wordmark } from "@/components/Brand";
import { LocaleSwitcher } from "@/components/LocaleSwitcher";
import { useI18n } from "@/i18n/I18nProvider";
import type { MessageKey } from "@/i18n/messages";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

type NavItem = { href: string; key: MessageKey; icon: React.ComponentType<{ className?: string }>; admin?: boolean };

const RESEARCH: NavItem[] = [
  { href: "/dashboard", key: "nav.dashboard", icon: LayoutDashboard },
  { href: "/projects", key: "nav.projects", icon: FolderKanban },
  { href: "/models", key: "nav.models", icon: Boxes },
];
const GOVERNANCE: NavItem[] = [
  { href: "/institutions", key: "nav.institutions", icon: Building2 },
  { href: "/audit", key: "nav.audit", icon: ScrollText },
  { href: "/admin/users", key: "nav.admin", icon: Users, admin: true },
];

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { status, user, logout } = useAuth();
  const { t } = useI18n();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (status === "anonymous") router.replace("/login");
  }, [status, router]);

  if (status !== "authenticated" || !user) {
    return <div className="flex min-h-screen items-center justify-center text-sm text-muted">{t("common.loading")}</div>;
  }

  const link = (item: NavItem) => {
    if (item.admin && !user.is_platform_admin) return null;
    const active = pathname === item.href || pathname.startsWith(item.href + "/");
    const Icon = item.icon;
    return (
      <Link
        key={item.href}
        href={item.href}
        aria-current={active ? "page" : undefined}
        className={cn(
          "flex items-center gap-2.5 rounded-[var(--radius-md)] px-2.5 py-1.5 text-sm",
          active ? "bg-surface font-medium text-ink shadow-[0_0_0_1px_var(--color-rule)]" : "text-ink-2 hover:bg-surface/60 hover:text-ink",
        )}
      >
        <Icon className="size-4 text-muted" />
        {t(item.key)}
      </Link>
    );
  };

  return (
    <div className="grid min-h-screen grid-cols-[232px_minmax(0,1fr)]">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:bg-surface focus:p-2">
        Skip to content
      </a>
      <aside className="sticky top-0 flex h-screen flex-col border-r border-rule bg-sunken px-3 py-4">
        <Link href="/dashboard" className="px-2 pb-6">
          <Wordmark />
        </Link>
        <nav className="space-y-5" aria-label="Main">
          <div>
            <div className="px-2.5 pb-1.5 text-[11px] font-medium uppercase tracking-[0.09em] text-muted">{t("nav.section.research")}</div>
            <div className="space-y-0.5">{RESEARCH.map(link)}</div>
          </div>
          <div>
            <div className="px-2.5 pb-1.5 text-[11px] font-medium uppercase tracking-[0.09em] text-muted">{t("nav.section.governance")}</div>
            <div className="space-y-0.5">{GOVERNANCE.map(link)}</div>
          </div>
        </nav>
        <div className="mt-auto space-y-3 border-t border-rule pt-3">
          <Link href="/settings" className="flex items-center gap-2.5 rounded-[var(--radius-md)] px-2.5 py-1.5 hover:bg-surface/60">
            <span className="flex size-7 items-center justify-center rounded-full bg-accent-soft text-xs font-semibold text-accent">
              {user.full_name.slice(0, 1).toUpperCase()}
            </span>
            <span className="min-w-0">
              <span className="block truncate text-sm text-ink">{user.full_name}</span>
              <span className="block truncate text-xs text-muted">{user.email}</span>
            </span>
            <UserCog className="ml-auto size-4 text-muted" />
          </Link>
          <div className="flex items-center justify-between px-2">
            <LocaleSwitcher onChange={(l) => api("/me", { method: "PATCH", json: { preferred_locale: l } }).catch(() => undefined)} />
            <button
              onClick={() => logout().then(() => router.replace("/login"))}
              className="flex items-center gap-1.5 text-xs text-muted hover:text-ink"
            >
              <LogOut className="size-3.5" />
              {t("nav.signout")}
            </button>
          </div>
        </div>
      </aside>
      {pathname.startsWith("/transcriptions/") ? (
        <main id="main" className="min-w-0">
          {children}
        </main>
      ) : (
        <main id="main" className="min-w-0 px-10 py-8">
          <div className="mx-auto max-w-[1180px]">{children}</div>
        </main>
      )}
    </div>
  );
}
