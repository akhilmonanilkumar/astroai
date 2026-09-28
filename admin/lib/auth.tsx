"use client";

/**
 * Team sign-in. With Supabase configured: email + password, then a TOTP code (enrolled on
 * first sign-in). The backend accepts only tokens that completed MFA (aal2) and belong to
 * a row in `admins`. Without Supabase (dev), sign in as `dev:<email>`.
 */
import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";

import type { Me } from "./types";

type Step = "loading" | "password" | "enroll" | "challenge" | "ready" | "denied";
type ConsoleConfig = { mode: "dev" } | { mode: "supabase"; url: string; key: string };

export interface Enrollment {
  factorId: string;
  qr: string;
  secret: string;
}

interface Auth {
  step: Step;
  mode: "dev" | "supabase" | null;
  me: Me | null;
  error: string | null;
  enrollment: Enrollment | null;
  token: () => Promise<string | null>;
  signIn: (email: string, password: string) => Promise<void>;
  verify: (code: string) => Promise<void>;
  signOut: () => Promise<void>;
  expired: () => void;
}

const AuthContext = createContext<Auth | null>(null);
const DEV_TOKEN = "guruji-admin-dev-token";

function readDevToken(): string | null {
  try {
    return localStorage.getItem(DEV_TOKEN);
  } catch {
    return null;
  }
}

function writeDevToken(value: string | null) {
  try {
    if (value) localStorage.setItem(DEV_TOKEN, value);
    else localStorage.removeItem(DEV_TOKEN);
  } catch {
    /* private mode: the session just won't survive a reload */
  }
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [step, setStep] = useState<Step>("loading");
  const [mode, setMode] = useState<"dev" | "supabase" | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [enrollment, setEnrollment] = useState<Enrollment | null>(null);
  const [factorId, setFactorId] = useState<string | null>(null);
  const supabase = useRef<SupabaseClient | null>(null);

  const token = useCallback(async (): Promise<string | null> => {
    if (supabase.current) {
      const { data } = await supabase.current.auth.getSession();
      return data.session?.access_token ?? null;
    }
    return readDevToken();
  }, []);

  const loadMe = useCallback(async () => {
    const t = await token();
    if (!t) {
      setStep("password");
      return;
    }
    const res = await fetch("/api/me", { headers: { authorization: `Bearer ${t}` } });
    if (res.ok) {
      setMe((await res.json()) as Me);
      setError(null);
      setStep("ready");
    } else if (res.status === 403) {
      setStep("denied");
    } else {
      writeDevToken(null);
      setStep("password");
      if (res.status === 502) setError("The admin API is not reachable. Is the admin role running?");
      else if (res.status !== 401)
        setError(`The admin API failed (HTTP ${res.status}). Check the admin API's logs.`);
    }
  }, [token]);

  /** After the password step: finish MFA, enrolling a TOTP factor the first time. */
  const secondFactor = useCallback(async () => {
    const client = supabase.current;
    if (!client) return loadMe();
    const aal = await client.auth.mfa.getAuthenticatorAssuranceLevel();
    if (aal.data?.currentLevel === "aal2") return loadMe();
    const factors = await client.auth.mfa.listFactors();
    const verified = factors.data?.totp[0];
    if (verified) {
      setFactorId(verified.id);
      setStep("challenge");
      return;
    }
    // A half-finished enrollment blocks a new one: clear it first.
    for (const f of factors.data?.all ?? []) {
      if (f.factor_type === "totp" && f.status === "unverified") {
        await client.auth.mfa.unenroll({ factorId: f.id });
      }
    }
    const enrolled = await client.auth.mfa.enroll({ factorType: "totp" });
    if (enrolled.error || !enrolled.data) {
      setError(enrolled.error?.message ?? "Could not start two-factor setup.");
      setStep("password");
      return;
    }
    setEnrollment({
      factorId: enrolled.data.id,
      qr: enrolled.data.totp.qr_code,
      secret: enrolled.data.totp.secret,
    });
    setFactorId(enrolled.data.id);
    setStep("enroll");
  }, [loadMe]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      let config: ConsoleConfig = { mode: "dev" };
      try {
        config = (await (await fetch("/console-config")).json()) as ConsoleConfig;
      } catch {
        /* fall back to dev */
      }
      if (cancelled) return;
      setMode(config.mode);
      if (config.mode === "supabase") {
        supabase.current = createClient(config.url, config.key);
        const { data } = await supabase.current.auth.getSession();
        if (!data.session) {
          setStep("password");
          return;
        }
        await secondFactor();
      } else {
        await loadMe();
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [loadMe, secondFactor]);

  const signIn = useCallback(
    async (email: string, password: string) => {
      setError(null);
      const client = supabase.current;
      if (!client) {
        writeDevToken(`dev:${email.trim()}`);
        await loadMe();
        return;
      }
      const { error: e } = await client.auth.signInWithPassword({ email, password });
      if (e) {
        setError(e.message);
        return;
      }
      await secondFactor();
    },
    [loadMe, secondFactor],
  );

  const verify = useCallback(
    async (code: string) => {
      const client = supabase.current;
      if (!client || !factorId) return;
      setError(null);
      const { error: e } = await client.auth.mfa.challengeAndVerify({ factorId, code: code.trim() });
      if (e) {
        setError(e.message);
        return;
      }
      setEnrollment(null);
      await loadMe();
    },
    [factorId, loadMe],
  );

  const signOut = useCallback(async () => {
    writeDevToken(null);
    await supabase.current?.auth.signOut();
    setMe(null);
    setEnrollment(null);
    setStep("password");
  }, []);

  const expired = useCallback(() => {
    setMe(null);
    setError("Your session has ended. Please sign in again.");
    setStep("password");
  }, []);

  const value = useMemo(
    () => ({ step, mode, me, error, enrollment, token, signIn, verify, signOut, expired }),
    [step, mode, me, error, enrollment, token, signIn, verify, signOut, expired],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): Auth {
  const auth = useContext(AuthContext);
  if (!auth) throw new Error("useAuth outside AuthProvider");
  return auth;
}
