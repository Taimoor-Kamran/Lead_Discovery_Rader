"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { useToast } from "@/components/Toast";
import { EstimatePanel } from "@/components/searches/EstimatePanel";
import { confirmRerun, ranRecently } from "@/components/searches/rerun";
import {
  ApiError,
  createSearchJob,
  estimateSearch,
  getIndustries,
  getSearchJobs,
  getSources,
  runSearchJob,
  type CostEstimate,
  type IndustryOption,
  type SearchJobCreate,
  type SearchJobListItem,
  type SourceRead,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatDateTime } from "@/lib/format";
import { canRunSearches } from "@/lib/roles";

const PLACES = "google_places";

type GeoMode = "place" | "point";

export function Searches() {
  const { show } = useToast();
  const { user } = useAuth();
  const router = useRouter();
  const mayRun = user ? canRunSearches(user.role) : false;
  const [items, setItems] = useState<SearchJobListItem[]>([]);
  const [industries, setIndustries] = useState<IndustryOption[]>([]);
  const [sources, setSources] = useState<SourceRead[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [name, setName] = useState("");
  const [industry, setIndustry] = useState("");
  const [mode, setMode] = useState<GeoMode>("place");
  const [city, setCity] = useState("");
  const [state, setState] = useState("");
  const [lat, setLat] = useState("");
  const [lng, setLng] = useState("");
  const [radius, setRadius] = useState("5000");
  const [maxResults, setMaxResults] = useState("");
  const [estimate, setEstimate] = useState<CostEstimate | null>(null);

  const load = useCallback(async () => {
    try {
      setItems((await getSearchJobs()).items);
      setError(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not load searches");
    }
  }, []);

  useEffect(() => {
    void load();
    if (!mayRun) return;
    getIndustries().then(setIndustries).catch(() => setIndustries([]));
    getSources().then(setSources).catch(() => setSources([]));
  }, [load, mayRun]);

  const placesSource = sources.find((source) => source.name === PLACES);

  const refreshEstimate = useCallback(async () => {
    if (!mayRun) return;
    try {
      const parsed = maxResults.trim() ? Number(maxResults) : null;
      setEstimate(
        await estimateSearch({
          max_results: parsed && parsed > 0 ? parsed : null,
          source_ids: placesSource ? [placesSource.id] : [],
        }),
      );
    } catch (caught) {
      setEstimate(null);
      setError(caught instanceof ApiError ? caught.message : "Could not estimate the cost");
    }
  }, [mayRun, maxResults, placesSource]);

  useEffect(() => {
    void refreshEstimate();
  }, [refreshEstimate]);

  function body(): SearchJobCreate | null {
    const chosen = industries.find((option) => option.key === industry);
    if (!chosen) {
      setError("Pick an industry.");
      return null;
    }
    const geo =
      mode === "place"
        ? { city: city.trim(), state: state.trim() }
        : { lat: Number(lat), lng: Number(lng), radius_m: Number(radius) };
    if (mode === "place" && (!geo.city || !geo.state)) {
      setError("Give a city and a state.");
      return null;
    }
    if (mode === "point" && (!lat.trim() || !lng.trim() || !radius.trim())) {
      setError("Give a latitude, a longitude and a radius.");
      return null;
    }
    const parsed = maxResults.trim() ? Number(maxResults) : null;
    return {
      name: name.trim() || `${chosen.label} in ${mode === "place" ? `${city.trim()}, ${state.trim()}` : `${lat}, ${lng}`}`,
      industry: chosen.query,
      geo,
      source_ids: placesSource ? [placesSource.id] : [],
      status: "active",
      max_results: parsed && parsed > 0 ? parsed : null,
    };
  }

  async function save(andRun: boolean) {
    const payload = body();
    if (!payload) return;
    setBusy(true);
    try {
      const job = await createSearchJob(payload);
      if (andRun) {
        await runSearchJob(job.id);
        show({ tone: "success", message: `Search "${job.name}" is running.` });
        router.push(`/searches/${job.id}`);
        return;
      }
      show({ tone: "success", message: `Search "${job.name}" saved.` });
      setName("");
      await load();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not save the search");
    } finally {
      setBusy(false);
    }
  }

  async function rerun(item: SearchJobListItem) {
    if (ranRecently(item.last_run) && !confirmRerun()) return;
    setBusy(true);
    try {
      await runSearchJob(item.id);
      show({ tone: "success", message: `"${item.name}" is running again.` });
      router.push(`/searches/${item.id}`);
    } catch (caught) {
      show({ tone: "error", message: caught instanceof ApiError ? caught.message : "Could not run the search" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <header>
        <h1 className="text-2xl font-semibold text-navy">Searches</h1>
        <p className="text-sm text-slate-600">
          A search asks Google Places for businesses of one industry in one area, then resolves,
          audits and classifies them. See the cost before you run.
        </p>
      </header>

      {mayRun ? (
        <form
          aria-label="New search"
          onSubmit={(event) => {
            event.preventDefault();
            void save(false);
          }}
          className="flex flex-col gap-3 rounded-lg border border-slate-200 bg-white p-3"
        >
          <div className="flex flex-wrap items-end gap-3">
            <label className="flex flex-col gap-1 text-xs text-slate-600">
              Industry
              <select className="field" aria-label="Industry" value={industry} onChange={(event) => setIndustry(event.target.value)} required>
                <option value="">Choose…</option>
                {industries.map((option) => (
                  <option key={option.key} value={option.key}>{option.label}</option>
                ))}
              </select>
            </label>
            <fieldset className="flex items-center gap-3 text-sm">
              <legend className="text-xs text-slate-600">Area</legend>
              <label className="flex items-center gap-1"><input type="radio" name="geo-mode" checked={mode === "place"} onChange={() => setMode("place")} /> City + state</label>
              <label className="flex items-center gap-1"><input type="radio" name="geo-mode" checked={mode === "point"} onChange={() => setMode("point")} /> Point + radius</label>
            </fieldset>
            {mode === "place" ? (
              <>
                <label className="flex flex-col gap-1 text-xs text-slate-600">City<input className="field" value={city} onChange={(event) => setCity(event.target.value)} /></label>
                <label className="flex flex-col gap-1 text-xs text-slate-600">State<input className="field w-24" value={state} onChange={(event) => setState(event.target.value)} /></label>
              </>
            ) : (
              <>
                <label className="flex flex-col gap-1 text-xs text-slate-600">Latitude<input className="field w-28" value={lat} onChange={(event) => setLat(event.target.value)} /></label>
                <label className="flex flex-col gap-1 text-xs text-slate-600">Longitude<input className="field w-28" value={lng} onChange={(event) => setLng(event.target.value)} /></label>
                <label className="flex flex-col gap-1 text-xs text-slate-600">Radius (m)<input className="field w-24" value={radius} onChange={(event) => setRadius(event.target.value)} /></label>
              </>
            )}
            <label className="flex flex-col gap-1 text-xs text-slate-600">
              Max results
              <input className="field w-24" type="number" min={1} max={estimate?.max_results && !maxResults ? undefined : undefined} value={maxResults} placeholder={estimate ? String(estimate.max_results) : ""} onChange={(event) => setMaxResults(event.target.value)} onBlur={() => void refreshEstimate()} />
            </label>
            <label className="flex flex-col gap-1 text-xs text-slate-600">
              Name (optional)
              <input className="field w-64" value={name} onChange={(event) => setName(event.target.value)} />
            </label>
            <span className="text-xs text-slate-600">Source: Google Places{placesSource ? "" : " (not registered: run make sync-sources)"}</span>
          </div>
          <EstimatePanel estimate={estimate} />
          <div className="flex gap-2">
            <button type="submit" className="btn-secondary" disabled={busy}>Save</button>
            <button type="button" className="btn-primary" disabled={busy || !estimate || !estimate.can_run} title={estimate && !estimate.can_run ? estimate.blockers[0] : undefined} onClick={() => void save(true)}>
              Save and run
            </button>
          </div>
        </form>
      ) : (
        <p className="text-sm text-slate-600">Your role can follow searches but not create or run them.</p>
      )}
      {error ? <p role="alert" className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</p> : null}

      <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-600">
            <tr>
              <th scope="col" className="px-3 py-2">Search</th>
              <th scope="col" className="px-3 py-2">Industry</th>
              <th scope="col" className="px-3 py-2">Area</th>
              <th scope="col" className="px-3 py-2">Max</th>
              <th scope="col" className="px-3 py-2">Last run</th>
              <th scope="col" className="px-3 py-2" />
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {items.map((item) => (
              <tr key={item.id} data-testid="search-row">
                <td className="px-3 py-2"><Link href={`/searches/${item.id}`} className="text-teal-700 underline">{item.name}</Link></td>
                <td className="px-3 py-2">{item.industry}</td>
                <td className="px-3 py-2 text-xs">{describeGeo(item.geo)}</td>
                <td className="px-3 py-2">{item.max_results ?? "default"}</td>
                <td className="px-3 py-2 text-xs" data-testid="last-run">
                  {item.last_run ? `${item.last_run.status} · ${formatDateTime(item.last_run.finished_at ?? item.last_run.created_at)}` : "never"}
                </td>
                <td className="px-3 py-2 text-right">
                  {mayRun ? (
                    <button type="button" className="btn-secondary !py-0.5" disabled={busy} onClick={() => void rerun(item)}>
                      {item.last_run ? "Run again" : "Run"}
                    </button>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!items.length ? <p className="p-8 text-center text-sm text-slate-600">No searches yet.</p> : null}
      </div>
    </div>
  );
}

export function describeGeo(geo: Record<string, unknown>): string {
  if (typeof geo.city === "string" && typeof geo.state === "string") return `${geo.city}, ${geo.state}`;
  if (geo.lat !== undefined && geo.lng !== undefined) return `${geo.lat}, ${geo.lng} ± ${geo.radius_m ?? "?"} m`;
  return "unknown";
}
