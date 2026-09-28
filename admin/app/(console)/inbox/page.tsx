"use client";

import Link from "next/link";
import { useState } from "react";

import { Empty, ErrorNote, PageHeader, SeverityBadge, StatusBadge, WindowBadge } from "@/components/page";
import { Card } from "@/components/ui/card";
import { Table, Td, Th } from "@/components/ui/table";
import { useResource } from "@/lib/api";
import type { Escalation } from "@/lib/types";
import { ago, cn, humanize, shortId, when } from "@/lib/utils";

export default function InboxPage() {
  const [tab, setTab] = useState<"active" | "closed">("active");
  const { data, error } = useResource<Escalation[]>(`/escalations?status=${tab}`, 10000);

  return (
    <>
      <PageHeader
        title="Inbox"
        description="Escalated chats. Guruji stays out of these until a case is closed or handed back."
      />
      <div className="mb-4 flex gap-1">
        {(["active", "closed"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={cn(
              "rounded-md px-3 py-1.5 text-sm capitalize",
              tab === t ? "bg-foreground text-background" : "text-muted-foreground hover:bg-muted",
            )}
          >
            {t}
          </button>
        ))}
      </div>
      <ErrorNote>{error}</ErrorNote>
      <Card>
        {data && data.length === 0 ? (
          <Empty>{tab === "active" ? "Nothing waiting. 🙏" : "No closed cases yet."}</Empty>
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Severity</Th>
                <Th>Category</Th>
                <Th>Status</Th>
                <Th>User</Th>
                <Th>Opened</Th>
                <Th>{tab === "active" ? "User last wrote" : "Closed"}</Th>
                {tab === "active" && <Th>Reply</Th>}
              </tr>
            </thead>
            <tbody>
              {(data ?? []).map((e) => (
                <tr key={e.id} className="hover:bg-muted/60">
                  <Td>
                    <SeverityBadge severity={e.severity} />
                  </Td>
                  <Td>
                    <Link href={`/escalations/${e.id}`} className="font-medium hover:underline">
                      {humanize(e.category)}
                    </Link>
                    {e.alert_count > 1 && (
                      <span className="ml-2 text-xs text-muted-foreground">alerted {e.alert_count}×</span>
                    )}
                  </Td>
                  <Td>
                    <StatusBadge status={e.status} />
                  </Td>
                  <Td className="font-mono text-xs">{shortId(e.user_id)}</Td>
                  <Td title={when(e.opened_at)}>{ago(e.opened_at)}</Td>
                  <Td>{tab === "active" ? ago(e.last_inbound_at) : when(e.resolved_at)}</Td>
                  {tab === "active" && (
                    <Td>
                      <WindowBadge open={e.window_open} />
                    </Td>
                  )}
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </>
  );
}
