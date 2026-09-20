"use client";

import { use } from "react";
import { AppShell } from "@/components/AppShell";
import { RequireRole } from "@/components/RequireRole";
import { BusinessReview } from "@/components/review/BusinessReview";

export default function BusinessReviewPage({
  params,
}: {
  params: Promise<{ businessId: string }>;
}) {
  const { businessId } = use(params);
  return (
    <RequireRole roles={["admin", "reviewer", "crm_manager", "tech_admin"]}>
      <AppShell>
        <BusinessReview businessId={businessId} />
      </AppShell>
    </RequireRole>
  );
}
