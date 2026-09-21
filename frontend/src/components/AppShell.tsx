"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { AlertBanner } from "@/components/AlertBanner";
import { Button, cx } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { navFor, ROLE_LABELS } from "@/lib/roles";

/**
 * The app shell: a slim ink bar with the product name, the role's navigation and the
 * account block, then the page. Navigation hides what a role cannot do; the API enforces
 * it. The skip link is the first focusable thing on every page.
 */
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
      <a href="#main" className="skip-link print-hide">
        Skip to content
      </a>
      <header className="print-hide bg-ink text-on-ink">
        <div className="mx-auto flex w-full max-w-shell flex-wrap items-center gap-x-6 gap-y-2 px-6 py-2">
          <Link href="/" className="flex items-center gap-2 text-base font-semibold">
            <span className="inline-block h-2 w-2 rounded-full bg-accent" aria-hidden="true" />
            Lead Discovery Radar
          </Link>
          <nav aria-label="Main" className="flex flex-wrap items-center gap-1">
            {items.map((item) => {
              const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  aria-current={active ? "page" : undefined}
                  className={cx(
                    "rounded px-2.5 py-1.5 text-sm transition-colors",
                    active ? "bg-ink-raised font-medium text-on-ink" : "text-on-ink-soft hover:bg-ink-raised hover:text-on-ink",
                  )}
                >
                  {item.label}
                </Link>
              );
            })}
          </nav>
          {user ? (
            <div className="ml-auto flex items-center gap-3">
              {/* The email and the role are text, not a link: a link would take their words
                  into its accessible name and collide with the navigation above. */}
              <span className="text-right leading-tight" data-testid="whoami">
                <span className="block text-sm text-on-ink">{user.email}</span>
                <span className="block text-xs text-on-ink-soft">{ROLE_LABELS[user.role]}</span>
              </span>
              <Link
                href="/profile"
                data-testid="profile-link"
                className="rounded text-sm text-on-ink-soft underline underline-offset-2 hover:text-on-ink"
              >
                Profile
              </Link>
              <Button size="sm" onClick={onSignOut}>
                Sign out
              </Button>
            </div>
          ) : null}
        </div>
      </header>
      <AlertBanner />
      <main id="main" className="mx-auto w-full max-w-shell flex-1 px-6 py-6">
        {children}
      </main>
    </div>
  );
}
