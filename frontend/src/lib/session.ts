/**
 * In-memory access-token store.
 *
 * Deliberately not localStorage or sessionStorage: a token kept in a module variable is
 * gone the moment the tab is closed and cannot be read by injected script from storage.
 */

let accessToken: string | null = null;

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function getAccessToken(): string | null {
  return accessToken;
}

export function clearAccessToken(): void {
  accessToken = null;
}
