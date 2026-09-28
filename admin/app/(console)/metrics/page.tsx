"use client";

import { useState } from "react";

import { Empty, ErrorNote, PageHeader } from "@/components/page";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Select } from "@/components/ui/input";
import { Table, Td, Th } from "@/components/ui/table";
import { useResource } from "@/lib/api";
import type { DayStats, Metrics } from "@/lib/types";
import { humanize } from "@/lib/utils";

function pct(part: number, whole: number): string {
  return whole ? `${Math.round((part / whole) * 100)}%` : "—";
}

function Stat({ label, value, sub }: { label: string; value: React.ReactNode; sub?: string }) {
  return (
    <Card className="p-4">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
      {sub && <p className="mt-0.5 text-xs text-muted-foreground">{sub}</p>}
    </Card>
  );
}

/** One measure per day: thin single-hue columns, value on hover, peak labelled. */
function Daily({
  days,
  field,
  title,
}: {
  days: DayStats[];
  field: keyof Omit<DayStats, "day">;
  title: string;
}) {
  const values = days.map((d) => d[field]);
  const max = Math.max(1, ...values);
  const total = values.reduce((a, b) => a + b, 0);
  return (
    <Card>
      <CardHeader
        title={title}
        description={`${total} in ${days.length} days · peak ${Math.max(0, ...values)}`}
      />
      <CardBody>
        <div className="flex h-28 items-end gap-[2px]" role="img" aria-label={`${title} per day`}>
          {days.map((d) => (
            <div
              key={d.day}
              className="group relative flex h-full flex-1 items-end"
              title={`${d.day}: ${d[field]}`}
            >
              <div
                className="w-full rounded-t-[4px] bg-accent/80 group-hover:bg-accent"
                style={{ height: d[field] ? `${Math.max(3, (d[field] / max) * 100)}%` : "1px" }}
              />
            </div>
          ))}
        </div>
        <div className="mt-1 flex justify-between text-[11px] text-muted-foreground">
          <span>{days[0]?.day.slice(5)}</span>
          <span>{days.at(-1)?.day.slice(5)}</span>
        </div>
      </CardBody>
    </Card>
  );
}

export default function MetricsPage() {
  const [days, setDays] = useState(14);
  const { data, error } = useResource<Metrics>(`/metrics?days=${days}`, 60000);
  const m = data;
  const f = m?.funnel;
  const openTotal = m ? Object.values(m.open_escalations).reduce((a, b) => a + b, 0) : 0;

  return (
    <>
      <PageHeader
        title="Metrics"
        description="Days are in IST. Paid means a pack or pass purchase (payments arrive in M7)."
        action={
          <Select className="w-36" value={days} onChange={(e) => setDays(Number(e.target.value))}>
            {[7, 14, 30, 90].map((d) => (
              <option key={d} value={d}>
                Last {d} days
              </option>
            ))}
          </Select>
        }
      />
      <ErrorNote>{error}</ErrorNote>
      {m && f && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
            <Stat label="Users" value={f.started} />
            <Stat label="Onboarded" value={f.onboarded} sub={`${pct(f.onboarded, f.started)} of users`} />
            <Stat label="Paid" value={f.paid} sub={`${pct(f.paid, f.onboarded)} of onboarded`} />
            <Stat label="Open escalations" value={openTotal} />
            <Stat
              label="Median time to acknowledge"
              value={m.median_ack_minutes === null ? "—" : `${Math.round(m.median_ack_minutes)} min`}
              sub={`last ${days} days`}
            />
          </div>

          <Card>
            <CardHeader
              title="Funnel"
              description="All time: started → agreed to the notice → chart cast → paid."
            />
            <CardBody className="space-y-2">
              {(["started", "consented", "onboarded", "paid"] as const).map((k) => (
                <div key={k} className="grid grid-cols-[6rem_1fr_5rem] items-center gap-3 text-sm">
                  <span className="text-muted-foreground">{k}</span>
                  <div className="h-3 rounded-r-[4px] bg-muted">
                    <div
                      className="h-3 rounded-r-[4px] bg-accent"
                      style={{ width: `${f.started ? (f[k] / f.started) * 100 : 0}%` }}
                      title={`${k}: ${f[k]}`}
                    />
                  </div>
                  <span className="text-right tabular-nums">
                    {f[k]} <span className="text-xs text-muted-foreground">{pct(f[k], f.started)}</span>
                  </span>
                </div>
              ))}
            </CardBody>
          </Card>

          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <Daily days={m.days} field="new_users" title="New users" />
            <Daily days={m.days} field="onboarded" title="Onboarded" />
            <Daily days={m.days} field="messages_in" title="Messages from users" />
            <Daily days={m.days} field="escalations" title="Escalations opened" />
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader
                title="Ads (Click-to-WhatsApp)"
                description="Users by the ad that brought them, top 20."
              />
              {m.ads.length === 0 ? (
                <Empty>No ad referrals yet.</Empty>
              ) : (
                <Table>
                  <thead>
                    <tr>
                      <Th>Ad</Th>
                      <Th className="text-right">Users</Th>
                      <Th className="text-right">Onboarded</Th>
                      <Th className="text-right">Paid</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {m.ads.map((a) => (
                      <tr key={a.source_id}>
                        <Td className="font-mono text-xs">{a.source_id}</Td>
                        <Td className="text-right tabular-nums">{a.users}</Td>
                        <Td className="text-right tabular-nums">
                          {a.onboarded}{" "}
                          <span className="text-xs text-muted-foreground">{pct(a.onboarded, a.users)}</span>
                        </Td>
                        <Td className="text-right tabular-nums">{a.paid}</Td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              )}
            </Card>
            <Card>
              <CardHeader title="Now" description="Users by state, and open escalations by category." />
              <CardBody className="grid grid-cols-2 gap-6 text-sm">
                <dl className="space-y-1">
                  {Object.entries(m.users_by_state).map(([k, v]) => (
                    <div key={k} className="flex justify-between">
                      <dt className="text-muted-foreground">{k}</dt>
                      <dd className="tabular-nums">{v}</dd>
                    </div>
                  ))}
                </dl>
                <dl className="space-y-1">
                  {openTotal === 0 && <p className="text-muted-foreground">No open escalations.</p>}
                  {Object.entries(m.open_escalations).map(([k, v]) => (
                    <div key={k} className="flex justify-between">
                      <dt className="text-muted-foreground">{humanize(k)}</dt>
                      <dd className="tabular-nums">{v}</dd>
                    </div>
                  ))}
                </dl>
              </CardBody>
            </Card>
          </div>

          <Card>
            <CardHeader title="By day" />
            <Table>
              <thead>
                <tr>
                  <Th>Day</Th>
                  <Th className="text-right">New users</Th>
                  <Th className="text-right">Onboarded</Th>
                  <Th className="text-right">Messages in</Th>
                  <Th className="text-right">Messages out</Th>
                  <Th className="text-right">Escalations</Th>
                </tr>
              </thead>
              <tbody>
                {[...m.days].reverse().map((d) => (
                  <tr key={d.day}>
                    <Td>{d.day}</Td>
                    <Td className="text-right tabular-nums">{d.new_users}</Td>
                    <Td className="text-right tabular-nums">{d.onboarded}</Td>
                    <Td className="text-right tabular-nums">{d.messages_in}</Td>
                    <Td className="text-right tabular-nums">{d.messages_out}</Td>
                    <Td className="text-right tabular-nums">{d.escalations}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </Card>
        </div>
      )}
    </>
  );
}
