/**
 * The error strings the UI says in its own voice. Every one names the cause **and** the
 * fix, so a reviewer is never left with "something went wrong" (spec v0.9.0, "Writing").
 * An error the API itself reports is shown in the API's own words instead.
 */

export const API_UNREACHABLE =
  "Couldn't reach the API. Check that the api container is running (`make prod-ps`).";

/** What a page says when one of its own requests failed for an unknown reason. */
export function loadFailed(what: string): string {
  return `Couldn't load ${what}. ${API_UNREACHABLE}`;
}
