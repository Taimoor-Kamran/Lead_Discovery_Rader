"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";
import { EmptyState, SkeletonLines } from "@/components/ui";
import type { Role } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { homeFor, ROLE_LABELS } from "@/lib/roles";

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
    // The shape of a page, not a spinner: nothing jumps when the session resolves.
    return <SkeletonLines lines={4} className="max-w-measure" />;
  }
  if (status === "anonymous" || !user) {
    return <p className="text-base text-ink-soft">Taking you to sign in…</p>;
  }
  if (forced) {
    return (
      <p className="text-base text-ink-soft" data-testid="forced-change">
        Change your password first…
      </p>
    );
  }
  if (!roles.includes(user.role)) {
    return (
      <div role="alert">
        <EmptyState
          title="Not available for your role"
          description={`A ${ROLE_LABELS[user.role].toLowerCase()} account cannot open this page. Nothing is wrong — this page belongs to another part of the team.`}
          action={
            <Link
              href={homeFor(user.role)}
              className="rounded font-medium text-accent underline underline-offset-2"
            >
              Go to your home page
            </Link>
          }
        />
      </div>
    );
  }
  return <>{children}</>;
}
