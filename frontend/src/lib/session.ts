/**
 * In-memory session store.
 *
 * Deliberately not localStorage or sessionStorage: a token kept in a module variable is
 * gone the moment the tab is closed and cannot be read by injected script from storage.
 * The refresh token lives in an httpOnly cookie the browser sends to `/auth/refresh` on
 * its own, which is how a reload gets a new access token without ever storing one.
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
