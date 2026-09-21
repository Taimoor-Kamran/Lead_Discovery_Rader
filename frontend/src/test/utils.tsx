/**
 * Test harness: a fetch router, the providers every page needs, and fixture builders.
 * Nothing here talks to a network; `fetch` is stubbed per test.
 */

import { render, type RenderOptions } from "@testing-library/react";
import { vi } from "vitest";
import { ToastProvider } from "@/components/ui";
import type { LeadDetail, LeadRead, Me, QueueItem, ReviewDetail, ReviewOpportunity } from "@/lib/api";
import { AuthProvider, type AuthStatus } from "@/lib/auth";

export type RouteHandler =
  | { status: number; body: unknown }
  | ((init: RequestInit | undefined, url: string) => { status: number; body: unknown });

/** Match on `"METHOD /path"`; the path is compared against the end of the URL (query stripped). */
export function routeFetch(routes: Record<string, RouteHandler>) {
  const calls: { method: string; url: string; init?: RequestInit }[] = [];
  const spy = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    const path = url.replace(/^https?:\/\/[^/]+/, "").replace(/\?.*$/, "");
    calls.push({ method, url, init });
    const key = Object.keys(routes).find((candidate) => {
      const [routeMethod, routePath] = candidate.split(" ");
      return routeMethod === method && path.endsWith(routePath);
    });
    const handler = key ? routes[key] : { status: 404, body: envelope(404, "not_found", "no route") };
    const result = typeof handler === "function" ? handler(init, url) : handler;
    return new Response(result.status === 204 ? null : JSON.stringify(result.body), {
      status: result.status,
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", spy);
  return { spy, calls };
}

export function envelope(status: number, code: string, message: string, details = {}) {
  return { error: { code, message, request_id: `req-${status}`, details } };
}

export function me(
  role: Me["role"] = "reviewer",
  email = `${role}@example.com`,
  overrides: Partial<Me> = {},
): Me {
  return {
    id: `user-${role}`,
    email,
    role,
    is_active: true,
    must_change_password: false,
    locked_until: null,
    locked: false,
    rate_limited_until: null,
    rate_limited: false,
    last_login_at: "2026-09-20T09:00:00Z",
    created_at: "2026-09-20T00:00:00Z",
    updated_at: "2026-09-20T00:00:00Z",
    ...overrides,
  };
}

export const router = {
  push: vi.fn(),
  replace: vi.fn(),
  back: vi.fn(),
  forward: vi.fn(),
  refresh: vi.fn(),
  prefetch: vi.fn(),
};

export let currentPathname = "/review";
export function setPathname(pathname: string) {
  currentPathname = pathname;
}

export let currentSearchParams = new URLSearchParams();
export function setSearchParams(query: string) {
  currentSearchParams = new URLSearchParams(query);
}

export function renderWithProviders(
  ui: React.ReactElement,
  { user = me(), status = "authenticated", ...options }: RenderOptions & { user?: Me | null; status?: AuthStatus } = {},
) {
  return render(
    <AuthProvider initial={{ status, user }}>
      <ToastProvider>{ui}</ToastProvider>
    </AuthProvider>,
    options,
  );
}

export function queueItem(overrides: Partial<QueueItem> = {}): QueueItem {
  return {
    business_id: "biz-1",
    display_name: "Barton Creek Plumbing",
    city: "Austin",
    state: "TX",
    industry: "plumbing",
    website: "https://bartoncreekplumbing.invalid/",
    top_score: 0.72,
    latest_audit: { status: "done", audited_at: "2026-09-20T10:00:00Z", top_findings: ["no_https", "no_h1"] },
    opportunities: [
      {
        id: "opp-1",
        service: "website_design",
        service_name: "Website design / redesign",
        source: "rules",
        confidence: 0.8,
        score: 0.72,
        review_status: "pending",
        lock_version: 0,
        reason: "Audit found the site is served over http.",
        weak: false,
      },
    ],
    weak_hidden: 1,
    ...overrides,
  };
}

export function reviewOpportunity(overrides: Partial<ReviewOpportunity> = {}): ReviewOpportunity {
  return {
    id: "opp-1",
    business_id: "biz-1",
    business_name: "Barton Creek Plumbing",
    industry: "plumbing",
    city: "Austin",
    state: "TX",
    service: "website_design",
    service_name: "Website design / redesign",
    source: "rules",
    confidence: 0.8,
    ai_agrees: null,
    score: 0.72,
    score_components: { facts: 0.5, inference: 0.8, intent: 0, contactability: 1 },
    scoring_version: "scoring-1",
    review_status: "pending",
    lock_version: 0,
    top_evidence: null,
    created_at: "2026-09-20T10:00:00Z",
    updated_at: "2026-09-20T10:00:00Z",
    reason: "Audit found the site is served over http.",
    evidence: [
      {
        finding_code: "no_https",
        text: "served over http",
        url: "https://bartoncreekplumbing.invalid/",
        source: "rules",
      },
    ],
    website_audit_id: "audit-1",
    ai_classification_id: null,
    ai: null,
    assigned_to: null,
    decided_at: null,
    decided_by: null,
    history: [],
    weak: false,
    rule_reason: "Audit found the site is served over http.",
    ai_rationale: null,
    ...overrides,
  };
}

export function reviewDetail(overrides: Partial<ReviewDetail> = {}): ReviewDetail {
  return {
    business: {
      id: "biz-1",
      display_name: "Barton Creek Plumbing",
      normalized_name: "barton creek plumbing",
      industry: "plumbing",
      city: "Austin",
      state: "TX",
      postal_code: "78701",
      country: "US",
      phone_e164: "+15125550100",
      website: "https://bartoncreekplumbing.invalid/",
      domain: "bartoncreekplumbing.invalid",
      website_kind: "own_site",
      business_status: "operational",
      created_at: "2026-09-20T10:00:00Z",
      updated_at: "2026-09-20T10:00:00Z",
      latest_audit: null,
      legal_name: null,
      name_key: null,
      address_line1: "100 Congress Ave",
      address_line2: null,
      street_key: null,
      lat: null,
      lng: null,
      geohash7: null,
      places_content_expires_at: null,
      field_values: [],
      records: [],
    },
    audit: null,
    ai: null,
    opportunities: [reviewOpportunity()],
    suppressed: false,
    suppressions: [],
    undo_window_minutes: 30,
    weak_confidence: 0.4,
    ...overrides,
  };
}

export function leadRead(overrides: Partial<LeadRead> = {}): LeadRead {
  return {
    opportunity_id: "opp-1",
    business_id: "biz-1",
    business_name: "Barton Creek Plumbing",
    city: "Austin",
    state: "TX",
    industry: "plumbing",
    service: "website_design",
    service_name: "Website design / redesign",
    score: 0.72,
    reason: "Audit found the site is served over http.",
    approved_by: "user-reviewer",
    approved_by_email: "reviewer@example.com",
    approved_at: "2026-09-20T10:00:00Z",
    assigned_to: "user-sales_rep",
    assigned_to_email: "rep1@example.com",
    phone_e164: "+15125550100",
    website: "https://bartoncreekplumbing.invalid/",
    lock_version: 1,
    top_evidence: null,
    rule_reason: "Audit found the site is served over http.",
    ai_rationale: "The model also read a 2016 copyright line.",
    crm: {
      id: "crm-1",
      status: "synced",
      external_url: "https://airtable.com/app1/tbl1/rec1",
      last_synced_at: "2026-09-20T12:31:00Z",
      due_at: null,
      last_error: null,
    },
    ...overrides,
  };
}

export function leadDetail(overrides: Partial<LeadDetail> = {}): LeadDetail {
  const base = reviewDetail();
  return {
    lead: leadRead(),
    crm_history: [
      {
        id: 1,
        crm_lead_id: "crm-1",
        action: "create",
        status: "ok",
        http_status: null,
        error: null,
        duration_ms: 12,
        created_at: "2026-09-20T12:31:00Z",
      },
    ],
    business: base.business,
    audit: null,
    opportunity: reviewOpportunity({ review_status: "approved", lock_version: 1 }),
    ...overrides,
  };
}
