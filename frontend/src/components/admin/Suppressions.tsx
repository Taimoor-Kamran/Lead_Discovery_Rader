"use client";

import { useCallback, useEffect, useState } from "react";
import { ErrorNote } from "@/components/ErrorNote";
import {
  Button,
  Card,
  Checkbox,
  EmptyState,
  Input,
  PageHeader,
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
import { addSuppression, ApiError, getSuppressions, liftSuppression, type SuppressionRead } from "@/lib/api";
import { loadFailed } from "@/lib/errors";
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
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setItems((await getSuppressions(activeOnly)).items);
      setError(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : loadFailed("the suppressions"));
    } finally {
      setLoading(false);
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
      <PageHeader
        title="Suppressions"
        description="Businesses, domains and phones that never get a new opportunity. Do-not-contact decisions land here too."
      />
      <Card as="form" onSubmit={submit} className="flex flex-wrap items-end gap-3">
        <Input
          label="Domain"
          value={domain}
          onChange={(event) => setDomain(event.target.value)}
          placeholder="example.com"
        />
        <Input
          label="Phone"
          value={phone}
          onChange={(event) => setPhone(event.target.value)}
          placeholder="+1 512 555 0100"
        />
        <Input
          label="Reason"
          controlClassName="w-72"
          required
          value={reason}
          onChange={(event) => setReason(event.target.value)}
        />
        <Button type="submit" variant="primary" loading={busy}>
          Add suppression
        </Button>
        <Checkbox
          label="Active only"
          className="ml-auto pb-2"
          checked={activeOnly}
          onChange={(event) => setActiveOnly(event.target.checked)}
        />
      </Card>
      {error ? <ErrorNote>{error}</ErrorNote> : null}
      <TableWrap>
        <Table minWidth="60rem">
          <THead>
            <tr>
              <Th>Business</Th>
              <Th>Domain</Th>
              <Th>Phone</Th>
              <Th>Reason</Th>
              <Th>Source</Th>
              <Th>Added</Th>
              <Th>Status</Th>
              <Th aria-label="Actions" />
            </tr>
          </THead>
          <TBody>
            {loading && !items.length ? <SkeletonTableRows rows={4} columns={8} /> : null}
            {!loading && !items.length ? (
              <tr>
                <td colSpan={8}>
                  <EmptyState
                    title="Nothing is suppressed."
                    description="Add a domain or a phone above, or mark a business do-not-contact from its review page."
                  />
                </td>
              </tr>
            ) : null}
            {items.map((item) => (
              <Tr key={item.id} data-testid="suppression-row">
                <Td>{item.business_name ?? "—"}</Td>
                <Td className="font-mono text-sm">{item.domain ?? "—"}</Td>
                <Td className="font-mono text-sm">{item.phone_e164 ?? "—"}</Td>
                <Td>{item.reason}</Td>
                <Td>{item.source}</Td>
                <Td className="text-sm text-ink-soft">{formatDateTime(item.created_at)}</Td>
                <Td>{item.active ? "active" : `lifted ${formatDateTime(item.lifted_at)}`}</Td>
                <Td className="text-right">
                  {item.active ? (
                    <Button size="sm" disabled={busy} onClick={() => lift(item)}>
                      Lift
                    </Button>
                  ) : null}
                </Td>
              </Tr>
            ))}
          </TBody>
        </Table>
      </TableWrap>
    </div>
  );
}
