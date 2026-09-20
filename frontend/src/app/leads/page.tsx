"use client";

import { AppShell } from "@/components/AppShell";
import { RequireRole } from "@/components/RequireRole";
import { Leads } from "@/components/leads/Leads";

export default function LeadsPage() {
  return (
    <AppShell>
      <RequireRole roles={["admin", "reviewer", "sales_rep", "crm_manager"]}>
        <Leads />
      </RequireRole>
    </AppShell>
  );
}
