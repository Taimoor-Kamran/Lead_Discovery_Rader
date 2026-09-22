"use client";

import Link from "next/link";
import { SafeLink } from "@/components/SafeLink";
import {
  Badge,
  Chip,
  cx,
  SkeletonTableRows,
  Table,
  TableWrap,
  TBody,
  Td,
  Th,
  THead,
  Tr,
} from "@/components/ui";
import { SourceCell } from "@/components/review/SourceProvenance";
import type { QueueItem } from "@/lib/api";
import { formatDateTime, percent, place, score } from "@/lib/format";
import { auditStatusLabel, findingLabel, industryLabel, serviceLabel } from "@/lib/labels";

type Props = {
  items: QueueItem[];
  selected: Set<string>;
  onToggle: (opportunityId: string) => void;
  onToggleBusiness: (item: QueueItem) => void;
  canSelect: boolean;
  /** With "Show weak signals" on, the checkboxes are always visible; otherwise on hover. */
  showWeak?: boolean;
  /** First load: skeleton rows keep the table its full height so nothing jumps. */
  loading?: boolean;
  /** What to say when there is nothing — always with the reviewer's next action. */
  empty?: React.ReactNode;
};

/** An audit outcome is a status, so it gets a badge tone rather than a bare colour. */
const AUDIT_TONE: Record<string, "neutral" | "ok" | "warn" | "risk"> = {
  done: "ok",
  skipped: "neutral",
  robots_blocked: "warn",
  unreachable: "risk",
  failed: "risk",
};

/** Hidden until the row is hovered or the box is focused or ticked; never removed from the page. */
const HOVER_ONLY = "opacity-0 group-hover:opacity-100 focus-visible:opacity-100 checked:opacity-100";
const BOX = "h-4 w-4 shrink-0 rounded-sm border-line accent-accent";

/** The queue: one row per business, city and state under the name, the score on the right. */
export function QueueTable({
  items,
  selected,
  onToggle,
  onToggleBusiness,
  canSelect,
  showWeak = false,
  loading = false,
  empty,
}: Props) {
  const checkboxClass = showWeak ? "" : HOVER_ONLY;
  const columns = canSelect ? 8 : 7;
  return (
    <TableWrap>
      <Table minWidth="68rem">
        <THead>
          <tr>
            {canSelect ? <Th className="w-8" aria-label="Select" /> : null}
            <Th>Business</Th>
            <Th>Industry</Th>
            <Th>Source</Th>
            <Th>Opportunities</Th>
            <Th>Audit</Th>
            <Th>Top findings</Th>
            <Th numeric className="w-20">
              Score
            </Th>
          </tr>
        </THead>
        <TBody>
          {loading && !items.length ? <SkeletonTableRows rows={6} columns={columns} /> : null}
          {!loading && !items.length ? (
            <tr>
              <td colSpan={columns}>{empty}</td>
            </tr>
          ) : null}
          {items.map((item) => {
            const allSelected =
              item.opportunities.length > 0 && item.opportunities.every((o) => selected.has(o.id));
            const anySelected = item.opportunities.some((o) => selected.has(o.id));
            return (
              <Tr key={item.business_id} selected={anySelected} data-testid="queue-row">
                {canSelect ? (
                  <Td>
                    <input
                      type="checkbox"
                      aria-label={`Select every opportunity of ${item.display_name}`}
                      checked={allSelected}
                      onChange={() => onToggleBusiness(item)}
                      className={cx(BOX, checkboxClass)}
                    />
                  </Td>
                ) : null}
                <Td>
                  <Link
                    href={`/review/${item.business_id}`}
                    className="rounded font-medium text-ink underline-offset-2 hover:text-accent hover:underline"
                  >
                    {item.display_name}
                  </Link>
                  <div className="text-sm text-ink-soft" data-testid="queue-place">
                    {place(item.city, item.state)}
                  </div>
                  {item.website ? (
                    <div className="text-sm">
                      <SafeLink href={item.website} />
                    </div>
                  ) : null}
                </Td>
                <Td className="text-ink-soft" title={item.industry ?? undefined}>
                  {industryLabel(item.industry)}
                </Td>
                <Td className="text-ink-soft">
                  <SourceCell codes={item.sources} />
                </Td>
                <Td>
                  <ul className="flex flex-wrap gap-1">
                    {item.opportunities.map((opportunity) => (
                      <li key={opportunity.id}>
                        <Chip
                          as="label"
                          interactive={canSelect}
                          className={cx(opportunity.weak && "border-warn")}
                          title={`${opportunity.service} · confidence ${percent(opportunity.confidence)} · source ${opportunity.source}`}
                          data-testid="service-chip"
                        >
                          {canSelect ? (
                            <input
                              type="checkbox"
                              aria-label={`Select ${serviceLabel(opportunity.service)} for ${item.display_name}`}
                              checked={selected.has(opportunity.id)}
                              onChange={() => onToggle(opportunity.id)}
                              className={cx(BOX, checkboxClass)}
                            />
                          ) : null}
                          <span className="font-medium">{serviceLabel(opportunity.service)}</span>
                          <span className="font-mono text-xs text-ink-soft">
                            Score {score(opportunity.score)}
                          </span>
                        </Chip>
                      </li>
                    ))}
                  </ul>
                  {item.weak_hidden > 0 ? (
                    <p className="mt-1 text-sm text-ink-soft" data-testid="weak-hidden">
                      {item.weak_hidden} weak signal{item.weak_hidden === 1 ? "" : "s"} hidden
                    </p>
                  ) : null}
                </Td>
                <Td>
                  {item.latest_audit ? (
                    <>
                      <Badge tone={AUDIT_TONE[item.latest_audit.status] ?? "neutral"} title={item.latest_audit.status}>
                        {auditStatusLabel(item.latest_audit.status)}
                      </Badge>
                      <div className="mt-1 text-sm text-ink-soft">
                        {formatDateTime(item.latest_audit.audited_at)}
                      </div>
                    </>
                  ) : (
                    <span className="text-ink-soft">not audited</span>
                  )}
                </Td>
                <Td>
                  <ul className="flex flex-wrap gap-1">
                    {item.latest_audit?.top_findings.map((code) => (
                      <li key={code}>
                        <Chip className="text-sm" title={code}>
                          {findingLabel(code)}
                        </Chip>
                      </li>
                    ))}
                  </ul>
                </Td>
                <Td numeric title={`raw ${item.top_score}`}>
                  {score(item.top_score)}
                </Td>
              </Tr>
            );
          })}
        </TBody>
      </Table>
    </TableWrap>
  );
}
