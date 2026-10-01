/**
 * Human wording for the codes the API speaks. The raw code stays available (tooltips) so a
 * reviewer can still match what they see to the API and the audit rules; nothing here
 * changes a value, only how it is read.
 */

export const SERVICE_LABELS: Record<string, string> = {
  website_design: "Website redesign",
  seo_gbp: "SEO / Google profile",
  booking_setup: "Online booking",
  ai_chat_setup: "Chat assistant",
  ads_social: "Ads & social",
};

export const FINDING_LABELS: Record<string, string> = {
  no_website: "No website on the listing",
  social_profile_only: "Social profile instead of a website",
  unreachable: "Homepage could not be loaded",
  no_https: "No HTTPS",
  tls_invalid: "Invalid HTTPS certificate",
  builder_subdomain: "Site on a website-builder subdomain",
  no_mobile_viewport: "Not mobile-friendly (no viewport tag)",
  missing_title: "Missing page title",
  missing_meta_description: "Missing meta description",
  no_h1: "No main heading",
  no_structured_data: "No LocalBusiness structured data",
  no_online_booking: "No online booking",
  no_live_chat: "No live chat",
  no_contact_on_homepage: "No contact info on homepage",
  images_without_alt: "Images without alt text",
  unlabelled_form_fields: "Form fields without labels",
  thin_content: "Little text on the homepage",
  no_section_headings: "No section headings",
  heading_level_skipped: "Heading levels skipped",
  stale_copyright: "Outdated copyright year",
  slow_mobile: "Slow on mobile",
  low_accessibility_score: "Low accessibility score",
  low_best_practices_score: "Low best-practices score",
  js_shell_suspected: "Page built with JavaScript (audit may be incomplete)",
  few_reviews: "Few Google reviews",
  robots_blocked: "Blocked by robots.txt",
  site_builder: "Built with a website builder",
  viewport_blocks_zoom: "Blocks zooming on phones",
  default_title: "Page title doesn't name the business",
  short_title: "Very short page title",
  long_title: "Page title too long for search results",
  multiple_h1: "More than one main heading",
  no_local_business_schema: "Structured data, but not LocalBusiness",
  invalid_structured_data: "Broken structured data",
  nap_phone_mismatch: "Site phone differs from listing",
  // Retired in v0.12.0 (no longer emitted); kept so audits stored before then still read.
  nap_address_mismatch: "Site address differs from listing",
  listing_website_http: "Listing links to the http:// site",
  listing_website_host_mismatch: "Listing website differs (www)",
  no_click_to_call: "No click-to-call link",
  placeholder_email: "Placeholder email address",
  placeholder_text: "Template placeholder text",
  future_copyright: "Copyright year in the future",
  // v0.13.0: from DNS and the domain registry, not from the page.
  domain_expired: "Domain registration expired",
  domain_no_a_record: "Domain has no address record",
  domain_expiring_soon: "Domain registration expiring soon",
  multiple_spf_records: "More than one SPF record",
  spf_allows_all: "SPF record allows any sender",
  no_spf: "No SPF record",
  no_dmarc: "No DMARC record",
  dmarc_policy_none: "DMARC policy set to none",
  no_domain_mx: "No mail server (MX) record",
};

/** How an opportunity was arrived at, not where a fact came from. See SOURCE_NAME_LABELS. */
export const SOURCE_LABELS: Record<string, string> = {
  rules: "Rules",
  ai: "AI",
  "rules+ai": "Rules + AI",
};

/**
 * Every row of the `sources` table, said the way a salesperson would say it out loud when a
 * prospect asks where their details came from. The keys are `sources.name` — the same string
 * the adapter registry registers — and a backend guard test
 * (`tests/unit/test_label_coverage.py`) fails when a registered source has no entry here, so
 * adding a source later cannot quietly ship `google_places` to the screen.
 */
export const SOURCE_NAME_LABELS: Record<string, string> = {
  google_places: "Google Places",
  pagespeed_insights: "PageSpeed Insights",
  rdap: "RDAP (domain registry)",
  openai: "OpenAI",
  airtable: "Airtable",
  demo_fixture: "Demo fixture",
};

/**
 * The platforms the website audit records a homepage linking to. These are links the
 * business published on its own site — never a profile this system read.
 */
export const SOCIAL_PLATFORM_LABELS: Record<string, string> = {
  facebook: "Facebook",
  instagram: "Instagram",
  x: "X",
  linkedin: "LinkedIn",
  youtube: "YouTube",
  tiktok: "TikTok",
  yelp: "Yelp",
  nextdoor: "Nextdoor",
  google: "Google",
};

