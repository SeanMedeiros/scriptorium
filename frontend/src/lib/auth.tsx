"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { useI18n } from "@/i18n/I18nProvider";
import { LOCALES, type Locale } from "@/i18n/messages";
import { api, onTokenChange, refreshSession, setAccessToken, type Me } from "./api";

type AuthState = {
  user: Me | null;
  status: "loading" | "authenticated" | "anonymous";
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  reload: () => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<Me | null>(null);
  const [status, setStatus] = useState<AuthState["status"]>("loading");
  const { setLocale } = useI18n();

  const loadMe = useCallback(async () => {
    const me = await api<Me>("/me");
    setUser(me);
    setStatus("authenticated");
    if ((LOCALES as readonly string[]).includes(me.preferred_locale)) setLocale(me.preferred_locale as Locale);
  }, [setLocale]);

  useEffect(() => {
    // Restore the session from the HttpOnly refresh cookie on first load.
    refreshSession().then(async (token) => {
      if (!token) return setStatus("anonymous");
      try {
        await loadMe();
      } catch {
        setStatus("anonymous");
      }
    });
    return onTokenChange((token) => {
      if (!token) {
        setUser(null);
        setStatus("anonymous");
      }
    });
  }, [loadMe]);

  const login = useCallback(
    async (email: string, password: string) => {
      const res = await api<{ access_token: string }>("/auth/login", { method: "POST", json: { email, password } });
      setAccessToken(res.access_token);
      await loadMe();
    },
    [loadMe],
  );

  const logout = useCallback(async () => {
    try {
      await api("/auth/logout", { method: "POST" });
    } finally {
      setAccessToken(null);
    }
  }, []);

  return (
    <AuthContext.Provider value={{ user, status, login, logout, reload: loadMe }}>{children}</AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
