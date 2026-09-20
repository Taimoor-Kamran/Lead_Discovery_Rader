/**
 * Human wording for the codes the API speaks. The raw code stays available (tooltips) so a
 * reviewer can still match what they see to the API and the audit rules; nothing here
 * changes a value, only how it is read.
 */

export const SERVICE_LABELS: Record<string, string> = {
  website_design: "Website redesign",
  seo_gbp: "SEO / Google profile",
  booking_setup: "Online booking",
  ads_social: "Ads & social",
};

export const FINDING_LABELS: Record<string, string> = {
  no_website: "No website on the listing",
  social_profile_only: "Social profile instead of a website",
  unreachable: "Homepage could not be loaded",
  no_https: "No HTTPS",
  tls_invalid: "Invalid HTTPS certificate",
  no_mobile_viewport: "Not mobile-friendly (no viewport tag)",
  missing_title: "Missing page title",
  missing_meta_description: "Missing meta description",
  no_h1: "No main heading",
  no_structured_data: "No LocalBusiness structured data",
  no_online_booking: "No online booking",
  no_contact_on_homepage: "No contact info on homepage",
  stale_copyright: "Outdated copyright year",
  slow_mobile: "Slow on mobile",
  js_shell_suspected: "Page built with JavaScript (audit may be incomplete)",
  robots_blocked: "Blocked by robots.txt",
};

export const SOURCE_LABELS: Record<string, string> = {
  rules: "Rules",
  ai: "AI",
  "rules+ai": "Rules + AI",
};

export const AUDIT_STATUS_LABELS: Record<string, string> = {
  done: "Audited",
  skipped: "Skipped",
  robots_blocked: "Blocked by robots.txt",
  unreachable: "Unreachable",
  failed: "Failed",
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

export function auditStatusLabel(code: string | null | undefined): string {
  return (code && AUDIT_STATUS_LABELS[code]) || humanize(code);
}

export function severityLabel(code: string | null | undefined): string {
  return (code && SEVERITY_LABELS[code]) || humanize(code);
}
