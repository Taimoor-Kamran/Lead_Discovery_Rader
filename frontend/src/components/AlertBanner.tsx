"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Button, cx } from "@/components/ui";
import { acknowledgeAlert, getAlerts, type AlertRead } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { canSeeAlerts } from "@/lib/roles";

const REFRESH_MS = 60_000;

/** Open alerts for admins and tech admins, on every page. Acknowledge hides one. */
export function AlertBanner() {
  const { user } = useAuth();
  const visible = user ? canSeeAlerts(user.role) : false;
  const [alerts, setAlerts] = useState<AlertRead[]>([]);

  const load = useCallback(async () => {
    try {
      setAlerts(await getAlerts());
    } catch {
      // The banner is a convenience; the health page reports errors.
    }
  }, []);

  useEffect(() => {
    if (!visible) return;
    void load();
    const timer = setInterval(() => void load(), REFRESH_MS);
    return () => clearInterval(timer);
  }, [visible, load]);

  if (!visible || !alerts.length) return null;
  const critical = alerts.some((alert) => alert.severity === "critical");
  return (
    <div
      role="alert"
      data-testid="alert-banner"
      className={cx(
        "print-hide border-b text-base",
        critical ? "border-risk bg-risk-tint" : "border-warn bg-warn-tint",
      )}
    >
      <div className="mx-auto flex w-full max-w-shell flex-wrap items-center gap-x-3 gap-y-2 px-6 py-2">
        <strong className="font-medium">
          {alerts.length} open alert{alerts.length === 1 ? "" : "s"}
        </strong>
        <span className="flex-1 text-ink">
          {alerts[0].message}
          {alerts.length > 1 ? ` (+${alerts.length - 1} more)` : ""}
        </span>
        <Link href="/admin/health" className="rounded font-medium text-accent underline underline-offset-2">
          Health page
        </Link>
        <Button
          size="sm"
          onClick={async () => {
            await acknowledgeAlert(alerts[0].id).catch(() => undefined);
            await load();
          }}
        >
          Acknowledge
        </Button>
      </div>
    </div>
  );
}
