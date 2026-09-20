"use client";

import { AppShell } from "@/components/AppShell";
import { RequireRole } from "@/components/RequireRole";
import { ReviewQueue } from "@/components/review/ReviewQueue";

/** The queue page. Selection enables batch reject / not-a-fit only; nothing else is batched. */
export default function ReviewQueuePage() {
  return (
    <RequireRole roles={["admin", "reviewer", "crm_manager", "tech_admin"]}>
      <AppShell>
        <ReviewQueue />
      </AppShell>
    </RequireRole>
  );
}
