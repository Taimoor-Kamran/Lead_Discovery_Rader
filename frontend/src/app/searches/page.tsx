"use client";

import { AppShell } from "@/components/AppShell";
import { RequireRole } from "@/components/RequireRole";
import { Searches } from "@/components/searches/Searches";

export default function SearchesPage() {
  return (
    <AppShell>
      <RequireRole roles={["admin", "sales_rep", "tech_admin"]}>
        <Searches />
      </RequireRole>
    </AppShell>
  );
}
