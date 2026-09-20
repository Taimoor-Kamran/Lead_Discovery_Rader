"use client";

import { AppShell } from "@/components/AppShell";
import { RequireRole } from "@/components/RequireRole";
import { Duplicates } from "@/components/duplicates/Duplicates";

export default function DuplicatesPage() {
  return (
    <AppShell>
      <RequireRole roles={["admin", "reviewer"]}>
        <Duplicates />
      </RequireRole>
    </AppShell>
  );
}
