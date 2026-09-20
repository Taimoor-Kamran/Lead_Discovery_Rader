"use client";

import { useParams } from "next/navigation";
import { AppShell } from "@/components/AppShell";
import { RequireRole } from "@/components/RequireRole";
import { SearchPipeline } from "@/components/searches/SearchPipeline";

export default function SearchPipelinePage() {
  const params = useParams<{ searchJobId: string }>();
  return (
    <AppShell>
      <RequireRole roles={["admin", "sales_rep", "tech_admin"]}>
        <SearchPipeline searchJobId={params.searchJobId} />
      </RequireRole>
    </AppShell>
  );
}
