"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { SafeLink } from "@/components/SafeLink";
import { useToast } from "@/components/ui";
import { CrmBadge } from "@/components/crm/CrmBadge";
import { EvidenceList, type Evidence } from "@/components/review/EvidenceList";
import { FindingList, type Finding } from "@/components/review/FindingList";
import { PsiPanel } from "@/components/review/PsiPanel";
import { ReasonLines } from "@/components/review/ReasonLines";
import { ApiError, getLeadDetail, retryCrmLead, type LeadDetail as LeadDetailData } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { CRM_ACTION_LABELS, formatDateTime, formatPhone, orUnknown, percent, place, score } from "@/lib/format";
import { auditStatusLabel, serviceLabel, sourceLabel } from "@/lib/labels";
import { canManageCrm } from "@/lib/roles";

export const NOT_YOURS_MESSAGE = "This lead is not assigned to you.";

/**
 * One approved lead, read-only: what the business is, why it is a lead, the evidence and
 * who approved it. No decisions here; those live on the review page.
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
        else setError(caught instanceof ApiError ? caught.message : "Could not load this lead");
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
      <p role="alert" className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-800">
        {error} · <Link href="/leads" className="underline">Back to leads</Link>
      </p>
    );
  }
  if (!detail) return <p className="text-sm text-slate-600">Loading…</p>;

  const { lead, business, audit, opportunity } = detail;
  const findings = (audit?.findings as Finding[] | undefined) ?? [];
  const approval = opportunity.history.find((d) => d.decision === "approve" && !d.undone_at);
  const facts: [string, React.ReactNode][] = [
    ["Location", place(business.city, business.state)],
    ["Address", orUnknown(business.address_line1)],
    ["Postal code", orUnknown(business.postal_code)],
    ["Industry", orUnknown(business.industry)],
    ["Public phone", <span key="phone" title={business.phone_e164 ?? undefined}>{formatPhone(business.phone_e164)}</span>],
    ["Website", business.website ? <SafeLink key="site" href={business.website}>{business.website}</SafeLink> : "none"],
    ["Status", business.business_status],
  ];

  return (
    <div className="flex flex-col gap-4" data-testid="lead-detail">
      <header className="flex flex-wrap items-center gap-3">
        <Link href="/leads" className="text-sm text-teal-700 underline underline-offset-2">
          ← Leads
        </Link>
        <h1 className="text-xl font-semibold text-navy">{business.display_name}</h1>
        <span className="text-sm text-slate-600">{place(business.city, business.state)}</span>
        <span className="chip border-slate-300 bg-slate-50 text-navy" title={lead.service}>
          {serviceLabel(lead.service)}
        </span>
        <span
          className="chip border-navy bg-navy font-mono text-white"
          title={`Confidence ${percent(opportunity.confidence)} · raw score ${lead.score}`}
          data-testid="score-chip"
        >
          Score {score(lead.score)}
        </span>
        <CrmBadge crm={lead.crm} canRetry={crmManager} busy={busy} onRetry={retry} />
      </header>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(18rem,1fr)_minmax(24rem,1.6fr)_minmax(20rem,1.2fr)]">
        <section className="flex flex-col gap-3 rounded-lg border border-slate-200 bg-white p-4">
          <h2 className="text-lg font-semibold text-navy">Business</h2>
          <dl className="grid grid-cols-[7rem_1fr] gap-y-1 text-sm">
            {facts.map(([label, value]) => (
              <div key={label} className="contents">
                <dt className="text-slate-500">{label}</dt>
                <dd className="break-words">{value}</dd>
              </div>
            ))}
          </dl>
          <h2 className="mt-2 text-lg font-semibold text-navy">Approval</h2>
          <dl className="grid grid-cols-[7rem_1fr] gap-y-1 text-sm" data-testid="approval">
            <dt className="text-slate-500">Approved by</dt>
            <dd>{lead.approved_by_email ?? "unknown"}</dd>
            <dt className="text-slate-500">Approved at</dt>
            <dd>{formatDateTime(lead.approved_at)}</dd>
            <dt className="text-slate-500">Assigned rep</dt>
            <dd>{lead.assigned_to_email ?? <span className="text-slate-500">unassigned</span>}</dd>
            {approval?.note ? (
              <>
                <dt className="text-slate-500">Reviewer note</dt>
                <dd className="whitespace-pre-wrap">“{approval.note}”</dd>
              </>
            ) : null}
          </dl>
        </section>

        <section className="flex flex-col gap-3 rounded-lg border border-slate-200 bg-white p-4">
          <header className="flex flex-wrap items-center gap-2">
            <h2 className="text-lg font-semibold text-navy">Why this is a lead</h2>
            <span className="chip border-slate-300 bg-slate-100 text-slate-800" title={opportunity.source}>
              {sourceLabel(opportunity.source)}
            </span>
          </header>
          <ReasonLines
            ruleReason={opportunity.rule_reason}
            aiRationale={opportunity.ai_rationale}
            fallback={opportunity.reason}
          />
          <h3 className="text-xs font-medium uppercase tracking-wide text-slate-500">Evidence</h3>
          <EvidenceList items={(opportunity.evidence as Evidence[]) ?? []} />
        </section>

        <section className="flex flex-col gap-3 rounded-lg border border-slate-200 bg-white p-4">
          <header className="flex items-baseline justify-between gap-2">
            <h2 className="text-lg font-semibold text-navy">Website findings</h2>
            {audit ? (
              <span className="text-xs text-slate-500" title={audit.status}>
                {auditStatusLabel(audit.status)} · {formatDateTime(audit.created_at)}
              </span>
            ) : null}
          </header>
          {audit ? (
            <>
              <p className="text-xs text-slate-600">
                Audited <SafeLink href={audit.url_audited}>{audit.url_audited}</SafeLink>
              </p>
              <FindingList findings={findings} />
              <h3 className="text-sm font-medium text-slate-700">PageSpeed</h3>
              <PsiPanel psi={(audit.psi as Record<string, unknown> | null) ?? null} />
            </>
          ) : (
            <p className="text-sm text-slate-600">This business has not been audited.</p>
          )}
        </section>
      </div>

      <section className="rounded-lg border border-slate-200 bg-white p-4" aria-label="CRM sync history" data-testid="crm-history">
        <h2 className="text-lg font-semibold text-navy">CRM sync history</h2>
        {detail.crm_history.length ? (
          <ul className="mt-2 flex flex-col gap-1 text-xs">
            {detail.crm_history.map((attempt) => (
              <li key={attempt.id} className="flex flex-wrap items-center gap-2" data-testid="crm-attempt">
                <span className={attempt.status === "ok" ? "text-teal-700" : "text-amber-900"}>
                  {attempt.status === "ok" ? "OK" : "Failed"}
                </span>
                <span className="font-medium">{CRM_ACTION_LABELS[attempt.action] ?? attempt.action}</span>
                <span className="text-slate-500">{formatDateTime(attempt.created_at)}</span>
                {attempt.http_status ? <span className="font-mono text-slate-500">HTTP {attempt.http_status}</span> : null}
                {attempt.error ? <span className="w-full text-amber-900">{attempt.error}</span> : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-1 text-sm text-slate-600">No sync attempt yet.</p>
        )}
      </section>
    </div>
  );
}
