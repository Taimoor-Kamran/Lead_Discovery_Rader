"use client";

import { AppShell } from "@/components/AppShell";
import { RequireRole } from "@/components/RequireRole";
import { Leads } from "@/components/leads/Leads";

export default function LeadsPage() {
  return (
    <RequireRole roles={["admin", "reviewer", "sales_rep", "crm_manager"]}>
      <AppShell>
        <Leads />
      </AppShell>
    </RequireRole>
  );
}
