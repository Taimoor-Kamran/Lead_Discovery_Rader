/**
 * Thin client for the Radar API.
 *
 * The access token is held in memory only (see `session.ts`); the refresh token lives in
 * an httpOnly cookie the browser sends back to `/auth/refresh` on its own.
 */

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

export type Health = {
  status: "ok" | "degraded";
  db: boolean;
  redis: boolean;
};

export type Me = {
  id: string;
  email: string;
  role: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
};

export type ErrorEnvelope = {
  error: {
    code: string;
    message: string;
    request_id: string;
    details: Record<string, unknown>;
  };
};

export class ApiError extends Error {
  readonly code: string;
  readonly requestId: string;
  readonly status: number;

  constructor(status: number, code: string, message: string, requestId: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init.headers ?? {}),
    },
  });

  if (!response.ok) {
    throw await toApiError(response);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

async function toApiError(response: Response): Promise<ApiError> {
  try {
    const body = (await response.json()) as Partial<ErrorEnvelope>;
    const error = body.error;
    if (error) {
      return new ApiError(response.status, error.code, error.message, error.request_id);
    }
  } catch {
    // Fall through to the generic message below.
  }
  return new ApiError(response.status, "http_error", response.statusText, "");
}

export function getHealth(): Promise<Health> {
  return request<Health>("/health");
}

export function login(email: string, password: string): Promise<{ access_token: string }> {
  return request<{ access_token: string }>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export function getMe(accessToken: string): Promise<Me> {
  return request<Me>("/auth/me", {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
}

export function logout(): Promise<void> {
  return request<void>("/auth/logout", { method: "POST" });
}
