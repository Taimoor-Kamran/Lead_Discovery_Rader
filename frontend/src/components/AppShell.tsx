"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { AlertBanner } from "@/components/AlertBanner";
import { useAuth } from "@/lib/auth";
import { navFor, ROLE_LABELS } from "@/lib/roles";

/** Navy header, role-aware navigation. Hides what a role cannot do; the API enforces it. */
export function AppShell({ children }: { children: React.ReactNode }) {
  const { user, signOut } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  const items = user ? navFor(user.role) : [];

  async function onSignOut() {
    await signOut();
    router.push("/login");
  }

  return (
    <div className="flex min-h-screen flex-col">
      <header className="bg-navy text-white">
        <div className="mx-auto flex h-14 w-full max-w-[1600px] items-center gap-6 px-4">
          <Link href="/" className="flex items-center gap-2 font-semibold tracking-tight">
            <span className="inline-block h-2.5 w-2.5 rounded-full bg-teal-400" aria-hidden="true" />
            Lead Discovery Radar
          </Link>
          <nav aria-label="Main" className="flex items-center gap-1">
            {items.map((item) => {
              const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  aria-current={active ? "page" : undefined}
                  className={`rounded px-3 py-1.5 text-sm ${
                    active ? "bg-teal-600 text-white" : "text-slate-200 hover:bg-navy-700"
                  }`}
                >
                  {item.label}
                </Link>
              );
            })}
          </nav>
          <div className="ml-auto flex items-center gap-3 text-sm">
            {user ? (
              <>
                <span className="text-slate-200" data-testid="whoami">
                  {user.email} · {ROLE_LABELS[user.role]}
                </span>
                <Link href="/profile" className="text-slate-200 underline" data-testid="profile-link">
                  Profile
                </Link>
                <button type="button" onClick={onSignOut} className="btn-secondary !py-1">
                  Sign out
                </button>
              </>
            ) : null}
          </div>
        </div>
      </header>
      <AlertBanner />
      <main className="mx-auto w-full max-w-[1600px] flex-1 px-4 py-6">{children}</main>
    </div>
  );
}
