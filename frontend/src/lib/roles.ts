/**
 * Who sees what (blueprint slide 7). The API enforces every one of these; the UI only
 * hides what a role cannot do, so nothing here is a security boundary.
 */

import type { Role } from "./api";

export type NavItem = { href: string; label: string; roles: readonly Role[] };

export const NAV_ITEMS: readonly NavItem[] = [
  { href: "/review", label: "Review queue", roles: ["admin", "reviewer", "crm_manager", "tech_admin"] },
  { href: "/duplicates", label: "Duplicates", roles: ["admin", "reviewer"] },
  { href: "/leads", label: "My leads", roles: ["admin", "reviewer", "sales_rep", "crm_manager"] },
  { href: "/searches", label: "Searches", roles: ["admin", "sales_rep", "tech_admin"] },
  { href: "/crm", label: "CRM", roles: ["admin", "crm_manager", "tech_admin"] },
  { href: "/admin/suppressions", label: "Suppressions", roles: ["admin"] },
  { href: "/admin/users", label: "Users", roles: ["admin"] },
  { href: "/admin/health", label: "Health", roles: ["admin", "tech_admin"] },
];

export function navFor(role: Role): NavItem[] {
  return NAV_ITEMS.filter((item) => item.roles.includes(role));
}

export function homeFor(role: Role): string {
  return navFor(role)[0]?.href ?? "/login";
}

export function canDecide(role: Role): boolean {
  return role === "admin" || role === "reviewer";
}

export function canSeeAllLeads(role: Role): boolean {
  return role !== "sales_rep";
}

export function canPickAssignee(role: Role): boolean {
  return role === "admin" || role === "reviewer";
}

/** Who may create and run a search. A tech admin only reads the searches page. */
export function canRunSearches(role: Role): boolean {
  return role === "admin" || role === "sales_rep";
}

/** Who sees the alert banner and the health page. */
export function canSeeAlerts(role: Role): boolean {
  return role === "admin" || role === "tech_admin";
}

/** Who may send, retry and export leads. A tech admin only reads the CRM page. */
export function canManageCrm(role: Role): boolean {
  return role === "admin" || role === "crm_manager";
}

export const ROLE_LABELS: Record<Role, string> = {
  admin: "Admin",
  reviewer: "Reviewer",
  sales_rep: "Sales rep",
  crm_manager: "CRM manager",
  tech_admin: "Tech admin",
};
