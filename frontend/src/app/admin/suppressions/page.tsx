"use client";

import { AppShell } from "@/components/AppShell";
import { RequireRole } from "@/components/RequireRole";
import { Suppressions } from "@/components/admin/Suppressions";

export default function SuppressionsPage() {
  return (
    <RequireRole roles={["admin"]}>
      <AppShell>
        <Suppressions />
      </AppShell>
    </RequireRole>
  );
}
