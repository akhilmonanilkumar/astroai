import { Badge, type Tone } from "@/components/ui/badge";
import type { Escalation, UserState } from "@/lib/types";
import { SEVERITY, humanize } from "@/lib/utils";

export function PageHeader({
  title,
  description,
  action,
}: {
  title: React.ReactNode;
  description?: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <h1 className="text-xl font-semibold">{title}</h1>
        {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
      </div>
      {action}
    </div>
  );
}

export function ErrorNote({ children }: { children: React.ReactNode }) {
  if (!children) return null;
  return <p className="rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">{children}</p>;
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <p className="px-4 py-8 text-center text-sm text-muted-foreground">{children}</p>;
}

export function SeverityBadge({ severity }: { severity: number }) {
  const s = SEVERITY[severity] ?? SEVERITY[3];
  return <Badge tone={s.tone}>{s.label}</Badge>;
}

const STATUS_TONE: Record<Escalation["status"], Tone> = {
  open: "danger",
  acknowledged: "accent",
  resolved: "ok",
  handed_back: "ok",
};

export function StatusBadge({ status }: { status: Escalation["status"] }) {
  return <Badge tone={STATUS_TONE[status]}>{humanize(status)}</Badge>;
}

const STATE_TONE: Record<UserState, Tone> = {
  new: "muted",
  consented: "muted",
  onboarding: "warn",
  active: "ok",
  escalated: "danger",
  blocked: "muted",
};

export function StateBadge({ state }: { state: UserState }) {
  return <Badge tone={STATE_TONE[state]}>{state}</Badge>;
}

export function WindowBadge({ open }: { open: boolean }) {
  return open ? <Badge tone="ok">can reply</Badge> : <Badge tone="warn">template only</Badge>;
}
