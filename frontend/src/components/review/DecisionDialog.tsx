"use client";

import { useEffect, useRef, useState } from "react";
import {
  getBusinessOpportunities,
  NOT_A_FIT_REASONS,
  REJECT_REASONS,
  searchBusinesses,
  type BusinessSummary,
  type Decision,
  type OpportunitySummary,
} from "@/lib/api";
import { Button, Dialog, Input, Select, Textarea } from "@/components/ui";
import { place, REASON_LABELS } from "@/lib/format";
import { GoogleMapsAttribution } from "@/components/GoogleMapsAttribution";

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
  const firstField = useRef<HTMLElement | null>(null);

  // The Dialog focuses the first control for us; this only matters when the first control
  // is not the one a reviewer wants (a note before a reason, say).
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
    <Dialog
      as="form"
      title={`${TITLES[decision]}${count > 1 ? ` ${count} opportunities` : ""}`}
      onClose={onCancel}
      onSubmit={submit}
      footer={
        <>
          <Button onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
          <Button
            type="submit"
            variant={decision === "do_not_contact" ? "danger" : "primary"}
            loading={busy}
          >
            {decision === "do_not_contact" ? "Confirm: do not contact" : TITLES[decision]}
          </Button>
        </>
      }
    >
      {decision === "do_not_contact" ? (
        <p className="rounded border border-risk bg-risk-tint p-2 text-base text-ink">
          This closes <strong>every</strong> opportunity of the business and blocks it, its
          domain and its phone from ever getting a new one. It can be undone for a short
          while.
        </p>
      ) : null}

      {reasons ? (
        <Select
          label="Reason"
          ref={(node) => {
            firstField.current = node;
          }}
          name="reason_code"
          required
          value={reason}
          onChange={(event) => setReason(event.target.value)}
        >
          <option value="">Choose a reason…</option>
          {reasons.map((code) => (
            <option key={code} value={code}>
              {REASON_LABELS[code] ?? code}
            </option>
          ))}
        </Select>
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
        <Select
          label="Assign to sales rep (optional)"
          ref={(node) => {
            firstField.current = node;
          }}
          name="assigned_to"
          value={assignee}
          onChange={(event) => setAssignee(event.target.value)}
        >
          <option value="">Unassigned</option>
          {assignees.map((rep) => (
            <option key={rep.id} value={rep.id}>
              {rep.email}
            </option>
          ))}
        </Select>
      ) : null}

      <Textarea
        label={`Note${noteRequired ? " (required)" : " (optional)"}`}
        ref={(node) => {
          if (!reasons && decision !== "approve") firstField.current = node;
        }}
        name="note"
        rows={3}
        required={noteRequired}
        value={note}
        onChange={(event) => setNote(event.target.value)}
      />

      {error ? (
        <p role="alert" className="text-base text-risk">
          {error}
        </p>
      ) : null}
    </Dialog>
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
      onChange({ id: same[0].id, label: `${business.display_name} — ${same[0].review_status}` });
    } else {
      onChange(null);
    }
  }

  return (
    <div className="flex flex-col gap-2 text-base">
      <Input
        label="Duplicate of (search businesses)"
        type="search"
        name="duplicate_search"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        placeholder="Business name or domain"
      />
      {searching ? <p className="text-sm text-ink-soft">Searching…</p> : null}
      {businesses.length ? (
        <ul className="max-h-40 divide-y divide-line overflow-auto rounded border border-line">
          {businesses.map((business) => (
            <li key={business.id}>
              <button
                type="button"
                className="flex w-full justify-between px-2 py-1.5 text-left transition-colors hover:bg-surface-sunken"
                onClick={() => pickBusiness(business)}
              >
                <span>{business.display_name}</span>
                <span className="text-ink-soft">{place(business.city, business.state)}</span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      {/* The search results are Places data, and this dialog covers the page's own
          attribution, so they carry their own (v0.11.1). */}
      {businesses.length ? <GoogleMapsAttribution className="-mx-2.5" /> : null}
      {candidates.length > 1 ? (
        <fieldset className="flex flex-col gap-1">
          <legend className="text-sm font-medium text-ink-soft">Which opportunity?</legend>
          {candidates.map((candidate) => (
            <label key={candidate.id} className="flex items-center gap-2">
              <input
                type="radio"
                name="duplicate_of"
                className="h-4 w-4 border-line accent-accent"
                checked={value?.id === candidate.id}
                onChange={() =>
                  onChange({
                    id: candidate.id,
                    label: `${candidate.business_name} — ${candidate.review_status}`,
                  })
                }
              />
              {candidate.business_name} — {candidate.review_status}, score{" "}
              <span className="font-mono tabular-nums">{candidate.score.toFixed(2)}</span>
            </label>
          ))}
        </fieldset>
      ) : null}
      {candidates.length === 0 && businesses.length === 0 && query.length >= 2 && !searching ? (
        <p className="text-sm text-ink-soft">No business matches.</p>
      ) : null}
      {value ? (
        <p className="rounded bg-accent-tint px-2 py-1 text-sm text-ink" data-testid="duplicate-pick">
          Duplicate of: {value.label}
        </p>
      ) : null}
    </div>
  );
}
