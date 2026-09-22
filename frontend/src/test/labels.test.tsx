/**
 * "Codes are read, not shown", asserted as the rule rather than as a list of known-bad
 * spellings.
 *
 * v0.9.0's guard grepped the sources for `·` and so missed `join("; ")` — the same defect
 * in wording the grep did not know. Both guards here are therefore stated against the
 * *rendered page* and derived from *the payload the API sent*, so a new code or a new
 * separator fails them without anyone remembering to add it:
 *
 * 1. **No raw code reaches the screen.** Every snake_case string anywhere in the response
 *    is collected, and none of them may appear in the page's visible prose. A code belongs
 *    in a `title` (the tooltip a reviewer matches against the API), which is an attribute
 *    and therefore not prose.
 * 2. **A field with several values renders as several elements.** No single text node may
 *    contain two values of the same multi-valued field. That is what `join("; ")`,
 *    `join(" · ")`, `join(", ")` and anything else a future hand reaches for all do, and it
 *    is why the rule is about text nodes rather than about separators.
 *
 * The evidence face is exempt from rule 1. A quoted line from someone's homepage, a URL and
 * a machine identifier a reviewer copies are set in `font-mono` precisely because they are
 * verbatim, and rewording them would be the bug.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { LeadDetail } from "@/components/leads/LeadDetail";
import { Leads } from "@/components/leads/Leads";
import { BusinessReview } from "@/components/review/BusinessReview";
import { ReviewQueue } from "@/components/review/ReviewQueue";
import { SOURCE_NAME_LABELS } from "@/lib/labels";
import {
  leadDetail,
  leadRead,
  me,
  queueItem,
  renderWithProviders,
  reviewDetail,
  reviewOpportunity,
  routeFetch,
  sourceRecord,
} from "@/test/utils";

afterEach(() => vi.unstubAllGlobals());

/** `no_https`, `rules+ai`, `none_detected`: a machine identifier, not a person's sentence. */
const RAW_CODE = /^[a-z][a-z0-9]*(?:[_+][a-z0-9]+)+$/;

/** Codes that are values in the payload but never rendered as prose anywhere. */
const NOT_RENDERED_AS_PROSE = new Set<string>([
  // Object *keys* in `checks` and `score_components` are labelled by the components that
  // read them; these two are values that happen to look like codes.
  "scoring-1",
]);

/** Every snake_case-looking string value anywhere in a response body. */
function codesIn(value: unknown, found = new Set<string>()): Set<string> {
  if (typeof value === "string") {
    if (RAW_CODE.test(value) && !NOT_RENDERED_AS_PROSE.has(value)) found.add(value);
  } else if (Array.isArray(value)) {
    for (const item of value) codesIn(item, found);
  } else if (value && typeof value === "object") {
    for (const item of Object.values(value)) codesIn(item, found);
  }
  return found;
}

/**
 * Every text node of the page that counts as prose: the evidence face is excluded, because
 * a quoted line, a URL and an identifier are deliberately verbatim.
 */
function prose(container: HTMLElement): { text: string; where: string }[] {
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
  const found: { text: string; where: string }[] = [];
  let node = walker.nextNode();
  while (node) {
    const text = node.textContent ?? "";
    const parent = node.parentElement;
    const verbatim = parent?.closest(".font-mono, [data-verbatim]") ?? null;
    if (text.trim() && !verbatim) {
      found.push({ text, where: parent?.outerHTML.slice(0, 160) ?? "" });
    }
    node = walker.nextNode();
  }
  return found;
}

function expectNoRawCode(container: HTMLElement, body: unknown) {
  const codes = [...codesIn(body)];
  // A floor, so an empty or miswired fixture cannot pass this by carrying nothing to find.
  expect(codes.length, "the fixture must actually carry codes, or this proves nothing").toBeGreaterThan(2);
  const offenders: string[] = [];
  for (const { text, where } of prose(container)) {
    for (const code of codes) {
      if (new RegExp(`(^|[^A-Za-z0-9_+])${escape(code)}([^A-Za-z0-9_+]|$)`).test(text)) {
        offenders.push(`"${code}" is rendered as prose in ${where}`);
      }
    }
  }
  expect(offenders).toEqual([]);
}

