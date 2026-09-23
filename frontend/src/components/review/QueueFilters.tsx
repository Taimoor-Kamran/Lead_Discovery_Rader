"use client";

import { Card, Checkbox, Input, Select } from "@/components/ui";
import { RECENCY_OPTIONS, SERVICES } from "@/lib/api";

export type QueueFilterState = {
  status: "pending" | "needs_enrichment";
  service: string;
  city: string;
  min_score: string;
  q: string;
  /** Days, as the option's value; "" is "Any time". */
  within: string;
  include_weak: boolean;
};

export const EMPTY_FILTERS: QueueFilterState = {
  status: "pending",
  service: "",
  city: "",
  min_score: "",
  q: "",
  within: "",
  include_weak: false,
};

/** Whether anything but the status tab is narrowing the queue, which changes what an
 *  empty result means: widen the filters, rather than run a search. */
export function isFiltered(value: QueueFilterState): boolean {
  return Boolean(
    value.service || value.city.trim() || value.min_score || value.q.trim() || value.within,
  );
}

export function QueueFilters({
  value,
  onChange,
}: {
  value: QueueFilterState;
  onChange: (next: QueueFilterState) => void;
}) {
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
