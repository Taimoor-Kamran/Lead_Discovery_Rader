"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ErrorNote } from "@/components/ErrorNote";
import { SafeLink } from "@/components/SafeLink";
import { CrmBadge } from "@/components/crm/CrmBadge";
import { EvidenceList, type Evidence } from "@/components/review/EvidenceList";
import { FindingList, type Finding } from "@/components/review/FindingList";
import { PsiPanel } from "@/components/review/PsiPanel";
import { ReasonLines } from "@/components/review/ReasonLines";
import { SourceProvenance } from "@/components/review/SourceProvenance";
import { Badge, Card, Chip, PageHeader, SkeletonLines, useToast } from "@/components/ui";
import { ApiError, getLeadDetail, retryCrmLead, type LeadDetail as LeadDetailData } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { loadFailed } from "@/lib/errors";
import { CRM_ACTION_LABELS, formatDateTime, formatPhone, listingRating, orUnknown, percent, place, score } from "@/lib/format";
import { auditStatusLabel, industryLabel, serviceLabel, sourceLabel } from "@/lib/labels";
import { canManageCrm } from "@/lib/roles";

export const NOT_YOURS_MESSAGE = "This lead is not assigned to you.";

/**
 * One approved lead, read-only: what the business is, why it is a lead, the evidence and
 * who approved it. No decisions here; those live on the review page.
 *
 * Reps print or PDF this before a call, so the page is built to print: the contact block
 * is a letterhead at the top, the argument flows underneath, and the navigation, the CRM
 * history and the screen-only chrome drop out (see the `@media print` block in globals.css).
 */
