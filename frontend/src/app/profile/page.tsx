"use client";

import { AppShell } from "@/components/AppShell";
import { RequireRole } from "@/components/RequireRole";
import { ChangePassword } from "@/components/profile/ChangePassword";

export default function ProfilePage() {
  return (
    <AppShell>
      <RequireRole roles={["admin", "reviewer", "sales_rep", "crm_manager", "tech_admin"]}>
        <ChangePassword />
      </RequireRole>
    </AppShell>
  );
}
