"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ErrorNote } from "@/components/ErrorNote";
import { SafeLink } from "@/components/SafeLink";
import { CrmBadge } from "@/components/crm/CrmBadge";
import { ReasonLines } from "@/components/review/ReasonLines";
import { SourceCell } from "@/components/review/SourceProvenance";
import {
  Button,
  Card,
  EmptyState,
  PageHeader,
  Pagination,
  Select,
  SkeletonTableRows,
  Table,
  TableWrap,
  TBody,
  Td,
  Th,
  THead,
  Tr,
  useToast,
} from "@/components/ui";
import {
  ApiError,
  getLeads,
  RECENCY_OPTIONS,
  retryCrmLead,
  SERVICES,
  type LeadRead,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { loadFailed } from "@/lib/errors";
import { formatDateTime, formatPhone, place, score } from "@/lib/format";
import { serviceLabel } from "@/lib/labels";
import { canManageCrm, canSeeAllLeads } from "@/lib/roles";

/** The chosen window in the words the control used, for the empty state's first line. */
function withinWords(within: string): string {
  return RECENCY_OPTIONS.find((option) => option.value === within)?.label ?? `${within} days`;
}

export function Leads() {
  const { user } = useAuth();
  const { show } = useToast();
  const [items, setItems] = useState<LeadRead[]>([]);
  const [busy, setBusy] = useState(false);
  const crmManager = user ? canManageCrm(user.role) : false;
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [service, setService] = useState("");
  const [rep, setRep] = useState("");
  const [within, setWithin] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const seesAll = user ? canSeeAllLeads(user.role) : false;

  const load = useCallback(
    async (cursor?: string) => {
      setLoading(true);
      try {
        const page = await getLeads({
          service: service || undefined,
          assigned_to: seesAll && rep ? rep : undefined,
          discovered_within_days: within ? Number(within) : undefined,
          cursor,
        });
        setItems((current) => (cursor ? [...current, ...page.items] : page.items));
        setNextCursor(page.next_cursor ?? null);
        setError(null);
      } catch (caught) {
        setError(caught instanceof ApiError ? caught.message : loadFailed("your leads"));
      } finally {
        setLoading(false);
      }
    },
    [service, rep, within, seesAll],
  );

  useEffect(() => {
    void load();
  }, [load]);

  async function retry(crmLeadId: string) {
    setBusy(true);
    try {
      const result = await retryCrmLead(crmLeadId);
      show({ tone: "success", message: `${result.business_name}: ${result.status}.` });
      await load();
    } catch (caught) {
      show({ tone: "error", message: caught instanceof ApiError ? caught.message : "Retry failed" });
    } finally {
      setBusy(false);
    }
  }

  // The rep filter is built from what is on screen, so every role that sees all leads can
  // use it without a users endpoint.
  const reps = useMemo(() => {
    const seen = new Map<string, string>();
    for (const lead of items) {
      if (lead.assigned_to && lead.assigned_to_email) seen.set(lead.assigned_to, lead.assigned_to_email);
    }
    return [...seen.entries()];
  }, [items]);

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title={seesAll ? "Leads" : "My leads"}
        description={`Approved by a reviewer${seesAll ? "" : " and assigned to you"}. Business-level public contact details only.`}
      />

      <Card padded={false} as="div" className="p-3">
        <div className="flex flex-wrap items-end gap-3">
          <Select label="Service" value={service} onChange={(event) => setService(event.target.value)}>
            <option value="">Any service</option>
            {SERVICES.map((item) => (
              <option key={item.key} value={item.key}>
                {item.name}
              </option>
            ))}
          </Select>
          {seesAll ? (
            <Select label="Sales rep" value={rep} onChange={(event) => setRep(event.target.value)}>
              <option value="">Anyone</option>
              {reps.map(([id, email]) => (
                <option key={id} value={id}>
                  {email}
                </option>
              ))}
            </Select>
          ) : null}
          {/* The window is measured on when the business was first found, not on when it
              was approved — the same semantics the review queue uses. */}
          <Select
            label="Found within"
            value={within}
            onChange={(event) => setWithin(event.target.value)}
          >
            {RECENCY_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </Select>
        </div>
      </Card>

      {error ? <ErrorNote>{error}</ErrorNote> : null}

      <TableWrap>
        <Table minWidth="74rem">
          <THead>
            <tr>
              <Th>Business</Th>
              <Th>Source</Th>
              <Th>Service</Th>
              <Th numeric className="w-20">
                Score
              </Th>
              <Th>Reason</Th>
              <Th>Approved</Th>
              <Th>Assigned rep</Th>
              <Th>Contact</Th>
              <Th>CRM</Th>
            </tr>
          </THead>
          <TBody>
            {loading && !items.length ? <SkeletonTableRows rows={5} columns={9} /> : null}
            {!loading && !items.length ? (
              <tr>
                <td colSpan={9}>
                  <EmptyState
                    title={
                      within
                        ? `No lead here was first found in the last ${withinWords(within)}.`
                        : "No leads yet."
                    }
                    description={
                      within
                        ? "Widen the window to see the rest of your leads."
                        : seesAll
                          ? "A lead appears here once a reviewer approves an opportunity."
                          : "A reviewer assigns leads to you."
                    }
                    action={
                      within ? (
                        <Button onClick={() => setWithin("")}>Show any time</Button>
                      ) : undefined
                    }
                  />
                </td>
              </tr>
            ) : null}
            {items.map((lead) => (
              <Tr key={lead.opportunity_id} data-testid="lead-row">
                <Td>
                  <Link
                    href={`/leads/${lead.opportunity_id}`}
                    className="rounded font-medium text-ink underline-offset-2 hover:text-accent hover:underline"
                  >
                    {lead.business_name}
                  </Link>
                  <div className="text-sm text-ink-soft" data-testid="lead-place">
                    {place(lead.city, lead.state)}
                  </div>
                </Td>
                <Td className="text-ink-soft">
                  <SourceCell codes={lead.sources} />
                </Td>
                <Td title={lead.service}>{serviceLabel(lead.service)}</Td>
                <Td numeric title={`raw ${lead.score}`}>
                  {score(lead.score)}
                </Td>
                <Td className="max-w-md">
                  <ReasonLines
                    ruleReason={lead.rule_reason}
                    aiRationale={lead.ai_rationale}
                    fallback={lead.reason}
                    compact
                  />
                </Td>
                <Td className="text-sm text-ink-soft">
                  <div>{lead.approved_by_email ?? "unknown"}</div>
                  <div>{formatDateTime(lead.approved_at)}</div>
                </Td>
                <Td>{lead.assigned_to_email ?? <span className="text-ink-soft">unassigned</span>}</Td>
                <Td className="text-sm">
                  <div className="font-mono" title={lead.phone_e164 ?? undefined}>
                    {formatPhone(lead.phone_e164)}
                  </div>
                  <div>{lead.website ? <SafeLink href={lead.website} /> : "no website"}</div>
                </Td>
                <Td className="text-sm">
                  <CrmBadge crm={lead.crm} canRetry={crmManager} busy={busy} onRetry={retry} />
                </Td>
              </Tr>
            ))}
          </TBody>
        </Table>
      </TableWrap>

      <Pagination
        count={items.length}
        noun={["lead", "leads"]}
        loading={loading}
        hasMore={Boolean(nextCursor)}
        onLoadMore={() => void load(nextCursor ?? undefined)}
      />
    </div>
  );
}