export function LeadDetail({ opportunityId }: { opportunityId: string }) {
  const { user } = useAuth();
  const { show } = useToast();
  const [detail, setDetail] = useState<LeadDetailData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const crmManager = user ? canManageCrm(user.role) : false;

  useEffect(() => {
    let cancelled = false;
    getLeadDetail(opportunityId)
      .then((data) => {
        if (!cancelled) setDetail(data);
      })
      .catch((caught: unknown) => {
        if (cancelled) return;
        if (caught instanceof ApiError && caught.status === 403) setError(NOT_YOURS_MESSAGE);
        else if (caught instanceof ApiError && caught.status === 404) setError("Lead not found.");
        else setError(caught instanceof ApiError ? caught.message : loadFailed("this lead"));
      });
    return () => {
      cancelled = true;
    };
  }, [opportunityId]);

  async function retry(crmLeadId: string) {
    setBusy(true);
    try {
      const result = await retryCrmLead(crmLeadId);
      show({ tone: "success", message: `${result.business_name}: ${result.status}.` });
      setDetail(await getLeadDetail(opportunityId));
    } catch (caught) {
      show({ tone: "error", message: caught instanceof ApiError ? caught.message : "Retry failed" });
    } finally {
      setBusy(false);
    }
  }

  if (error) {
    return (
      <ErrorNote>
        {error}{" "}
        <Link href="/leads" className="rounded font-medium text-accent underline underline-offset-2">
          Back to leads
        </Link>
      </ErrorNote>
    );
  }
  if (!detail) return <SkeletonLines lines={6} className="max-w-measure" />;

  const { lead, business, audit, opportunity } = detail;
  const findings = (audit?.findings as Finding[] | undefined) ?? [];
  const approval = opportunity.history.find((d) => d.decision === "approve" && !d.undone_at);

  return (
    <div className="flex flex-col gap-5" data-testid="lead-detail">
      <PageHeader
        className="print-hide"
        meta={
          <Link href="/leads" className="rounded font-medium text-accent underline underline-offset-2">
            ← Leads
          </Link>
        }
        title={business.display_name}
        description={place(business.city, business.state)}
        actions={
          <>
            <Chip title={lead.service}>{serviceLabel(lead.service)}</Chip>
            <span
              className="font-mono text-md font-medium tabular-nums text-ink"
              title={`Confidence ${percent(opportunity.confidence)} · raw score ${lead.score}`}
              data-testid="score-chip"
            >
              Score {score(lead.score)}
            </span>
            <CrmBadge crm={lead.crm} canRetry={crmManager} busy={busy} onRetry={retry} />
          </>
        }
      />

      {/* The letterhead: what a rep dials or types, first on screen and first on paper. */}
      <Card className="print-break-avoid print-plain">
        <h2 className="print-only text-md font-semibold">
          {business.display_name} — {serviceLabel(lead.service)}
        </h2>
        <dl className="print-cols-4 grid grid-cols-1 gap-x-8 gap-y-3 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <dt className="text-sm text-ink-soft">Public phone</dt>
            <dd className="font-mono text-md text-ink" title={business.phone_e164 ?? undefined}>
              {formatPhone(business.phone_e164)}
            </dd>
          </div>
          <div className="min-w-0">
            <dt className="text-sm text-ink-soft">Website</dt>
            <dd className="break-words text-md">
              {business.website ? <SafeLink href={business.website}>{business.website}</SafeLink> : "none"}
            </dd>
          </div>
          <div>
            <dt className="text-sm text-ink-soft">Listing rating</dt>
            <dd className="text-md" data-testid="listing-rating">
              {listingRating(business.rating, business.user_rating_count)}
            </dd>
          </div>
          <div>
            <dt className="text-sm text-ink-soft">Industry</dt>
            <dd className="text-md" title={business.industry ?? undefined}>
              {industryLabel(business.industry)}
            </dd>
          </div>
          <div>
            <dt className="text-sm text-ink-soft">Address</dt>
            <dd>
              {orUnknown(business.address_line1)}
              <span className="block text-sm text-ink-soft">
                {place(business.city, business.state)}
                {business.postal_code ? ` ${business.postal_code}` : ""}
              </span>
            </dd>
          </div>
        </dl>
      </Card>

      <div className="print-tight grid grid-cols-1 gap-5 lg:grid-cols-2">
        <Card className="print-break-avoid print-plain">
          <header className="flex flex-wrap items-center gap-2">
            <h2 className="text-md font-semibold text-ink">Why this is a lead</h2>
            <Badge title={opportunity.source}>{sourceLabel(opportunity.source)}</Badge>
          </header>
          <div className="mt-3">
            <ReasonLines
              ruleReason={opportunity.rule_reason}
              aiRationale={detail.ai_enabled === false ? null : opportunity.ai_rationale}
              fallback={opportunity.reason}
            />
          </div>
          <h3 className="mb-1 mt-4 text-sm font-semibold text-ink">Evidence</h3>
          <EvidenceList items={(opportunity.evidence as Evidence[]) ?? []} />
        </Card>

        <Card className="print-break-avoid print-plain">
          <header className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="text-md font-semibold text-ink">Website findings</h2>
            {audit ? (
              <span className="text-sm text-ink-soft" title={audit.status}>
                {auditStatusLabel(audit.status)} {formatDateTime(audit.created_at)}
              </span>
            ) : null}
          </header>
          {audit ? (
            <>
              <p className="mt-1 text-sm text-ink-soft">
                Audited <SafeLink href={audit.url_audited}>{audit.url_audited}</SafeLink>
              </p>
              <div className="mt-3">
                <FindingList findings={findings} />
              </div>
              <div className="print-hide mt-4 border-t border-line pt-3">
                <h3 className="mb-2 text-base font-semibold text-ink">Speed and quality</h3>
                <PsiPanel psi={(audit.psi as Record<string, unknown> | null) ?? null} />
              </div>
            </>
          ) : (
            <p className="mt-2 text-base text-ink-soft">This business has not been audited.</p>
          )}
        </Card>
      </div>

      {/* The answer to "where did you get my details?", on the page a rep has open when
          they are asked it. */}
      <SourceProvenance sources={detail.sources} linkedProfiles={detail.linked_profiles} />

      <div className="print-tight grid grid-cols-1 gap-5 lg:grid-cols-2">
        <Card className="print-break-avoid print-plain">
          <h2 className="text-md font-semibold text-ink">Approval</h2>
          <dl className="print-dl mt-2 grid grid-cols-[8rem_1fr] gap-y-1.5 text-base" data-testid="approval">
            <dt className="text-ink-soft">Approved by</dt>
            <dd>{lead.approved_by_email ?? "unknown"}</dd>
            <dt className="text-ink-soft">Approved at</dt>
            <dd>{formatDateTime(lead.approved_at)}</dd>
            <dt className="text-ink-soft">Assigned rep</dt>
            <dd>{lead.assigned_to_email ?? <span className="text-ink-soft">unassigned</span>}</dd>
            {approval?.note ? (
              <>
                <dt className="text-ink-soft">Reviewer note</dt>
                <dd className="whitespace-pre-wrap">“{approval.note}”</dd>
              </>
            ) : null}
          </dl>
        </Card>

        <Card className="print-hide" aria-label="CRM sync history" data-testid="crm-history">
          <h2 className="text-md font-semibold text-ink">CRM sync history</h2>
          {detail.crm_history.length ? (
            <ul className="mt-2 flex flex-col gap-2 text-sm">
              {detail.crm_history.map((attempt) => (
                <li key={attempt.id} className="flex flex-wrap items-center gap-2" data-testid="crm-attempt">
                  <Badge tone={attempt.status === "ok" ? "ok" : "warn"}>
                    {attempt.status === "ok" ? "OK" : "Failed"}
                  </Badge>
                  <span className="font-medium">{CRM_ACTION_LABELS[attempt.action] ?? attempt.action}</span>
                  <span className="text-ink-soft">{formatDateTime(attempt.created_at)}</span>
                  {attempt.http_status ? (
                    <span className="font-mono text-ink-soft">HTTP {attempt.http_status}</span>
                  ) : null}
                  {attempt.error ? <span className="w-full text-warn">{attempt.error}</span> : null}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-1 text-base text-ink-soft">No sync attempt yet.</p>
          )}
        </Card>
      </div>
    </div>
  );
}
