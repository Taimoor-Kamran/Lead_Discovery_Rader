"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
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
    <div role="alert" data-testid="alert-banner" className={`border-b px-4 py-2 text-sm ${critical ? "border-red-300 bg-red-50 text-red-900" : "border-amber-300 bg-amber-50 text-amber-900"}`}>
      <div className="mx-auto flex w-full max-w-[1600px] flex-wrap items-center gap-3">
        <strong>{alerts.length} open alert{alerts.length === 1 ? "" : "s"}:</strong>
        <span className="flex-1">{alerts[0].message}{alerts.length > 1 ? ` (+${alerts.length - 1} more)` : ""}</span>
        <Link href="/admin/health" className="underline">Health page</Link>
        <button
          type="button"
          className="btn-secondary !py-0.5"
          onClick={async () => {
            await acknowledgeAlert(alerts[0].id).catch(() => undefined);
            await load();
          }}
        >
          Acknowledge
        </button>
      </div>
    </div>
  );
}
