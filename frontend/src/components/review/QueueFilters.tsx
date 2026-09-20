"use client";

import { SERVICES } from "@/lib/api";

export type QueueFilterState = {
  status: "pending" | "needs_enrichment";
  service: string;
  city: string;
  min_score: string;
  q: string;
  include_weak: boolean;
};

export const EMPTY_FILTERS: QueueFilterState = {
  status: "pending",
  service: "",
  city: "",
  min_score: "",
  q: "",
  include_weak: false,
};

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
    <div className="flex flex-col gap-3">
      <div role="tablist" aria-label="Status" className="flex gap-1">
        {(["pending", "needs_enrichment"] as const).map((status) => (
          <button
            key={status}
            role="tab"
            type="button"
            aria-selected={value.status === status}
            onClick={() => set("status", status)}
            className={`rounded-t border-b-2 px-3 py-1.5 text-sm ${
              value.status === status
                ? "border-teal-600 font-medium text-navy"
                : "border-transparent text-slate-600 hover:text-navy"
            }`}
          >
            {status === "pending" ? "Pending" : "Needs enrichment"}
          </button>
        ))}
      </div>
      <div className="flex flex-wrap items-end gap-3 rounded-lg border border-slate-200 bg-white p-3">
        <label className="flex flex-col gap-1 text-xs text-slate-600">
          Service
          <select
            className="field"
            value={value.service}
            onChange={(event) => set("service", event.target.value)}
          >
            <option value="">Any service</option>
            {SERVICES.map((service) => (
              <option key={service.key} value={service.key}>
                {service.name}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-slate-600">
          City
          <input
            className="field"
            value={value.city}
            onChange={(event) => set("city", event.target.value)}
            placeholder="Austin"
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-slate-600">
          Min score
          <input
            className="field w-24"
            type="number"
            min={0}
            max={1}
            step={0.05}
            value={value.min_score}
            onChange={(event) => set("min_score", event.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-slate-600">
          Search
          <input
            className="field"
            type="search"
            value={value.q}
            onChange={(event) => set("q", event.target.value)}
            placeholder="Name or domain"
          />
        </label>
        <label className="flex items-center gap-2 pb-1.5 text-sm">
          <input
            type="checkbox"
            checked={value.include_weak}
            onChange={(event) => set("include_weak", event.target.checked)}
          />
          Show weak signals
        </label>
      </div>
    </div>
  );
}
