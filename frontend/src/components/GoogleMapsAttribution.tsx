import Image from "next/image";
import { SafeLink } from "@/components/SafeLink";
import { cx } from "@/components/ui";
import { safeHttpUrl } from "@/lib/safe";

export type DataProvider = { provider: string; provider_uri: string | null };

/** The official file from Google's "Google_Maps_Attribution_Assets.zip", unmodified. */
export const GOOGLE_MAPS_LOGO = "/google-maps/GoogleMaps_Logo_Gray.svg";

/**
 * The Google Maps attribution Places data must carry wherever it is shown (spec v0.11.1).
 *
 * Built to Google's "Policies and attributions for Places API" (last updated 2026-09-24),
 * not from memory: the attribution is now "Google Maps", not "Google" or "Powered by
 * Google"; the logo is preferred to text; the non-outlined logo goes on a plain background;
 * it is 16–19 px tall (the file is 18) with 10 px clear space left, right and top and 5 px
 * below; it carries the accessible label "Google Maps"; it sits at the top or bottom of the
 * content, in the same visual container; and it is never hidden. `translate="no"` keeps a
 * browser from translating the name.
 *
 * `providers` are the third-party data providers Places returns in `attributions[]`, which
 * "must be shown with this result". Each is named, linked where Places gave an http(s) link;
 * any other link is dropped and the name shown alone.
 */
export function GoogleMapsAttribution({
  providers = [],
  className,
}: {
  providers?: DataProvider[];
  className?: string;
}) {
  return (
    <div
      className={cx("flex flex-wrap items-center gap-x-2 text-sm text-ink-soft", className)}
      data-testid="google-maps-attribution"
    >
      <span className="inline-flex px-2.5 pb-1.5 pt-2.5" translate="no">
        <Image src={GOOGLE_MAPS_LOGO} width={98} height={18} alt="Google Maps" unoptimized />
      </span>
      {providers.length ? (
        <span data-testid="data-providers">
          Data from{" "}
          {providers.map((item, index) => (
            <span key={`${item.provider}-${index}`}>
              {index ? ", " : ""}
              {safeHttpUrl(item.provider_uri) ? (
                <SafeLink href={item.provider_uri}>
                  {item.provider}
                </SafeLink>
              ) : (
                item.provider
              )}
            </span>
          ))}
        </span>
      ) : null}
    </div>
  );
}

/** Every distinct provider across a page of rows, in first-seen order. */
export function distinctProviders(rows: { data_providers?: DataProvider[] }[]): DataProvider[] {
  const seen = new Map<string, DataProvider>();
  for (const row of rows) {
    for (const item of row.data_providers ?? []) {
      const key = `${item.provider}\u0000${item.provider_uri ?? ""}`;
      if (!seen.has(key)) seen.set(key, item);
    }
  }
  return [...seen.values()];
}
