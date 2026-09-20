"use client";

import { AppShell } from "@/components/AppShell";
import { RequireRole } from "@/components/RequireRole";
import { HealthPage } from "@/components/admin/HealthPage";

export default function AdminHealthPage() {
  return (
    <AppShell>
      <RequireRole roles={["admin", "tech_admin"]}>
        <HealthPage />
      </RequireRole>
    </AppShell>
  );
}
