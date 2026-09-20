"use client";

import { useEffect, useId, useRef, useState } from "react";
import {
  getBusinessOpportunities,
  NOT_A_FIT_REASONS,
  REJECT_REASONS,
  searchBusinesses,
  type BusinessSummary,
  type Decision,
  type OpportunitySummary,
} from "@/lib/api";
import { place, REASON_LABELS } from "@/lib/format";

export type DecisionFields = {
  reason_code?: string;
  note?: string;
  duplicate_of?: string;
  assigned_to?: string;
};

export type Assignee = { id: string; email: string };

type Props = {
  decision: Decision;
  /** How many opportunities this decision applies to (batch mode when > 1). */
  count?: number;
  /** For `duplicate`: the service the target must share, and the id to exclude. */
  service?: string;
  excludeOpportunityId?: string;
  /** For `approve`: the reps that may be picked. Empty hides the picker. */
  assignees?: Assignee[];
  busy?: boolean;
  onSubmit: (fields: DecisionFields) => void | Promise<void>;
  onCancel: () => void;
};

const TITLES: Record<Decision, string> = {
  approve: "Approve",
  reject: "Reject",
  needs_enrichment: "Needs enrichment",
  duplicate: "Mark as duplicate",
  not_a_fit: "Not a fit",
  do_not_contact: "Do not contact",
};

/**
 * One dialog for every decision; the fields it demands are the spec's table. It never
 * submits without what the API would refuse anyway, so a reviewer sees the rule here.
 */
export function DecisionDialog({
  decision,
  count = 1,
  service,
  excludeOpportunityId,
  assignees = [],
  busy = false,
  onSubmit,
  onCancel,
}: Props) {
  const [reason, setReason] = useState("");
  const [note, setNote] = useState("");
  const [assignee, setAssignee] = useState("");
  const [duplicateOf, setDuplicateOf] = useState<{ id: string; label: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const headingId = useId();
  const firstField = useRef<HTMLElement | null>(null);

  useEffect(() => {
    firstField.current?.focus();
  }, []);

  const reasons =
    decision === "reject" ? REJECT_REASONS : decision === "not_a_fit" ? NOT_A_FIT_REASONS : null;
  const noteRequired =
    decision === "needs_enrichment" || decision === "do_not_contact" || reason === "other";

  function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (reasons && !reason) {
      setError("Pick a reason.");
      return;
    }
    if (noteRequired && !note.trim()) {
      setError("A note is required.");
      return;
    }
    if (decision === "duplicate" && !duplicateOf) {
      setError("Pick the opportunity this duplicates.");
      return;
    }
    setError(null);
    const fields: DecisionFields = {};
    if (reasons) fields.reason_code = reason;
    if (note.trim()) fields.note = note.trim();
    if (decision === "duplicate" && duplicateOf) fields.duplicate_of = duplicateOf.id;
    if (decision === "approve" && assignee) fields.assigned_to = assignee;
    void onSubmit(fields);
  }

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center bg-navy-900/50 p-4"
      onKeyDown={(event) => {
        if (event.key === "Escape") onCancel();
      }}
    >
      <form
        role="dialog"
        aria-modal="true"
        aria-labelledby={headingId}
        onSubmit={submit}
        className="flex w-full max-w-md flex-col gap-3 rounded-lg border border-slate-200 bg-white p-5 shadow-xl"
      >
        <h2 id={headingId} className="text-lg font-semibold text-navy">
          {TITLES[decision]}
          {count > 1 ? ` ${count} opportunities` : ""}
        </h2>
        {decision === "do_not_contact" ? (
          <p className="rounded border border-red-200 bg-red-50 p-2 text-sm text-red-900">
            This closes <strong>every</strong> opportunity of the business and blocks it, its
            domain and its phone from ever getting a new one. It can be undone for a short
            while.
          </p>
        ) : null}

        {reasons ? (
          <label className="flex flex-col gap-1 text-sm">
            Reason
            <select
              ref={(node) => {
                firstField.current = node;
              }}
              name="reason_code"
              required
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              className="field"
            >
              <option value="">Choose a reason…</option>
              {reasons.map((code) => (
                <option key={code} value={code}>
                  {REASON_LABELS[code] ?? code}
                </option>
              ))}
            </select>
          </label>
        ) : null}

        {decision === "duplicate" && service ? (
          <DuplicatePicker
            service={service}
            exclude={excludeOpportunityId}
            value={duplicateOf}
            onChange={setDuplicateOf}
          />
        ) : null}

        {decision === "approve" && assignees.length ? (
          <label className="flex flex-col gap-1 text-sm">
            Assign to sales rep (optional)
            <select
              ref={(node) => {
                firstField.current = node;
              }}
              name="assigned_to"
              value={assignee}
              onChange={(event) => setAssignee(event.target.value)}
              className="field"
            >
              <option value="">Unassigned</option>
              {assignees.map((rep) => (
                <option key={rep.id} value={rep.id}>
                  {rep.email}
                </option>
              ))}
            </select>
          </label>
        ) : null}

        <label className="flex flex-col gap-1 text-sm">
          Note{noteRequired ? " (required)" : " (optional)"}
          <textarea
            ref={(node) => {
              if (!reasons && decision !== "approve") firstField.current = node;
            }}
            name="note"
            rows={3}
            required={noteRequired}
            value={note}
            onChange={(event) => setNote(event.target.value)}
            className="field"
          />
        </label>

        {error ? (
          <p role="alert" className="text-sm text-red-700">
            {error}
          </p>
        ) : null}

        <div className="flex justify-end gap-2">
          <button type="button" className="btn-secondary" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button
            type="submit"
            className={decision === "do_not_contact" ? "btn-danger" : "btn-primary"}
            disabled={busy}
          >
            {decision === "do_not_contact" ? "Confirm: do not contact" : TITLES[decision]}
          </button>
        </div>
      </form>
    </div>
  );
}

