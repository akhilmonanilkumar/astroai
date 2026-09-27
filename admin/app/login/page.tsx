"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { useAuth } from "@/lib/auth";

export default function LoginPage() {
  const auth = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (auth.step === "ready") router.replace("/inbox");
  }, [auth.step, router]);

  async function run(fn: () => Promise<void>) {
    setBusy(true);
    try {
      await fn();
    } finally {
      setBusy(false);
    }
  }

  const dev = auth.mode === "dev";

  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <Card className="w-full max-w-sm p-6">
        <h1 className="text-lg font-semibold">Guruji Console</h1>
        <p className="mt-1 text-sm text-muted-foreground">For the Guruji team only.</p>

        {auth.step === "loading" && <p className="mt-6 text-sm text-muted-foreground">Loading…</p>}

        {auth.step === "password" && (
          <form
            className="mt-6 space-y-4"
            onSubmit={(e) => {
              e.preventDefault();
              void run(() => auth.signIn(email, password));
            }}
          >
            <div>
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                type={dev ? "text" : "email"}
                autoComplete="username"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>
            {dev ? (
              <p className="rounded-md bg-warn-soft px-3 py-2 text-xs text-warn">
                Dev sign-in: no password, and you get the owner role. Add “:agent” after the email to try the
                agent role.
              </p>
            ) : (
              <div>
                <Label htmlFor="password">Password</Label>
                <Input
                  id="password"
                  type="password"
                  autoComplete="current-password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </div>
            )}
            <Button className="w-full" disabled={busy}>
              Sign in
            </Button>
          </form>
        )}

        {(auth.step === "enroll" || auth.step === "challenge") && (
          <form
            className="mt-6 space-y-4"
            onSubmit={(e) => {
              e.preventDefault();
              void run(() => auth.verify(code));
            }}
          >
            {auth.step === "enroll" && auth.enrollment && (
              <div className="space-y-3">
                <p className="text-sm">
                  Two-factor sign-in is required. Scan this with an authenticator app, then enter the 6-digit
                  code.
                </p>
                {/* Supabase returns the QR code as an SVG data URI. */}
                <img
                  src={auth.enrollment.qr}
                  alt="Authenticator QR code"
                  className="mx-auto h-44 w-44 rounded bg-white p-2"
                />
                <p className="break-all text-center font-mono text-xs text-muted-foreground">
                  {auth.enrollment.secret}
                </p>
              </div>
            )}
            {auth.step === "challenge" && (
              <p className="text-sm">Enter the 6-digit code from your authenticator app.</p>
            )}
            <div>
              <Label htmlFor="code">Code</Label>
              <Input
                id="code"
                inputMode="numeric"
                autoComplete="one-time-code"
                pattern="[0-9]{6}"
                required
                value={code}
                onChange={(e) => setCode(e.target.value)}
              />
            </div>
            <Button className="w-full" disabled={busy}>
              Verify
            </Button>
            <Button type="button" variant="ghost" className="w-full" onClick={() => void auth.signOut()}>
              Use another account
            </Button>
          </form>
        )}

        {auth.step === "denied" && (
          <div className="mt-6 space-y-4">
            <p className="text-sm">
              This account is not on the Guruji team. An owner can add you with{" "}
              <code className="font-mono text-xs">python -m guruji add-admin</code>.
            </p>
            <Button variant="outline" className="w-full" onClick={() => void auth.signOut()}>
              Sign out
            </Button>
          </div>
        )}

        {auth.error && <p className="mt-4 text-sm text-danger">{auth.error}</p>}
      </Card>
    </main>
  );
}