/** What the model said about intent. `none_detected` is the honest default, not a blank. */
export const BUYING_INTENT_LABELS: Record<string, string> = {
  none_detected: "None detected",
  explicit: "Stated on their website",
};

/** How a classification call ended. Shown inside the AI details disclosure. */
export const AI_STATUS_LABELS: Record<string, string> = {
  ok: "Answered",
  reused: "Reused an earlier answer",
  guardrail_trimmed: "Answered, parts dropped by the guardrails",
  schema_invalid: "Answer did not match the schema",
  error: "Call failed",
  skipped_budget: "Skipped: daily budget reached",
  skipped_disabled: "Skipped: AI is off",
};

export const AUDIT_STATUS_LABELS: Record<string, string> = {
  done: "Audited",
  skipped: "Skipped",
  robots_blocked: "Blocked by robots.txt",
  bot_challenge: "Blocked by bot protection",
  not_readable: "Homepage not readable",
  unreachable: "Unreachable",
  failed: "Failed",
};

export const WEBSITE_KIND_LABELS: Record<string, string> = {
  own_site: "Own website",
  builder_subdomain: "Builder site",
  social_profile: "Social profile only",
  none: "No website",
};

export const BUSINESS_STATUS_LABELS: Record<string, string> = {
  operational: "Open",
  closed_temporarily: "Temporarily closed",
  closed_permanently: "Permanently closed",
  unknown: "Unknown",
};

/**
 * Industry slugs come from `normalization/taxonomy.py`, which is data and grows without a
 * code change, and the API's own dropdown label is `slug → Slug`. So only the slugs
 * `humanize` would read wrongly are listed here; everything else falls through to
 * `pest_control` → `Pest control`, which is the same wording the dropdown shows.
 */
export const INDUSTRY_LABELS: Record<string, string> = {
  hvac: "HVAC",
};

/** How a finding was established (v0.12.0 `Method`), in the reviewer's words (v0.14.0). */
export const METHOD_LABELS: Record<string, { label: string; title: string }> = {
  deterministic: { label: "Our check", title: "Our own code read the homepage or the listing" },
  api: { label: "Outside service", title: "An outside service measured or reported this (PageSpeed, the listing, DNS)" },
  ai: { label: "AI reading", title: "A model's reading of the page" },
};

export const SEVERITY_LABELS: Record<string, string> = {
  high: "High",
  medium: "Medium",
  low: "Low",
  info: "Info",
};

/** `some_code` → `Some code`, for a code the tables above do not know yet. */
export function humanize(code: string | null | undefined): string {
  if (!code) return "unknown";
  const words = code.replace(/[_-]+/g, " ").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : "unknown";
}

export function serviceLabel(code: string | null | undefined): string {
  return (code && SERVICE_LABELS[code]) || humanize(code);
}

export function findingLabel(code: string | null | undefined): string {
  return (code && FINDING_LABELS[code]) || humanize(code);
}

export function sourceLabel(code: string | null | undefined): string {
  return (code && SOURCE_LABELS[code]) || humanize(code);
}

export function sourceNameLabel(code: string | null | undefined): string {
  return (code && SOURCE_NAME_LABELS[code]) || humanize(code);
}

export function socialPlatformLabel(code: string | null | undefined): string {
  return (code && SOCIAL_PLATFORM_LABELS[code]) || humanize(code);
}

export function buyingIntentLabel(code: string | null | undefined): string {
  return (code && BUYING_INTENT_LABELS[code]) || humanize(code);
}

export function aiStatusLabel(code: string | null | undefined): string {
  return (code && AI_STATUS_LABELS[code]) || humanize(code);
}

export function auditStatusLabel(code: string | null | undefined): string {
  return (code && AUDIT_STATUS_LABELS[code]) || humanize(code);
}

export function severityLabel(code: string | null | undefined): string {
  return (code && SEVERITY_LABELS[code]) || humanize(code);
}

export function websiteKindLabel(code: string | null | undefined): string {
  return (code && WEBSITE_KIND_LABELS[code]) || humanize(code);
}

export function businessStatusLabel(code: string | null | undefined): string {
  return (code && BUSINESS_STATUS_LABELS[code]) || humanize(code);
}

export function industryLabel(code: string | null | undefined): string {
  return (code && INDUSTRY_LABELS[code]) || humanize(code);
}