function DuplicatePicker({
  service,
  exclude,
  value,
  onChange,
}: {
  service: string;
  exclude?: string;
  value: { id: string; label: string } | null;
  onChange: (value: { id: string; label: string } | null) => void;
}) {
  const [query, setQuery] = useState("");
  const [businesses, setBusinesses] = useState<BusinessSummary[]>([]);
  const [candidates, setCandidates] = useState<OpportunitySummary[]>([]);
  const [searching, setSearching] = useState(false);

  useEffect(() => {
    if (query.trim().length < 2) {
      setBusinesses([]);
      return;
    }
    let cancelled = false;
    const handle = window.setTimeout(async () => {
      setSearching(true);
      try {
        const page = await searchBusinesses(query.trim());
        if (!cancelled) setBusinesses(page.items);
      } catch {
        if (!cancelled) setBusinesses([]);
      } finally {
        if (!cancelled) setSearching(false);
      }
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(handle);
    };
  }, [query]);

  async function pickBusiness(business: BusinessSummary) {
    const page = await getBusinessOpportunities(business.id);
    const same = page.items.filter((o) => o.service === service && o.id !== exclude);
    setCandidates(same);
    if (same.length === 1) {
      onChange({ id: same[0].id, label: `${business.display_name} · ${same[0].review_status}` });
    } else {
      onChange(null);
    }
  }

  return (
    <div className="flex flex-col gap-2 text-sm">
      <label className="flex flex-col gap-1">
        Duplicate of (search businesses)
        <input
          type="search"
          name="duplicate_search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Business name or domain"
          className="field"
        />
      </label>
      {searching ? <p className="text-xs text-slate-500">Searching…</p> : null}
      {businesses.length ? (
        <ul className="max-h-40 divide-y overflow-auto rounded border border-slate-200">
          {businesses.map((business) => (
            <li key={business.id}>
              <button
                type="button"
                className="flex w-full justify-between px-2 py-1 text-left hover:bg-slate-50"
                onClick={() => pickBusiness(business)}
              >
                <span>{business.display_name}</span>
                <span className="text-slate-500">{place(business.city, business.state)}</span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      {candidates.length > 1 ? (
        <fieldset className="flex flex-col gap-1">
          <legend className="text-xs text-slate-600">Which opportunity?</legend>
          {candidates.map((candidate) => (
            <label key={candidate.id} className="flex items-center gap-2">
              <input
                type="radio"
                name="duplicate_of"
                checked={value?.id === candidate.id}
                onChange={() =>
                  onChange({
                    id: candidate.id,
                    label: `${candidate.business_name} · ${candidate.review_status}`,
                  })
                }
              />
              {candidate.business_name} · {candidate.review_status} · score{" "}
              {candidate.score.toFixed(2)}
            </label>
          ))}
        </fieldset>
      ) : null}
      {candidates.length === 0 && businesses.length === 0 && query.length >= 2 && !searching ? (
        <p className="text-xs text-slate-500">No business matches.</p>
      ) : null}
      {value ? (
        <p className="rounded bg-teal-50 px-2 py-1 text-xs text-teal-700" data-testid="duplicate-pick">
          Duplicate of: {value.label}
        </p>
      ) : null}
    </div>
  );
}