function escape(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * The anti-`join` rule: every value is on the page, and no one text node holds two of them.
 * A separator of any kind puts them in the same text node, which is what this catches.
 */
function expectRenderedApart(container: HTMLElement, values: string[], what: string) {
  expect(values.length, `${what} needs at least two values to prove anything`).toBeGreaterThan(1);
  const nodes = prose(container).map((entry) => entry.text);
  for (const value of values) {
    expect(nodes.some((text) => text.includes(value)), `${what}: "${value}" is not on the page`).toBe(true);
  }
  const joined = nodes.filter((text) => values.filter((value) => text.includes(value)).length > 1);
  expect(joined, `${what} is joined into one text node instead of rendered as separate elements`).toEqual([]);
}

// --- fixtures that carry every code these pages can show -----------------------------------

const AI = {
  ai_generated: true as const,
  classification_id: "cls-1",
  model: "gpt-4.1-mini",
  prompt_version: "classify-1",
  status: "guardrail_trimmed",
  escalated: false,
  business_summary: "A plumbing company in Austin.",
  industry: "plumbing",
  industry_matches_listing: true,
  buying_intent: "none_detected",
  unknowns: ["whether they take card payments", "how many vans they run"],
  created_at: "2026-09-20T10:00:00Z",
};

const AUDIT = {
  id: "audit-1",
  business_id: "biz-1",
  job_run_id: null,
  url_audited: "https://bartoncreekplumbing.invalid/",
  final_url: "https://bartoncreekplumbing.invalid/",
  status: "robots_blocked",
  http_status: 200,
  finding_codes: ["no_https", "no_h1"],
  rules_version: "audit-2",
  started_at: "2026-09-20T10:00:00Z",
  finished_at: "2026-09-20T10:00:00Z",
  created_at: "2026-09-20T10:00:00Z",
  checks: {},
  psi: null,
  tech_stack: { platforms: [] },
  findings: [
    { code: "no_https", severity: "high", message: "Audit found the site served over http.", evidence_text: "served over http", evidence_url: null },
    { code: "stale_copyright", severity: "low", message: "Audit found a 2016 copyright line.", evidence_text: null, evidence_url: null },
  ],
  page_text: null,
  page_text_hidden: false,
  html_sha256: null,
  content_expires_at: null,
  purged_at: null,
};

const HISTORY = [
  {
    id: "dec-1",
    opportunity_id: "opp-1",
    decision: "not_a_fit",
    from_status: "pending",
    to_status: "not_a_fit",
    reason_code: "outside_area",
    note: null,
    duplicate_of: null,
    assigned_to: null,
    assigned_to_email: null,
    decided_by: "user-reviewer",
    decided_by_email: "reviewer@example.com",
    decided_at: "2026-09-20T11:00:00Z",
    undone_at: null,
    undone_by: null,
    undo_until: "2026-09-20T11:30:00Z",
    can_undo: false,
  },
];

function loadedReviewDetail() {
  return reviewDetail({
    audit: AUDIT as never,
    ai: AI,
    linked_profiles: {
      page_url: "https://bartoncreekplumbing.invalid/",
      profiles: [
        { platform: "facebook", url: "https://www.facebook.invalid/barton" },
        { platform: "nextdoor", url: null },
      ],
    },
    sources: [sourceRecord(), sourceRecord({ code: "demo_fixture", source_record_id: "demo-1" })],
    opportunities: [
      reviewOpportunity({
        source: "rules+ai",
        review_status: "needs_enrichment",
        ai: AI as never,
        ai_classification_id: "cls-1",
        ai_rationale: "The model also read a 2016 copyright line.",
        history: HISTORY as never,
      }),
    ],
  });
}

// --- the rule, on every page that renders codes --------------------------------------------

describe("no raw code reaches the screen", () => {
  it("review detail", async () => {
    const body = loadedReviewDetail();
    routeFetch({
      "GET /review-queue/biz-1": { status: 200, body },
      "GET /review-queue": { status: 200, body: { items: [queueItem()], next_cursor: null } },
      "GET /users": { status: 200, body: { items: [], next_cursor: null } },
    });
    const { container } = renderWithProviders(<BusinessReview businessId="biz-1" />, {
      user: me("reviewer"),
    });
    await waitFor(() => expect(screen.getByTestId("ai-summary")).toBeTruthy());

    expectNoRawCode(container, body);
  });

  it("the review queue", async () => {
    const body = {
      items: [
        queueItem({
          sources: ["google_places", "demo_fixture"],
          latest_audit: {
            status: "robots_blocked",
            audited_at: "2026-09-20T10:00:00Z",
            top_findings: ["no_https", "stale_copyright"],
          },
          opportunities: [
            {
              id: "opp-1",
              service: "booking_setup",
              service_name: "Online booking",
              source: "rules+ai",
              confidence: 0.8,
              score: 0.72,
              review_status: "needs_enrichment",
              lock_version: 0,
              reason: "Audit found no way to book online.",
              weak: false,
            },
          ],
        }),
      ],
      next_cursor: null,
    };
    routeFetch({ "GET /review-queue": { status: 200, body } });
    const { container } = renderWithProviders(<ReviewQueue />, { user: me("reviewer") });
    await waitFor(() => expect(screen.getAllByTestId("queue-row")).toHaveLength(1));

    expectNoRawCode(container, body);
  });

  it("lead detail", async () => {
    const body = leadDetail({
      audit: AUDIT as never,
      sources: [sourceRecord(), sourceRecord({ code: "demo_fixture", source_record_id: "demo-1" })],
      linked_profiles: {
        page_url: "https://bartoncreekplumbing.invalid/",
        profiles: [{ platform: "linkedin", url: "https://www.linkedin.invalid/company/barton" }],
      },
      opportunity: reviewOpportunity({
        review_status: "approved",
        source: "rules+ai",
        ai: AI as never,
        history: HISTORY as never,
      }),
    });
    routeFetch({ "GET /leads/opp-1": { status: 200, body } });
    const { container } = renderWithProviders(<LeadDetail opportunityId="opp-1" />, {
      user: me("reviewer"),
    });
    await waitFor(() => expect(screen.getByTestId("lead-detail")).toBeTruthy());

    expectNoRawCode(container, body);
  });

  it("the leads list", async () => {
    const body = {
      items: [leadRead({ service: "ads_social", sources: ["google_places", "demo_fixture"] })],
      next_cursor: null,
    };
    routeFetch({ "GET /leads": { status: 200, body } });
    const { container } = renderWithProviders(<Leads />, { user: me("sales_rep") });
    await waitFor(() => expect(screen.getAllByTestId("lead-row")).toHaveLength(1));

    expectNoRawCode(container, body);
  });
});

describe("the guard itself fails on a raw code", () => {
  it("catches a code rendered as prose, so a green run means something", async () => {
    const { container } = renderWithProviders(
      <main>
        <p>Source: google_places</p>
      </main>,
    );

    expect(() =>
      expectNoRawCode(container, {
        a: "google_places",
        b: "no_https",
        c: "rules+ai",
        d: "not_a_fit",
        e: "none_detected",
        f: "own_site",
      }),
    ).toThrow();
  });

  it("does not object to a code inside a title, which is where one belongs", () => {
    const { container } = renderWithProviders(
      <main>
        <p title="google_places">Google Places</p>
      </main>,
    );

    expectNoRawCode(container, {
      a: "google_places",
      b: "no_https",
      c: "rules+ai",
      d: "not_a_fit",
      e: "none_detected",
      f: "own_site",
    });
  });

  it("does not object to a verbatim quotation in the evidence face", () => {
    const { container } = renderWithProviders(
      <main>
        <span className="font-mono">og:site_name missing</span>
      </main>,
    );

    expectNoRawCode(container, {
      a: "site_name",
      b: "no_https",
      c: "rules+ai",
      d: "not_a_fit",
      e: "none_detected",
      f: "own_site",
    });
  });
});

// --- the anti-join rule --------------------------------------------------------------------

describe("a field with several values is rendered as several elements", () => {
  it("lists the AI summary's unknowns rather than joining them", async () => {
    const body = loadedReviewDetail();
    routeFetch({
      "GET /review-queue/biz-1": { status: 200, body },
      "GET /review-queue": { status: 200, body: { items: [], next_cursor: null } },
      "GET /users": { status: 200, body: { items: [], next_cursor: null } },
    });
    const { container } = renderWithProviders(<BusinessReview businessId="biz-1" />, {
      user: me("reviewer"),
    });
    await waitFor(() => expect(screen.getByTestId("ai-summary")).toBeTruthy());

    expectRenderedApart(container, AI.unknowns, "the AI summary's unknowns");
    expect(screen.getAllByTestId("ai-unknown")).toHaveLength(AI.unknowns.length);
  });

  it("lists the sources of a business rather than joining them", async () => {
    const body = loadedReviewDetail();
    routeFetch({
      "GET /review-queue/biz-1": { status: 200, body },
      "GET /review-queue": { status: 200, body: { items: [], next_cursor: null } },
      "GET /users": { status: 200, body: { items: [], next_cursor: null } },
    });
    const { container } = renderWithProviders(<BusinessReview businessId="biz-1" />, {
      user: me("reviewer"),
    });
    await waitFor(() => expect(screen.getByTestId("source-provenance")).toBeTruthy());

    expectRenderedApart(
      container,
      [SOURCE_NAME_LABELS.google_places, SOURCE_NAME_LABELS.demo_fixture],
      "the source block",
    );
  });

  it("lists a business's sources in the queue's source column rather than joining them", async () => {
    routeFetch({
      "GET /review-queue": {
        status: 200,
        body: {
          items: [queueItem({ sources: ["google_places", "demo_fixture"] })],
          next_cursor: null,
        },
      },
    });
    const { container } = renderWithProviders(<ReviewQueue />, { user: me("reviewer") });
    await waitFor(() => expect(screen.getAllByTestId("queue-row")).toHaveLength(1));

    expectRenderedApart(
      container,
      [SOURCE_NAME_LABELS.google_places, SOURCE_NAME_LABELS.demo_fixture],
      "the queue's source column",
    );
  });
});

describe("the anti-join guard itself", () => {
  it("fails on a semicolon-joined run-on", () => {
    const { container } = renderWithProviders(
      <main>
        <p>{["first unknown", "second unknown"].join("; ")}</p>
      </main>,
    );

    expect(() => expectRenderedApart(container, ["first unknown", "second unknown"], "x")).toThrow();
  });

  it("fails on a middle-dot-joined run-on, which is the same defect", () => {
    const { container } = renderWithProviders(
      <main>
        <p>{["first unknown", "second unknown"].join(" · ")}</p>
      </main>,
    );

    expect(() => expectRenderedApart(container, ["first unknown", "second unknown"], "x")).toThrow();
  });

  it("passes on separate elements", () => {
    const { container } = renderWithProviders(
      <main>
        <ul>
          <li>first unknown</li>
          <li>second unknown</li>
        </ul>
      </main>,
    );

    expectRenderedApart(container, ["first unknown", "second unknown"], "x");
  });
});
