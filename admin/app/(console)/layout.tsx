"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";

import { Badge } from "@/components/ui/badge";
import { useResource } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { Escalation } from "@/lib/types";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/inbox", label: "Inbox" },
  { href: "/users", label: "Users" },
  { href: "/feedback", label: "Feedback" },
  { href: "/payments", label: "Payments" },
  { href: "/metrics", label: "Metrics" },
  { href: "/config", label: "Config" },
  { href: "/audit", label: "Audit log", owner: true },
];

export default function ConsoleLayout({ children }: { children: React.ReactNode }) {
  const auth = useAuth();
  const router = useRouter();
  const path = usePathname();
  const ready = auth.step === "ready";
  const { data: active } = useResource<Escalation[]>(ready ? "/escalations" : null, 15000);

  useEffect(() => {
    if (auth.step !== "ready" && auth.step !== "loading") router.replace("/login");
  }, [auth.step, router]);

  if (!ready || !auth.me) {
    return <p className="p-6 text-sm text-muted-foreground">Loading…</p>;
  }
  const urgent = active?.some((e) => e.severity === 1 && e.status === "open");

  return (
    <div className="min-h-screen md:flex">
      <aside className="border-b border-border bg-surface md:w-56 md:shrink-0 md:border-r md:border-b-0">
        <div className="flex items-center justify-between px-4 py-3 md:block">
          <p className="font-semibold">Guruji Console</p>
          <p className="text-xs text-muted-foreground md:mt-1">
            {auth.me.email} · {auth.me.role}
          </p>
        </div>
        <nav className="flex gap-1 overflow-x-auto px-2 pb-2 md:flex-col md:pb-4">
          {NAV.filter((n) => !n.owner || auth.me?.role === "owner").map((n) => (
            <Link
              key={n.href}
              href={n.href}
              className={cn(
                "flex items-center justify-between gap-2 rounded-md px-3 py-2 text-sm whitespace-nowrap",
                path.startsWith(n.href) || (n.href === "/inbox" && path.startsWith("/escalations"))
                  ? "bg-muted font-medium"
                  : "text-muted-foreground hover:bg-muted",
              )}
            >
              {n.label}
              {n.href === "/inbox" && !!active?.length && (
                <Badge tone={urgent ? "danger" : "accent"}>{active.length}</Badge>
              )}
            </Link>
          ))}
          <button
            onClick={() => void auth.signOut()}
            className="rounded-md px-3 py-2 text-left text-sm whitespace-nowrap text-muted-foreground hover:bg-muted"
          >
            Sign out
          </button>
        </nav>
      </aside>
      <main className="min-w-0 flex-1 px-4 py-6 md:px-8">{children}</main>
    </div>
  );
}
