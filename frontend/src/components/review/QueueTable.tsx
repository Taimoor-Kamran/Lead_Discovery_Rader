"use client";

import Link from "next/link";
import type { QueueItem } from "@/lib/api";
import { formatDateTime, percent, place, score } from "@/lib/format";
import { auditStatusLabel, findingLabel, serviceLabel } from "@/lib/labels";
import { SafeLink } from "@/components/SafeLink";

type Props = {
  items: QueueItem[];
  selected: Set<string>;
  onToggle: (opportunityId: string) => void;
  onToggleBusiness: (item: QueueItem) => void;
  canSelect: boolean;
  /** With "Show weak signals" on, the checkboxes are always visible; otherwise on hover. */
  showWeak?: boolean;
};

const SOURCE_STYLE: Record<string, string> = {
  rules: "border-slate-300 bg-slate-100 text-slate-800",
  ai: "border-amber-300 bg-amber-50 text-amber-900",
  "rules+ai": "border-teal-300 bg-teal-50 text-teal-700",
};

const AUDIT_STYLE: Record<string, string> = {
  done: "text-teal-700",
  skipped: "text-slate-500",
  robots_blocked: "text-amber-700",
  unreachable: "text-red-700",
  failed: "text-red-700",
};

/** Hidden until the row is hovered or the box is focused or ticked; never removed from the page. */
const HOVER_ONLY = "opacity-0 group-hover:opacity-100 focus-visible:opacity-100 checked:opacity-100";

/** The queue: one row per business, city and state next to the name, service chips. */
export function QueueTable({ items, selected, onToggle, onToggleBusiness, canSelect, showWeak = false }: Props) {
  const checkboxClass = showWeak ? "" : HOVER_ONLY;
  if (!items.length) {
    return (
      <p className="rounded border border-dashed border-slate-300 bg-white p-8 text-center text-sm text-slate-600">
        Nothing to review with these filters.
      </p>
    );
  }
  return (
    <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
      <table className="w-full min-w-[960px] text-sm">
        <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-600">
          <tr>
            {canSelect ? <th scope="col" className="w-8 px-3 py-2" /> : null}
            <th scope="col" className="px-3 py-2">Business</th>
            <th scope="col" className="px-3 py-2">Industry</th>
            <th scope="col" className="px-3 py-2">Opportunities</th>
            <th scope="col" className="px-3 py-2">Audit</th>
            <th scope="col" className="px-3 py-2">Top findings</th>
            <th scope="col" className="w-20 px-3 py-2 text-right">Score</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {items.map((item) => {
            const allSelected =
              item.opportunities.length > 0 &&
              item.opportunities.every((o) => selected.has(o.id));
            return (
              <tr key={item.business_id} className="group align-top hover:bg-slate-50" data-testid="queue-row">
                {canSelect ? (
                  <td className="px-3 py-2">
                    <input
                      type="checkbox"
                      aria-label={`Select every opportunity of ${item.display_name}`}
                      checked={allSelected}
                      onChange={() => onToggleBusiness(item)}
                      className={checkboxClass}
                    />
                  </td>
                ) : null}
                <td className="px-3 py-2">
                  <Link
                    href={`/review/${item.business_id}`}
                    className="font-medium text-navy underline-offset-2 hover:underline"
                  >
                    {item.display_name}
                  </Link>
                  <div className="text-xs text-slate-600" data-testid="queue-place">
                    {place(item.city, item.state)}
                  </div>
                  {item.website ? (
                    <div className="text-xs">
                      <SafeLink href={item.website} />
                    </div>
                  ) : null}
                </td>
                <td className="px-3 py-2 text-slate-700">{item.industry ?? "unknown"}</td>
                <td className="px-3 py-2">
                  <ul className="flex flex-wrap gap-1">
                    {item.opportunities.map((opportunity) => (
                      <li key={opportunity.id}>
                        <label
                          className={`chip cursor-pointer ${SOURCE_STYLE[opportunity.source] ?? ""}`}
                          title={`${opportunity.service} · confidence ${percent(opportunity.confidence)} · source ${opportunity.source}`}
                          data-testid="service-chip"
                        >
                          {canSelect ? (
                            <input
                              type="checkbox"
                              aria-label={`Select ${serviceLabel(opportunity.service)} for ${item.display_name}`}
                              checked={selected.has(opportunity.id)}
                              onChange={() => onToggle(opportunity.id)}
                              className={`mr-1 ${checkboxClass}`}
                            />
                          ) : null}
                          <span className="font-medium">{serviceLabel(opportunity.service)}</span>
                          <span className="text-slate-600">Score {score(opportunity.score)}</span>
                          {opportunity.weak ? <span className="text-amber-700">weak</span> : null}
                        </label>
                      </li>
                    ))}
                  </ul>
                  {item.weak_hidden > 0 ? (
                    <p className="mt-1 text-xs text-slate-500" data-testid="weak-hidden">
                      {item.weak_hidden} weak signal{item.weak_hidden === 1 ? "" : "s"} hidden
                    </p>
                  ) : null}
                </td>
                <td className="px-3 py-2">
                  {item.latest_audit ? (
                    <div>
                      <span className={AUDIT_STYLE[item.latest_audit.status] ?? ""} title={item.latest_audit.status}>
                        {auditStatusLabel(item.latest_audit.status)}
                      </span>
                      <div className="text-xs text-slate-500">
                        {formatDateTime(item.latest_audit.audited_at)}
                      </div>
                    </div>
                  ) : (
                    <span className="text-slate-500">not audited</span>
                  )}
                </td>
                <td className="px-3 py-2">
                  <ul className="flex flex-wrap gap-1">
                    {item.latest_audit?.top_findings.map((code) => (
                      <li key={code} className="chip border-slate-200 bg-slate-50 text-slate-700" title={code}>
                        {findingLabel(code)}
                      </li>
                    ))}
                  </ul>
                </td>
                <td className="px-3 py-2 text-right font-mono" title={`raw ${item.top_score}`}>
                  {score(item.top_score)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
