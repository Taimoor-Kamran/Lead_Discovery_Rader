/** Small display helpers. Unknown stays visibly unknown; nothing is guessed. */

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "unknown";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "unknown";
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function percent(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${Math.round(value * 100)}%`;
}

export function score(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toFixed(2);
}

export function place(city: string | null | undefined, state: string | null | undefined): string {
  const parts = [city, state].filter((part): part is string => Boolean(part && part.trim()));
  return parts.length ? parts.join(", ") : "location unknown";
}

export function orUnknown(value: string | null | undefined): string {
  return value && value.trim() ? value : "unknown";
}

export const REASON_LABELS: Record<string, string> = {
  evidence_wrong: "Evidence is wrong",
  business_closed: "Business is closed",
  wrong_industry: "Wrong industry",
  ai_mistake: "AI mistake",
  too_small: "Too small",
  too_large: "Too large",
  outside_area: "Outside our area",
  already_client: "Already a client",
  other: "Other",
};

export const STATUS_LABELS: Record<string, string> = {
  pending: "Pending",
  approved: "Approved",
  rejected: "Rejected",
  needs_enrichment: "Needs enrichment",
  duplicate: "Duplicate",
  not_a_fit: "Not a fit",
  do_not_contact: "Do not contact",
};

export const DECISION_LABELS: Record<string, string> = {
  approve: "Approved",
  reject: "Rejected",
  needs_enrichment: "Needs enrichment",
  duplicate: "Duplicate",
  not_a_fit: "Not a fit",
  do_not_contact: "Do not contact",
};
