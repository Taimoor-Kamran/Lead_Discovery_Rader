"use client";

import { AppShell } from "@/components/AppShell";
import { RequireRole } from "@/components/RequireRole";
import { CrmDashboard } from "@/components/crm/CrmDashboard";

export default function CrmPage() {
  return (
    <AppShell>
      <RequireRole roles={["admin", "crm_manager", "tech_admin"]}>
        <CrmDashboard />
      </RequireRole>
    </AppShell>
  );
}
