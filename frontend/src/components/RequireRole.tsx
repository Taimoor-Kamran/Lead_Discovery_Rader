"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";
import type { Role } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { homeFor } from "@/lib/roles";

/**
 * Gate a page on the session and the role. Anonymous visitors go to `/login`; a signed-in
 * user with the wrong role sees a plain refusal (the API refuses too, with a 403).
 */
export function RequireRole({
  roles,
  children,
}: {
  roles: readonly Role[];
  children: React.ReactNode;
}) {
  const { status, user } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  // A user created or reset by an admin can only change their password (the API refuses
  // everything else with 403 password_change_required), so every page sends them there.
  const forced = Boolean(user?.must_change_password) && pathname !== "/profile";

  useEffect(() => {
    if (status === "anonymous") router.replace("/login");
    else if (forced) router.replace("/profile?forced=1");
  }, [status, forced, router]);

  if (status === "loading") {
    return <p className="p-6 text-sm text-slate-600">Loading…</p>;
  }
  if (status === "anonymous" || !user) {
    return <p className="p-6 text-sm text-slate-600">Redirecting to sign in…</p>;
  }
  if (forced) {
    return <p className="p-6 text-sm text-slate-600" data-testid="forced-change">You must change your password first…</p>;
  }
  if (!roles.includes(user.role)) {
    return (
      <div className="p-6" role="alert">
        <h1 className="text-lg font-semibold">Not available for your role</h1>
        <p className="mt-1 text-sm text-slate-600">
          Your account ({user.role}) cannot open this page.{" "}
          <a href={homeFor(user.role)} className="text-teal-700 underline">
            Go to your home page
          </a>
          .
        </p>
      </div>
    );
  }
  return <>{children}</>;
}
