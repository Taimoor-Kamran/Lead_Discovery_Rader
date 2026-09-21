"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { ErrorNote } from "@/components/ErrorNote";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Input,
  PageHeader,
  Select,
  Table,
  TableWrap,
  TBody,
  Td,
  Th,
  THead,
  Tr,
  useToast,
} from "@/components/ui";
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
import { loadFailed } from "@/lib/errors";
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
      setError(caught instanceof ApiError ? caught.message : loadFailed("the searches"));
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
    <div className="flex flex-col gap-5">
      <PageHeader
        title="Searches"
        description="A search asks Google Places for businesses of one industry in one area, then resolves, audits and classifies them. See the cost before you run."
      />

      {mayRun ? (
        <Card
          as="form"
          aria-label="New search"
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            void save(false);
          }}
        >
          <div className="flex flex-wrap items-end gap-4">
            <Select
              label="Industry"
              value={industry}
              onChange={(event) => setIndustry(event.target.value)}
              required
            >
              <option value="">Choose…</option>
              {industries.map((option) => (
                <option key={option.key} value={option.key}>
                  {option.label}
                </option>
              ))}
            </Select>
            <fieldset className="flex flex-col gap-1">
              <legend className="text-sm font-medium text-ink-soft">Area</legend>
              <div className="flex items-center gap-4 py-1.5">
                <label className="flex items-center gap-2 text-base">
                  <input
                    type="radio"
                    name="geo-mode"
                    className="h-4 w-4 border-line accent-accent"
                    checked={mode === "place"}
                    onChange={() => setMode("place")}
                  />
                  City and state
                </label>
                <label className="flex items-center gap-2 text-base">
                  <input
                    type="radio"
                    name="geo-mode"
                    className="h-4 w-4 border-line accent-accent"
                    checked={mode === "point"}
                    onChange={() => setMode("point")}
                  />
                  Point and radius
                </label>
              </div>
            </fieldset>
            {mode === "place" ? (
              <>
                <Input label="City" value={city} onChange={(event) => setCity(event.target.value)} />
                <Input
                  label="State"
                  controlClassName="w-24"
                  value={state}
                  onChange={(event) => setState(event.target.value)}
                />
              </>
            ) : (
              <>
                <Input
                  label="Latitude"
                  controlClassName="w-32"
                  value={lat}
                  onChange={(event) => setLat(event.target.value)}
                />
                <Input
                  label="Longitude"
                  controlClassName="w-32"
                  value={lng}
                  onChange={(event) => setLng(event.target.value)}
                />
                <Input
                  label="Radius (m)"
                  controlClassName="w-28"
                  value={radius}
                  onChange={(event) => setRadius(event.target.value)}
                />
              </>
            )}
            <Input
              label="Max results"
              type="number"
              min={1}
              controlClassName="w-28"
              value={maxResults}
              placeholder={estimate ? String(estimate.max_results) : ""}
              onChange={(event) => setMaxResults(event.target.value)}
              onBlur={() => void refreshEstimate()}
            />
            <Input
              label="Name (optional)"
              controlClassName="w-64"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </div>
          <p className="text-sm text-ink-soft">
            Source: Google Places
            {placesSource ? "" : " — not registered yet, run make sync-sources"}
          </p>
          <EstimatePanel estimate={estimate} />
          <div className="flex gap-2">
            <Button type="submit" disabled={busy}>
              Save
            </Button>
            <Button
              variant="primary"
              disabled={busy || !estimate || !estimate.can_run}
              title={estimate && !estimate.can_run ? estimate.blockers[0] : undefined}
              onClick={() => void save(true)}
            >
              Save and run
            </Button>
          </div>
        </Card>
      ) : (
        <p className="text-base text-ink-soft">
          Your role can follow searches but not create or run them.
        </p>
      )}
      {error ? <ErrorNote>{error}</ErrorNote> : null}

      <TableWrap>
        <Table minWidth="54rem">
          <THead>
            <tr>
              <Th>Search</Th>
              <Th>Industry</Th>
              <Th>Area</Th>
              <Th numeric className="w-20">
                Max
              </Th>
              <Th>Last run</Th>
              <Th aria-label="Actions" />
            </tr>
          </THead>
          <TBody>
            {items.length ? (
              items.map((item) => (
                <Tr key={item.id} data-testid="search-row">
                  <Td>
                    <Link
                      href={`/searches/${item.id}`}
                      className="rounded font-medium text-ink underline-offset-2 hover:text-accent hover:underline"
                    >
                      {item.name}
                    </Link>
                  </Td>
                  <Td>{item.industry}</Td>
                  <Td className="text-sm">{describeGeo(item.geo)}</Td>
                  <Td numeric>{item.max_results ?? "default"}</Td>
                  <Td className="text-sm" data-testid="last-run">
                    {item.last_run ? (
                      <>
                        <Badge tone={item.last_run.status === "done" ? "ok" : "neutral"}>
                          {item.last_run.status}
                        </Badge>
                        <span className="ml-2 text-ink-soft">
                          {formatDateTime(item.last_run.finished_at ?? item.last_run.created_at)}
                        </span>
                      </>
                    ) : (
                      "never"
                    )}
                  </Td>
                  <Td className="text-right">
                    {mayRun ? (
                      <Button size="sm" disabled={busy} onClick={() => void rerun(item)}>
                        {item.last_run ? "Run again" : "Run"}
                      </Button>
                    ) : null}
                  </Td>
                </Tr>
              ))
            ) : (
              <tr>
                <td colSpan={6}>
                  <EmptyState
                    title="No searches yet."
                    description={
                      mayRun
                        ? "Pick an industry and an area above, check the cost, then save and run."
                        : "A search appears here once an admin or a sales rep creates one."
                    }
                  />
                </td>
              </tr>
            )}
          </TBody>
        </Table>
      </TableWrap>
    </div>
  );
}

export function describeGeo(geo: Record<string, unknown>): string {
  if (typeof geo.city === "string" && typeof geo.state === "string") return `${geo.city}, ${geo.state}`;
  if (geo.lat !== undefined && geo.lng !== undefined) return `${geo.lat}, ${geo.lng} ± ${geo.radius_m ?? "?"} m`;
  return "unknown";
}
