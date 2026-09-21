"use client";

import { useCallback, useEffect, useState } from "react";
import { useToast } from "@/components/ui";
import { addSuppression, ApiError, getSuppressions, liftSuppression, type SuppressionRead } from "@/lib/api";
import { formatDateTime } from "@/lib/format";

export function Suppressions() {
  const { show } = useToast();
  const [items, setItems] = useState<SuppressionRead[]>([]);
  const [activeOnly, setActiveOnly] = useState(true);
  const [domain, setDomain] = useState("");
  const [phone, setPhone] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setItems((await getSuppressions(activeOnly)).items);
      setError(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not load suppressions");
    }
  }, [activeOnly]);

  useEffect(() => {
    void load();
  }, [load]);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!domain.trim() && !phone.trim()) {
      setError("Give a domain or a phone number.");
      return;
    }
    setBusy(true);
    try {
      await addSuppression({
        domain: domain.trim() || null,
        phone_e164: phone.trim() || null,
        reason: reason.trim(),
      });
      show({ tone: "success", message: "Suppression added." });
      setDomain("");
      setPhone("");
      setReason("");
      await load();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not add the suppression");
    } finally {
      setBusy(false);
    }
  }

  async function lift(item: SuppressionRead) {
    setBusy(true);
    try {
      await liftSuppression(item.id);
      show({ tone: "success", message: "Suppression lifted." });
      await load();
    } catch (caught) {
      show({ tone: "error", message: caught instanceof ApiError ? caught.message : "Could not lift" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <header>
        <h1 className="text-2xl font-semibold text-navy">Suppressions</h1>
        <p className="text-sm text-slate-600">
          Businesses, domains and phones that never get a new opportunity. Do-not-contact
          decisions land here too.
        </p>
      </header>
      <form onSubmit={submit} className="flex flex-wrap items-end gap-3 rounded-lg border border-slate-200 bg-white p-3">
        <label className="flex flex-col gap-1 text-xs text-slate-600">
          Domain
          <input className="field" value={domain} onChange={(event) => setDomain(event.target.value)} placeholder="example.com" />
        </label>
        <label className="flex flex-col gap-1 text-xs text-slate-600">
          Phone
          <input className="field" value={phone} onChange={(event) => setPhone(event.target.value)} placeholder="+1 512 555 0100" />
        </label>
        <label className="flex flex-col gap-1 text-xs text-slate-600">
          Reason
          <input className="field w-72" required value={reason} onChange={(event) => setReason(event.target.value)} />
        </label>
        <button type="submit" className="btn-primary" disabled={busy}>Add suppression</button>
        <label className="ml-auto flex items-center gap-2 text-sm">
          <input type="checkbox" checked={activeOnly} onChange={(event) => setActiveOnly(event.target.checked)} />
          Active only
        </label>
      </form>
      {error ? <p role="alert" className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</p> : null}
      <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-600">
            <tr>
              <th scope="col" className="px-3 py-2">Business</th>
              <th scope="col" className="px-3 py-2">Domain</th>
              <th scope="col" className="px-3 py-2">Phone</th>
              <th scope="col" className="px-3 py-2">Reason</th>
              <th scope="col" className="px-3 py-2">Source</th>
              <th scope="col" className="px-3 py-2">Added</th>
              <th scope="col" className="px-3 py-2">Status</th>
              <th scope="col" className="px-3 py-2" />
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {items.map((item) => (
              <tr key={item.id} data-testid="suppression-row">
                <td className="px-3 py-2">{item.business_name ?? "—"}</td>
                <td className="px-3 py-2 font-mono text-xs">{item.domain ?? "—"}</td>
                <td className="px-3 py-2 font-mono text-xs">{item.phone_e164 ?? "—"}</td>
                <td className="px-3 py-2">{item.reason}</td>
                <td className="px-3 py-2">{item.source}</td>
                <td className="px-3 py-2 text-xs text-slate-600">{formatDateTime(item.created_at)}</td>
                <td className="px-3 py-2">{item.active ? "active" : `lifted ${formatDateTime(item.lifted_at)}`}</td>
                <td className="px-3 py-2 text-right">
                  {item.active ? (
                    <button type="button" className="btn-secondary !py-0.5" disabled={busy} onClick={() => lift(item)}>Lift</button>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!items.length ? <p className="p-8 text-center text-sm text-slate-600">No suppressions.</p> : null}
      </div>
    </div>
  );
}
