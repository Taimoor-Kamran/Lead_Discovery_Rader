"use client";

import { useRouter } from "next/navigation";
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

  useEffect(() => {
    if (status === "anonymous") router.replace("/login");
  }, [status, router]);

  if (status === "loading") {
    return <p className="p-6 text-sm text-slate-600">Loading…</p>;
  }
  if (status === "anonymous" || !user) {
    return <p className="p-6 text-sm text-slate-600">Redirecting to sign in…</p>;
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
