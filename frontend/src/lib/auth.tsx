"use client";

/**
 * Session state for the whole app.
 *
 * On first render there is no access token (it only ever lives in memory), so the provider
 * asks `/auth/refresh` once; the httpOnly cookie either yields a new token and `/auth/me`
 * fills in the user, or the visitor is anonymous and the pages send them to `/login`.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { getMe, logout as apiLogout, refreshAccessToken, type Me } from "./api";
import { clearAccessToken, getAccessToken } from "./session";

export type AuthStatus = "loading" | "anonymous" | "authenticated";

export type AuthState = {
  status: AuthStatus;
  user: Me | null;
  /** Called after `login()` succeeded, so the provider fetches `/auth/me`. */
  signedIn: () => Promise<Me>;
  signOut: () => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({
  children,
  initial,
}: {
  children: React.ReactNode;
  /** Tests inject a state and skip the network. */
  initial?: { status: AuthStatus; user: Me | null };
}) {
  const [status, setStatus] = useState<AuthStatus>(initial?.status ?? "loading");
  const [user, setUser] = useState<Me | null>(initial?.user ?? null);

  useEffect(() => {
    if (initial) return;
    let cancelled = false;
    (async () => {
      const token = getAccessToken() ?? (await refreshAccessToken());
      if (!token) {
        if (!cancelled) setStatus("anonymous");
        return;
      }
      try {
        const me = await getMe();
        if (!cancelled) {
          setUser(me);
          setStatus("authenticated");
        }
      } catch {
        clearAccessToken();
        if (!cancelled) setStatus("anonymous");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [initial]);

  const signedIn = useCallback(async () => {
    const me = await getMe();
    setUser(me);
    setStatus("authenticated");
    return me;
  }, []);

  const signOut = useCallback(async () => {
    try {
      await apiLogout();
    } finally {
      setUser(null);
      setStatus("anonymous");
    }
  }, []);

  const value = useMemo(
    () => ({ status, user, signedIn, signOut }),
    [status, user, signedIn, signOut],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside <AuthProvider>");
  return value;
}
