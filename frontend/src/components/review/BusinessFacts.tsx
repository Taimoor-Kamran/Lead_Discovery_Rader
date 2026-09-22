"use client";

import { SafeLink } from "@/components/SafeLink";
import { Card, Disclosure, Table, TBody, Td, Th, THead, Tr } from "@/components/ui";
import type { ReviewDetail } from "@/lib/api";
import { formatDateTime, formatPhone, orUnknown, place } from "@/lib/format";
import { businessStatusLabel, industryLabel, websiteKindLabel } from "@/lib/labels";

/**
 * A code the API speaks, read in plain words, with the code itself one hover away so a
 * reviewer can still match what they see to the API. Nothing is guessed: a missing code
 * reads "unknown" and carries no tooltip.
 */
function Coded({
  code,
  label,
}: {
  code: string | null | undefined;
  label: (code: string | null | undefined) => string;
}) {
  return <span title={code ?? undefined}>{label(code)}</span>;
}

/**
 * Who the business is, as survivorship settled it, with the provenance one click away.
 * The two things a rep acts on — the phone and the website — lead the list.
 */
export function BusinessFacts({ detail }: { detail: ReviewDetail }) {
  const business = detail.business;
  const shown = business.field_values.filter((value) => value.is_displayed);
  const rows: [string, React.ReactNode][] = [
    [
      "Public phone",
      <span key="phone" className="font-mono" title={business.phone_e164 ?? undefined}>
        {formatPhone(business.phone_e164)}
      </span>,
    ],
    ["Website", business.website ? <SafeLink href={business.website}>{business.website}</SafeLink> : "none"],
    ["Industry", <Coded key="industry" code={business.industry} label={industryLabel} />],
    ["Location", place(business.city, business.state)],
    ["Address", orUnknown(business.address_line1)],
    ["Postal code", orUnknown(business.postal_code)],
    [
      "Website kind",
      <Coded key="website_kind" code={business.website_kind} label={websiteKindLabel} />,
    ],
    [
      "Status",
      <Coded key="business_status" code={business.business_status} label={businessStatusLabel} />,
    ],
  ];

  return (
    <Card className="print-break-avoid print-plain">
      {detail.suppressed ? (
        <p
          className="mb-3 rounded border border-risk bg-risk-tint p-2 text-base text-ink"
          role="status"
        >
          Do not contact: this business is suppressed
          {detail.suppressions[0]?.reason ? ` (${detail.suppressions[0].reason})` : ""}.
        </p>
      ) : null}
      <dl className="grid grid-cols-[8rem_1fr] gap-x-4 gap-y-1.5 text-base" data-testid="business-facts">
        {rows.map(([label, value]) => (
          <div key={label} className="contents">
            <dt className="text-ink-soft">{label}</dt>
            <dd className="break-words">{value}</dd>
          </div>
        ))}
      </dl>
      <div className="print-hide mt-4">
        <Disclosure summary={(open) => `${open ? "Hide" : "Show"} field provenance (${shown.length})`}>
          <Table minWidth="30rem" className="text-sm">
            <THead>
              <tr>
                <Th>Field</Th>
                <Th>Value</Th>
                <Th>Source</Th>
                <Th>Observed</Th>
              </tr>
            </THead>
            <TBody>
              {shown.map((value) => (
                <Tr key={`${value.field}-${value.discovered_record_id}`}>
                  <Td className="font-mono">{value.field}</Td>
                  <Td className="break-all font-mono">{value.value ?? "null"}</Td>
                  <Td>{value.source}</Td>
                  <Td className="text-ink-soft">{formatDateTime(value.observed_at)}</Td>
                </Tr>
              ))}
            </TBody>
          </Table>
          {business.records.length ? (
            <p className="mt-2 text-sm text-ink-soft">
              {business.records.length} source record{business.records.length === 1 ? "" : "s"}:{" "}
              {business.records.map((record) => record.source).join(", ")}
            </p>
          ) : null}
        </Disclosure>
      </div>
    </Card>
  );
}
