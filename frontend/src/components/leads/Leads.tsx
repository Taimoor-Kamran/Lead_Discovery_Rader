"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { SafeLink } from "@/components/SafeLink";
import { useToast } from "@/components/Toast";
import { CrmBadge } from "@/components/crm/CrmBadge";
import { ReasonLines } from "@/components/review/ReasonLines";
import { ApiError, getLeads, retryCrmLead, SERVICES, type LeadRead } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatDateTime, formatPhone, place, score } from "@/lib/format";
import { serviceLabel } from "@/lib/labels";
import { canManageCrm, canSeeAllLeads } from "@/lib/roles";

export function Leads() {
  const { user } = useAuth();
  const { show } = useToast();
  const [items, setItems] = useState<LeadRead[]>([]);
  const [busy, setBusy] = useState(false);
  const crmManager = user ? canManageCrm(user.role) : false;
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [service, setService] = useState("");
  const [rep, setRep] = useState("");
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
          cursor,
        });
        setItems((current) => (cursor ? [...current, ...page.items] : page.items));
        setNextCursor(page.next_cursor ?? null);
        setError(null);
      } catch (caught) {
        setError(caught instanceof ApiError ? caught.message : "Could not load leads");
      } finally {
        setLoading(false);
      }
    },
    [service, rep, seesAll],
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
      <header>
        <h1 className="text-2xl font-semibold text-navy">{seesAll ? "Leads" : "My leads"}</h1>
        <p className="text-sm text-slate-600">
          Approved by a reviewer{seesAll ? "" : " and assigned to you"}. Business-level public
          contact details only.
        </p>
      </header>
      <div className="flex flex-wrap items-end gap-3 rounded-lg border border-slate-200 bg-white p-3">
        <label className="flex flex-col gap-1 text-xs text-slate-600">
          Service
          <select className="field" value={service} onChange={(event) => setService(event.target.value)}>
            <option value="">Any service</option>
            {SERVICES.map((item) => (
              <option key={item.key} value={item.key}>{item.name}</option>
            ))}
          </select>
        </label>
        {seesAll ? (
          <label className="flex flex-col gap-1 text-xs text-slate-600">
            Sales rep
            <select className="field" value={rep} onChange={(event) => setRep(event.target.value)}>
              <option value="">Anyone</option>
              {reps.map(([id, email]) => (
                <option key={id} value={id}>{email}</option>
              ))}
            </select>
          </label>
        ) : null}
      </div>
      {error ? <p role="alert" className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</p> : null}
      <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
        <table className="w-full min-w-[960px] text-sm">
          <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-600">
            <tr>
              <th scope="col" className="px-3 py-2">Business</th>
              <th scope="col" className="px-3 py-2">Service</th>
              <th scope="col" className="px-3 py-2 text-right">Score</th>
              <th scope="col" className="px-3 py-2">Reason</th>
              <th scope="col" className="px-3 py-2">Approved</th>
              <th scope="col" className="px-3 py-2">Assigned rep</th>
              <th scope="col" className="px-3 py-2">Contact</th>
              <th scope="col" className="px-3 py-2">CRM</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {items.map((lead) => (
              <tr key={lead.opportunity_id} className="align-top" data-testid="lead-row">
                <td className="px-3 py-2">
                  <Link href={`/leads/${lead.opportunity_id}`} className="font-medium text-navy hover:underline">
                    {lead.business_name}
                  </Link>
                  <div className="text-xs text-slate-600" data-testid="lead-place">{place(lead.city, lead.state)}</div>
                </td>
                <td className="px-3 py-2" title={lead.service}>{serviceLabel(lead.service)}</td>
                <td className="px-3 py-2 text-right font-mono" title={`raw ${lead.score}`}>{score(lead.score)}</td>
                <td className="max-w-md px-3 py-2 text-slate-700">
                  <ReasonLines
                    ruleReason={lead.rule_reason}
                    aiRationale={lead.ai_rationale}
                    fallback={lead.reason}
                    compact
                  />
                </td>
                <td className="px-3 py-2 text-xs text-slate-600">
                  {lead.approved_by_email ?? "unknown"}
                  <br />
                  {formatDateTime(lead.approved_at)}
                </td>
                <td className="px-3 py-2">{lead.assigned_to_email ?? <span className="text-slate-500">unassigned</span>}</td>
                <td className="px-3 py-2 text-xs">
                  <div title={lead.phone_e164 ?? undefined}>{formatPhone(lead.phone_e164)}</div>
                  <div>{lead.website ? <SafeLink href={lead.website} /> : "no website"}</div>
                </td>
                <td className="px-3 py-2 text-xs">
                  <CrmBadge crm={lead.crm} canRetry={crmManager} busy={busy} onRetry={retry} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!loading && !items.length ? (
          <p className="p-8 text-center text-sm text-slate-600">No leads yet.</p>
        ) : null}
      </div>
      <div className="flex items-center gap-3 text-sm text-slate-600">
        {loading ? <span>Loading…</span> : <span>{items.length} lead{items.length === 1 ? "" : "s"}</span>}
        {nextCursor ? (
          <button type="button" className="btn-secondary" onClick={() => load(nextCursor)}>Load more</button>
        ) : null}
      </div>
    </div>
  );
}
