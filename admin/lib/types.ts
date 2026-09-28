/** Shapes returned by the backend admin API (backend/src/guruji/admin/app.py). */

export type Role = "owner" | "agent";
export type UserState = "new" | "consented" | "onboarding" | "active" | "escalated" | "blocked" | "opted_out";

export interface Me {
  id: string;
  email: string;
  role: Role;
}

export interface Escalation {
  id: string;
  user_id: string;
  category: string;
  severity: number;
  status: "open" | "acknowledged" | "resolved" | "handed_back";
  alert_count: number;
  opened_at: string;
  acknowledged_at: string | null;
  resolved_at: string | null;
  notes: string | null;
  user_state: UserState;
  language: string | null;
  last_inbound_at: string | null;
  window_open: boolean;
}

export interface UserRow {
  id: string;
  state: UserState;
  language: string | null;
  created_at: string;
  last_inbound_at: string | null;
  balance: number;
  onboarded: boolean;
  window_open: boolean;
}

export interface Message {
  id: number;
  direction: "in" | "out";
  sent_by: "user" | "guru" | "human";
  kind: string;
  body: string | null;
  created_at: string;
  meta: Record<string, unknown> | null;
}

export interface UserRecord {
  user: UserRow;
  birth: { on_file: boolean; name_on_file: boolean; time_known: boolean; tz_name: string } | null;
  consents: {
    notice_version: string;
    purpose: string;
    granted: boolean;
    age_confirmed: boolean;
    wamid: string;
    given_at: string;
  }[];
  escalations: Escalation[];
  ledger: { delta: number; reason: string; created_at: string; ref: Record<string, unknown> | null }[];
  facts: { category: string; fact: string; created_at: string }[];
  readings: { topic: string; summary: string; factors: string[]; created_at: string }[];
}

export interface Revealed {
  phone: string | null;
  name: string | null;
  birth_date: string | null;
  birth_time: string | null;
  place: string | null;
  latitude: string | null;
  longitude: string | null;
}

export interface DayStats {
  day: string;
  new_users: number;
  onboarded: number;
  messages_in: number;
  messages_out: number;
  escalations: number;
}

export interface Metrics {
  users_by_state: Record<string, number>;
  funnel: { started: number; consented: number; onboarded: number; paid: number };
  days: DayStats[];
  open_escalations: Record<string, number>;
  median_ack_minutes: number | null;
  ads: { source_id: string; users: number; onboarded: number; paid: number }[];
}

export interface ConfigEntry {
  key: string;
  value: unknown;
  updated_at: string | null;
  updated_by: string | null;
}

export interface Feedback {
  id: number;
  user_id: string;
  turn_id: string;
  rating: "up" | "down";
  created_at: string;
  language: string | null;
  question: string | null;
  answer: string | null;
}

export interface PaymentIssue {
  id: number;
  kind: "dispute" | "duplicate" | "partial_refund";
  reference_id: string | null;
  payment_id: string;
  amount_paise: number | null;
  details: Record<string, unknown>;
  created_at: string;
  resolved_at: string | null;
  resolved_by: string | null;
  user_id: string | null;
}

export interface AuditEntry {
  actor: string;
  action: string;
  subject_user_id: string | null;
  detail: Record<string, unknown> | null;
  created_at: string;
}
