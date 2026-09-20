"use client";

import { AppShell } from "@/components/AppShell";
import { RequireRole } from "@/components/RequireRole";
import { Users } from "@/components/admin/Users";

export default function UsersPage() {
  return (
    <AppShell>
      <RequireRole roles={["admin"]}>
        <Users />
      </RequireRole>
    </AppShell>
  );
}
