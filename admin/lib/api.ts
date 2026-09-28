"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { useAuth } from "./auth";

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(detail);
  }
}

/** Human wording for the API's error codes. */
const MESSAGES: Record<string, string> = {
  window_closed:
    "More than 24 hours since their last message: WhatsApp only allows the approved template now.",
  escalation_closed: "This case is already closed.",
  owner_only: "Only an owner can do this.",
  not_allowed_in_current_state: "Not possible in the user's current state.",
  no_phone_number: "No phone number on file for this user.",
  bad_phone_number: "That doesn't look like a phone number.",
  admin_api_unreachable: "The admin API is not reachable.",
  not_found: "Not found.",
};

export function errorText(e: unknown): string {
  if (e instanceof ApiError) return MESSAGES[e.detail] ?? e.detail;
  return e instanceof Error ? e.message : String(e);
}

export type Api = <T>(path: string, init?: { method?: string; body?: unknown }) => Promise<T>;

export function useApi(): Api {
  const { token, expired } = useAuth();
  return useCallback(
    async <T>(path: string, init: { method?: string; body?: unknown } = {}): Promise<T> => {
      const t = await token();
      const res = await fetch(`/api${path}`, {
        method: init.method ?? "GET",
        headers: {
          ...(t ? { authorization: `Bearer ${t}` } : {}),
          ...(init.body !== undefined ? { "content-type": "application/json" } : {}),
        },
        body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
      });
      const data: unknown = await res.json().catch(() => ({}));
      if (!res.ok) {
        const raw = (data as { detail?: unknown }).detail;
        const detail =
          typeof raw === "string" ? raw : Array.isArray(raw) ? "Please check the form." : res.statusText;
        if (res.status === 401) expired();
        throw new ApiError(res.status, detail);
      }
      return data as T;
    },
    [token, expired],
  );
}

/** GET `path`, re-fetching every `pollMs` (if set) and whenever `reload()` is called. */
export function useResource<T>(path: string | null, pollMs?: number) {
  const api = useApi();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const latest = useRef(path);
  latest.current = path;

  useEffect(() => {
    if (!path) return;
    let alive = true;
    const load = () =>
      api<T>(path)
        .then((d) => {
          if (alive && latest.current === path) {
            setData(d);
            setError(null);
          }
        })
        .catch((e: unknown) => alive && setError(errorText(e)));
    load();
    const timer = pollMs ? setInterval(load, pollMs) : undefined;
    return () => {
      alive = false;
      if (timer) clearInterval(timer);
    };
  }, [api, path, pollMs, tick]);

  const reload = useCallback(() => setTick((n) => n + 1), []);
  return { data, error, reload, setData };
}
