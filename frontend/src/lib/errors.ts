/**
 * The error strings the UI says in its own voice. Every one names the cause **and** the
 * fix, so a reviewer is never left with "something went wrong" (spec v0.9.0, "Writing").
 * An error the API itself reports is shown in the API's own words instead.
 */

/**
 * Which compose stack this build belongs to. `NODE_ENV` cannot answer it: both stacks run
 * the same production Next image, so it reads "production" after a plain `make up` too.
 * `NEXT_PUBLIC_ENVIRONMENT` is the project's own `ENVIRONMENT`, passed as a build arg and
 * inlined by Next — so the string is fixed when the image is built, not guessed at runtime.
 */
const PROD_STACK = ["production", "staging"];

/** The compose target that lists the containers, for the stack this build talks to. */
export function psCommand(): string {
  const environment = (process.env.NEXT_PUBLIC_ENVIRONMENT ?? "").trim().toLowerCase();
  return PROD_STACK.includes(environment) ? "make prod-ps" : "make ps";
}

export function apiUnreachable(): string {
  return `Couldn't reach the API. Check that the api container is running (\`${psCommand()}\`).`;
}

/** What a page says when one of its own requests failed for an unknown reason. */
export function loadFailed(what: string): string {
  return `Couldn't load ${what}. ${apiUnreachable()}`;
}
