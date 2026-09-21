"use client";

import { useCallback, useEffect, useState } from "react";
import { SafeLink } from "@/components/SafeLink";
import { useToast } from "@/components/ui";
import { ApiError, decideMatchCandidate, getMatchCandidates, type MatchCandidate } from "@/lib/api";
import { orUnknown, place, score } from "@/lib/format";

export function Duplicates() {
  const { show } = useToast();
  const [items, setItems] = useState<MatchCandidate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setItems((await getMatchCandidates()).items);
      setError(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not load duplicates");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function decide(candidate: MatchCandidate, decision: "merge" | "keep_apart") {
    setBusy(candidate.id);
    try {
      await decideMatchCandidate(candidate.id, decision);
      show({ tone: "success", message: decision === "merge" ? "Merged." : "Kept apart." });
      setItems((current) => current.filter((item) => item.id !== candidate.id));
    } catch (caught) {
      show({ tone: "error", message: caught instanceof ApiError ? caught.message : "Could not decide" });
      await load();
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <header>
        <h1 className="text-2xl font-semibold text-navy">Duplicates</h1>
        <p className="text-sm text-slate-600">
          Pairs the resolver would not decide on its own. Merge links the record to the
          business; keep apart leaves them separate.
        </p>
      </header>
      {error ? <p role="alert" className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</p> : null}
      {loading ? <p className="text-sm text-slate-600">Loading…</p> : null}
      {!loading && !items.length ? (
        <p className="rounded border border-dashed border-slate-300 bg-white p-8 text-center text-sm text-slate-600">
          No pending duplicate pairs.
        </p>
      ) : null}
      <ul className="flex flex-col gap-3">
        {items.map((candidate) => (
          <li key={candidate.id} className="rounded-lg border border-slate-200 bg-white p-4" data-testid="duplicate-pair">
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_1fr_14rem]">
              <Side
                title="New record"
                subtitle={candidate.record?.source ?? "unknown source"}
                name={candidate.record?.display_name}
                place={candidate.record?.formatted_address}
                phone={candidate.record?.phone}
                website={candidate.record?.website}
              />
              <Side
                title="Existing business"
                subtitle={candidate.business ? place(candidate.business.city, candidate.business.state) : ""}
                name={candidate.business?.display_name}
                place={
                  candidate.business
                    ? [candidate.business.city, candidate.business.state, candidate.business.postal_code]
                        .filter(Boolean)
                        .join(", ")
                    : null
                }
                phone={candidate.business?.phone_e164}
                website={candidate.business?.website}
              />
              <div className="flex flex-col gap-2 text-sm">
                <p className="font-mono">match score {score(Number(candidate.score))}</p>
                <ul className="text-xs text-slate-600">
                  {Object.entries(candidate.signals ?? {}).map(([key, value]) => (
                    <li key={key}>
                      {key}: {typeof value === "object" ? JSON.stringify(value) : String(value)}
                    </li>
                  ))}
                </ul>
                <div className="mt-auto flex gap-2">
                  <button type="button" className="btn-primary" disabled={busy === candidate.id} onClick={() => decide(candidate, "merge")}>
                    Merge
                  </button>
                  <button type="button" className="btn-secondary" disabled={busy === candidate.id} onClick={() => decide(candidate, "keep_apart")}>
                    Keep apart
                  </button>
                </div>
              </div>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Side({
  title,
  subtitle,
  name,
  place: address,
  phone,
  website,
}: {
  title: string;
  subtitle: string;
  name: string | null | undefined;
  place: string | null | undefined;
  phone: string | null | undefined;
  website: string | null | undefined;
}) {
  return (
    <div>
      <h2 className="text-xs font-medium uppercase tracking-wide text-slate-500">
        {title} <span className="normal-case text-slate-400">· {subtitle}</span>
      </h2>
      <p className="mt-1 font-semibold text-navy">{orUnknown(name)}</p>
      <dl className="mt-1 grid grid-cols-[5rem_1fr] gap-y-0.5 text-sm">
        <dt className="text-slate-500">Address</dt>
        <dd>{orUnknown(address)}</dd>
        <dt className="text-slate-500">Phone</dt>
        <dd>{orUnknown(phone)}</dd>
        <dt className="text-slate-500">Website</dt>
        <dd>{website ? <SafeLink href={website}>{website}</SafeLink> : "none"}</dd>
      </dl>
    </div>
  );
}
