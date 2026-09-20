"use client";

import { useState } from "react";
import type { ReviewDetail } from "@/lib/api";
import { formatDateTime, formatPhone, orUnknown, place } from "@/lib/format";
import { SafeLink } from "@/components/SafeLink";

/** Left column: the business as survivorship shows it, with the provenance behind it. */
export function BusinessFacts({ detail }: { detail: ReviewDetail }) {
  const business = detail.business;
  const [showProvenance, setShowProvenance] = useState(false);
  const shown = business.field_values.filter((value) => value.is_displayed);
  const rows: [string, React.ReactNode][] = [
    ["Location", place(business.city, business.state)],
    ["Address", orUnknown(business.address_line1)],
    ["Postal code", orUnknown(business.postal_code)],
    ["Industry", orUnknown(business.industry)],
    ["Public phone", <span key="phone" title={business.phone_e164 ?? undefined}>{formatPhone(business.phone_e164)}</span>],
    [
      "Website",
      business.website ? <SafeLink href={business.website}>{business.website}</SafeLink> : "none",
    ],
    ["Website kind", business.website_kind],
    ["Status", business.business_status],
  ];

  return (
    <section className="flex flex-col gap-3 rounded-lg border border-slate-200 bg-white p-4">
      <header>
        <h2 className="text-lg font-semibold text-navy">{business.display_name}</h2>
        <p className="text-sm text-slate-600">{place(business.city, business.state)}</p>
        {detail.suppressed ? (
          <p className="mt-2 rounded border border-red-200 bg-red-50 p-2 text-sm text-red-900" role="status">
            Do not contact: this business is suppressed
            {detail.suppressions[0]?.reason ? ` (${detail.suppressions[0].reason})` : ""}.
          </p>
        ) : null}
      </header>
      <dl className="grid grid-cols-[7rem_1fr] gap-y-1 text-sm">
        {rows.map(([label, value]) => (
          <div key={label} className="contents">
            <dt className="text-slate-500">{label}</dt>
            <dd className="break-words">{value}</dd>
          </div>
        ))}
      </dl>
      <button
        type="button"
        className="btn-secondary self-start"
        aria-expanded={showProvenance}
        onClick={() => setShowProvenance((current) => !current)}
      >
        {showProvenance ? "Hide" : "Show"} field provenance ({shown.length})
      </button>
      {showProvenance ? (
        <table className="w-full text-xs">
          <thead className="text-left text-slate-500">
            <tr>
              <th scope="col" className="py-1">Field</th>
              <th scope="col" className="py-1">Value</th>
              <th scope="col" className="py-1">Source</th>
              <th scope="col" className="py-1">Observed</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {shown.map((value) => (
              <tr key={`${value.field}-${value.discovered_record_id}`}>
                <td className="py-1 pr-2 font-mono">{value.field}</td>
                <td className="py-1 pr-2 break-all">{value.value ?? "null"}</td>
                <td className="py-1 pr-2">{value.source}</td>
                <td className="py-1">{formatDateTime(value.observed_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {business.records.length ? (
        <p className="text-xs text-slate-500">
          {business.records.length} source record{business.records.length === 1 ? "" : "s"}:{" "}
          {business.records.map((record) => record.source).join(", ")}
        </p>
      ) : null}
    </section>
  );
}
