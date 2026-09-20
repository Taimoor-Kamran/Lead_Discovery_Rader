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

/** A 0–1 score as the 0–100 integer people read. The API keeps the 0–1 value. */
export function score(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return String(Math.round(Math.max(0, Math.min(1, value)) * 100));
}

export function place(city: string | null | undefined, state: string | null | undefined): string {
  const parts = [city, state].filter((part): part is string => Boolean(part && part.trim()));
  return parts.length ? parts.join(", ") : "location unknown";
}

export function orUnknown(value: string | null | undefined): string {
  return value && value.trim() ? value : "unknown";
}

/**
 * A stored E.164 number the way people dial it. Only the North American shape is
 * reformatted — `+15125550102` → `(512) 555-0102`; anything else is shown exactly as
 * stored, because guessing a grouping would be inventing.
 */
export function formatPhone(value: string | null | undefined): string {
  if (!value) return "no public phone";
  const match = /^\+1(\d{3})(\d{3})(\d{4})$/.exec(value.trim());
  if (!match) return value;
  return `(${match[1]}) ${match[2]}-${match[3]}`;
}

export type PsiRating = "good" | "needs work" | "poor";

export type PsiLine = { key: string; label: string; value: string; rating: PsiRating | null };

/** Google's own thresholds: Lighthouse score bands and the Core Web Vitals cut-offs. */
function band(value: number, good: number, needsWork: number, higherIsBetter = false): PsiRating {
  if (higherIsBetter) {
    if (value >= good) return "good";
    return value >= needsWork ? "needs work" : "poor";
  }
  if (value <= good) return "good";
  return value <= needsWork ? "needs work" : "poor";
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** The PageSpeed numbers as labelled lines. A metric PSI did not report reads "unknown". */
export function psiLines(psi: Record<string, unknown> | null | undefined): PsiLine[] {
  if (!psi || !Object.keys(psi).length) return [];
  const scoreValue = num(psi.performance_score);
  const lcp = num(psi.lcp_ms);
  const cls = num(psi.cls);
  const tbt = num(psi.tbt_ms);
  const lines: PsiLine[] = [
    {
      key: "performance_score",
      label: "Mobile score",
      value: scoreValue === null ? "unknown" : `${Math.round(scoreValue)}/100`,
      rating: scoreValue === null ? null : band(scoreValue, 90, 50, true),
    },
    {
      key: "lcp_ms",
      label: "Load time (LCP)",
      value: lcp === null ? "unknown" : `${(lcp / 1000).toFixed(1)} s`,
      rating: lcp === null ? null : band(lcp, 2500, 4000),
    },
    {
      key: "cls",
      label: "Layout shift (CLS)",
      value: cls === null ? "unknown" : cls.toFixed(2),
      rating: cls === null ? null : band(cls, 0.1, 0.25),
    },
    {
      key: "tbt_ms",
      label: "Blocking time (TBT)",
      value: tbt === null ? "unknown" : `${Math.round(tbt)} ms`,
      rating: tbt === null ? null : band(tbt, 200, 600),
    },
  ];
  const crux = psi.crux_category;
  if (typeof crux === "string" && crux) {
    lines.push({ key: "crux_category", label: "Field data", value: crux, rating: null });
  }
  return lines;
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
