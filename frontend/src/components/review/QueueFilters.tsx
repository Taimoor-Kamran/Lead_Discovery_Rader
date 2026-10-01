"use client";

import { Card, Checkbox, Input, Select } from "@/components/ui";
import { useEffect, useState } from "react";
import { getIndustries, RECENCY_OPTIONS, SERVICES, type IndustryOption } from "@/lib/api";
import type { QueueSortDir, QueueSortKey } from "@/components/review/QueueTable";

export type QueueFilterState = {
  status: "pending" | "needs_enrichment";
  service: string;
  city: string;
  /** Two-letter state, as stored on the listing. */
  state: string;
  industry: string;
  /** An F9 badge: "no_website" or "closed_permanently"; "" is any. */
  badge: string;
  min_score: string;
  q: string;
  /** Days, as the option's value; "" is "Any time". */
  within: string;
  include_weak: boolean;
  sort: QueueSortKey;
  sort_dir: QueueSortDir;
};

/** The data-quality filter's choices; each matches a badge on the row. */
export const BADGE_OPTIONS: readonly { value: string; label: string }[] = [
  { value: "", label: "Any listing" },
  { value: "no_website", label: "No website" },
  { value: "closed_permanently", label: "Permanently closed" },
];

export const EMPTY_FILTERS: QueueFilterState = {
  status: "pending",
  service: "",
  city: "",
  state: "",
  industry: "",
  badge: "",
  min_score: "",
  q: "",
  within: "",
  include_weak: false,
  sort: "score",
  sort_dir: "desc",
};

/** Whether anything but the status tab is narrowing the queue, which changes what an
 *  empty result means: widen the filters, rather than run a search. */
export function isFiltered(value: QueueFilterState): boolean {
  return Boolean(
    value.service ||
      value.city.trim() ||
      value.state.trim() ||
      value.industry ||
      value.badge ||
      value.min_score ||
      value.q.trim() ||
      value.within,
  );
}

export function QueueFilters({
  value,
  onChange,
}: {
  value: QueueFilterState;
  onChange: (next: QueueFilterState) => void;
}) {
  // The taxonomy's industries; if the list will not load, the filter offers "Any" only.
  const [industries, setIndustries] = useState<IndustryOption[]>([]);
  useEffect(() => {
    getIndustries().then(setIndustries).catch(() => setIndustries([]));
  }, []);
  function set<K extends keyof QueueFilterState>(key: K, next: QueueFilterState[K]) {
    onChange({ ...value, [key]: next });
  }
  return (
    <Card padded={false} as="div" className="p-3">
      <div className="flex flex-wrap items-end gap-3">
        <Select label="Service" value={value.service} onChange={(event) => set("service", event.target.value)}>
          <option value="">Any service</option>
          {SERVICES.map((service) => (
            <option key={service.key} value={service.key}>
              {service.name}
            </option>
          ))}
        </Select>
        <Input label="City" value={value.city} onChange={(event) => set("city", event.target.value)} placeholder="Austin" />
        <Input
          label="State"
          value={value.state}
          maxLength={2}
          controlClassName="w-16"
          onChange={(event) => set("state", event.target.value)}
          placeholder="TX"
        />
        <Select label="Industry" value={value.industry} onChange={(event) => set("industry", event.target.value)}>
          <option value="">Any industry</option>
          {industries.map((option) => (
            <option key={option.key} value={option.key}>
              {option.label}
            </option>
          ))}
        </Select>
        <Select label="Listing" value={value.badge} onChange={(event) => set("badge", event.target.value)}>
          {BADGE_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </Select>
        <Input
          label="Min score"
          type="number"
          min={0}
          max={1}
          step={0.05}
          controlClassName="w-24"
          value={value.min_score}
          onChange={(event) => set("min_score", event.target.value)}
        />
        <Input
          label="Search"
          type="search"
          value={value.q}
          onChange={(event) => set("q", event.target.value)}
          placeholder="Name or domain"
        />
        {/* "Found within" is about when the business was *first found*, not when a source
            last handed the same record over again — see GET /review-queue's docstring. */}
        <Select
          label="Found within"
          value={value.within}
          onChange={(event) => set("within", event.target.value)}
        >
          {RECENCY_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </Select>
        <Checkbox
          label="Show weak signals"
          className="pb-2"
          checked={value.include_weak}
          onChange={(event) => set("include_weak", event.target.checked)}
        />
      </div>
    </Card>
  );
}
