"use client";

import { AppShell } from "@/components/AppShell";
import { RequireRole } from "@/components/RequireRole";
import { Duplicates } from "@/components/duplicates/Duplicates";

export default function DuplicatesPage() {
  return (
    <RequireRole roles={["admin", "reviewer"]}>
      <AppShell>
        <Duplicates />
      </AppShell>
    </RequireRole>
  );
}
