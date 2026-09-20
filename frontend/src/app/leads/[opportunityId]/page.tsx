"use client";

import { use } from "react";
import { AppShell } from "@/components/AppShell";
import { RequireRole } from "@/components/RequireRole";
import { LeadDetail } from "@/components/leads/LeadDetail";

export default function LeadDetailPage({
  params,
}: {
  params: Promise<{ opportunityId: string }>;
}) {
  const { opportunityId } = use(params);
  return (
    <AppShell>
      <RequireRole roles={["admin", "reviewer", "sales_rep", "crm_manager"]}>
        <LeadDetail opportunityId={opportunityId} />
      </RequireRole>
    </AppShell>
  );
}
