"use client";

import Link from "next/link";
import { GoogleMapsAttribution, distinctProviders } from "@/components/GoogleMapsAttribution";
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
  type SortDirection,
} from "@/components/ui";
import { SourceCell } from "@/components/review/SourceProvenance";
import type { QueueItem } from "@/lib/api";
import { formatDateTime, percent, place, reviewCount, score } from "@/lib/format";
import {
  auditStatusLabel,
  findingLabel,
  industryLabel,
  serviceLabel,
  websiteKindLabel,
} from "@/lib/labels";

export type QueueSortKey = "score" | "reviews";
export type QueueSortDir = "asc" | "desc";

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
  /** The server-side order. Without `onSort` the headers are plain, not buttons. */
  sort?: QueueSortKey;
  sortDir?: QueueSortDir;
  onSort?: (key: QueueSortKey) => void;
};

/** The F9 badges: words read straight off the listing, never inferred. */
const BADGES: Record<string, { label: string; tone: "neutral" | "warn" | "risk"; title: string }> = {
  no_website: { label: "No website", tone: "warn", title: "The listing gives no website" },
  closed_permanently: { label: "Permanently closed", tone: "risk", title: "The listing says permanently closed" },
};

/** "—" for a value nobody measured, with the reason in the tooltip; never a 0. */
function Unmeasured({ why, testId }: { why: string; testId?: string }) {
  return (
    <span className="text-ink-soft" title={why} aria-label={`Not measured: ${why}`} data-testid={testId}>
      —
    </span>
  );
}

/** An audit outcome is a status, so it gets a badge tone rather than a bare colour. */
const AUDIT_TONE: Record<string, "neutral" | "ok" | "warn" | "risk"> = {
  done: "ok",
  skipped: "neutral",
  robots_blocked: "warn",
  bot_challenge: "warn",
  not_readable: "warn",
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
  sort = "score",
  sortDir = "desc",
  onSort,
}: Props) {
  const checkboxClass = showWeak ? "" : HOVER_ONLY;
  const columns = canSelect ? 12 : 11;
  const direction: SortDirection = sortDir === "asc" ? "ascending" : "descending";
  const sortOf = (key: QueueSortKey) => (onSort ? (sort === key ? direction : "none") : undefined);
  return (
    <TableWrap>
      <Table minWidth="86rem">
        <THead>
          <tr>
            {canSelect ? <Th className="w-8" aria-label="Select" /> : null}
            <Th>Business</Th>
            <Th>Industry</Th>
            <Th>Source</Th>
            <Th>Opportunities</Th>
            <Th>Audit</Th>
            <Th>Top findings</Th>
            <Th numeric title="How many findings the latest audit filed">
              Findings
            </Th>
            <Th numeric title="PageSpeed mobile performance, 0–100">
              PageSpeed
            </Th>
            <Th>Website</Th>
            <Th
              numeric
              sort={sortOf("reviews")}
              onSort={onSort ? () => onSort("reviews") : undefined}
              title="Review count on the listing; listings without one sort last"
            >
              Reviews
            </Th>
            <Th numeric className="w-20" sort={sortOf("score")} onSort={onSort ? () => onSort("score") : undefined}>
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
                  {item.badges.length ? (
                    <ul className="mt-1 flex flex-wrap gap-1">
                      {item.badges.map((badge) => (
                        <li key={badge}>
                          <Badge tone={BADGES[badge]?.tone ?? "neutral"} title={BADGES[badge]?.title} data-testid="data-badge">
                            {BADGES[badge]?.label ?? badge}
                          </Badge>
                        </li>
                      ))}
                    </ul>
                  ) : null}
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
                <Td numeric data-testid="finding-count">
                  {!item.latest_audit ? (
                    <Unmeasured why="Not audited yet" />
                  ) : item.latest_audit.finding_count === null || item.latest_audit.finding_count === undefined ? (
                    <Unmeasured why="The audit did not read the page" />
                  ) : (
                    item.latest_audit.finding_count
                  )}
                </Td>
                <Td numeric data-testid="pagespeed">
                  {item.latest_audit?.pagespeed_score === null || item.latest_audit?.pagespeed_score === undefined ? (
                    <Unmeasured why="PageSpeed was not measured for this site" />
                  ) : (
                    <span title="PageSpeed mobile performance, 0–100">{item.latest_audit.pagespeed_score}</span>
                  )}
                </Td>
                <Td className="text-sm text-ink-soft" data-testid="website-kind">
                  {websiteKindLabel(item.website_kind)}
                </Td>
                <Td numeric data-testid="reviews">
                  {reviewCount(item.user_rating_count) ? (
                    <>
                      <span
                        title={
                          item.rating === null || item.rating === undefined
                            ? "Reviews on the business listing"
                            : `Rated ${item.rating.toFixed(1)} on the business listing`
                        }
                        data-testid="review-chip"
                      >
                        {item.user_rating_count}
                      </span>
                      {item.rating === null || item.rating === undefined ? null : (
                        <div className="text-xs text-ink-soft">★ {item.rating.toFixed(1)}</div>
                      )}
                    </>
                  ) : (
                    <Unmeasured why="The listing gives no review count" />
                  )}
                </Td>
                <Td numeric title={`raw ${item.top_score}`}>
                  {score(item.top_score)}
                </Td>
              </Tr>
            );
          })}
        </TBody>
      </Table>
      {/* Every row is Places data; the attribution sits in the same container (v0.11.1). */}
      {items.length ? (
        <GoogleMapsAttribution providers={distinctProviders(items)} className="sticky left-0 border-t border-line" />
      ) : null}
    </TableWrap>
  );
}
