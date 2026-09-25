"use client";

import { useCallback, useEffect, useState } from "react";
import { ErrorNote } from "@/components/ErrorNote";
import { SafeLink } from "@/components/SafeLink";
import {
  Button,
  Card,
  EmptyState,
  PageHeader,
  SkeletonLines,
  useToast,
} from "@/components/ui";
import { ApiError, decideMatchCandidate, getMatchCandidates, type MatchCandidate } from "@/lib/api";
import { loadFailed } from "@/lib/errors";
import { orUnknown, place, score } from "@/lib/format";
import { GoogleMapsAttribution } from "@/components/GoogleMapsAttribution";

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
      setError(caught instanceof ApiError ? caught.message : loadFailed("the duplicate pairs"));
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
      <PageHeader
        title="Duplicates"
        description="Pairs the resolver would not decide on its own. Merge links the record to the business; keep apart leaves them separate."
      />
      {error ? <ErrorNote>{error}</ErrorNote> : null}
      {loading ? <SkeletonLines lines={4} /> : null}
      {!loading && !items.length ? (
        <Card className="border-dashed">
          <EmptyState
            title="No pairs to decide."
            description="The resolver merges what it is sure about on its own. A pair only lands here when it is not."
          />
        </Card>
      ) : null}
      <ul className="flex flex-col gap-4">
        {items.map((candidate) => (
          <li key={candidate.id}>
            <Card data-testid="duplicate-pair">
              <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_1fr_14rem]">
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
                <div className="flex flex-col gap-3">
                  <p>
                    <span className="block text-sm text-ink-soft">Match score</span>
                    <span className="font-mono text-md tabular-nums">{score(Number(candidate.score))}</span>
                  </p>
                  <dl className="grid grid-cols-[1fr_auto] gap-x-3 gap-y-0.5 text-sm text-ink-soft">
                    {Object.entries(candidate.signals ?? {}).map(([key, value]) => (
                      <div key={key} className="contents">
                        <dt>{key.replace(/_/g, " ")}</dt>
                        <dd className="font-mono">
                          {typeof value === "object" ? JSON.stringify(value) : String(value)}
                        </dd>
                      </div>
                    ))}
                  </dl>
                  <div className="mt-auto flex gap-2">
                    <Button
                      variant="primary"
                      disabled={busy === candidate.id}
                      onClick={() => decide(candidate, "merge")}
                    >
                      Merge
                    </Button>
                    <Button disabled={busy === candidate.id} onClick={() => decide(candidate, "keep_apart")}>
                      Keep apart
                    </Button>
                  </div>
                </div>
              </div>
              {/* Both sides are Places data: attributed inside the pair's card (v0.11.1). */}
              <GoogleMapsAttribution providers={candidate.data_providers} className="-mx-2.5 mt-2" />
            </Card>
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
    <div className="min-w-0">
      <h3 className="text-sm font-medium text-ink-soft">
        {title}
        {subtitle ? <span className="ml-2 font-normal">{subtitle}</span> : null}
      </h3>
      <p className="mt-1 text-md font-semibold text-ink">{orUnknown(name)}</p>
      <dl className="mt-2 grid grid-cols-[5rem_1fr] gap-y-1 text-base">
        <dt className="text-ink-soft">Address</dt>
        <dd className="break-words">{orUnknown(address)}</dd>
        <dt className="text-ink-soft">Phone</dt>
        <dd className="font-mono">{orUnknown(phone)}</dd>
        <dt className="text-ink-soft">Website</dt>
        <dd className="break-words">{website ? <SafeLink href={website}>{website}</SafeLink> : "none"}</dd>
      </dl>
    </div>
  );
}
