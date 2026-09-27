export function cn(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

/** Random hex id so a retried send or credit adjustment is applied once. */
export function clientId(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

export function shortId(id: string): string {
  return id.slice(0, 8);
}

const IST = "Asia/Kolkata";

export function when(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-IN", {
    timeZone: IST,
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function ago(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "never";
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (s < 60) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 48) return `${h} h ago`;
  return `${Math.round(h / 24)} d ago`;
}

export const SEVERITY: Record<number, { label: string; tone: "danger" | "warn" | "muted" }> = {
  1: { label: "Urgent", tone: "danger" },
  2: { label: "High", tone: "warn" },
  3: { label: "Normal", tone: "muted" },
};

export function humanize(key: string): string {
  return key.replace(/_/g, " ");
}
