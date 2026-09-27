"use client";

import Link from "next/link";

import { Empty, ErrorNote, PageHeader } from "@/components/page";
import { Card } from "@/components/ui/card";
import { Table, Td, Th } from "@/components/ui/table";
import { useResource } from "@/lib/api";
import type { AuditEntry } from "@/lib/types";
import { humanize, shortId, when } from "@/lib/utils";

export default function AuditPage() {
  const { data, error } = useResource<AuditEntry[]>("/audit?limit=300");
  return (
    <>
      <PageHeader
        title="Audit log"
        description="Who viewed or changed what. Append-only; repeat views within 30 minutes are counted once."
      />
      <ErrorNote>{error}</ErrorNote>
      <Card>
        {data && data.length === 0 ? (
          <Empty>Nothing yet.</Empty>
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>When</Th>
                <Th>Who</Th>
                <Th>Action</Th>
                <Th>User</Th>
                <Th>Detail</Th>
              </tr>
            </thead>
            <tbody>
              {(data ?? []).map((a, i) => (
                <tr key={i}>
                  <Td className="whitespace-nowrap">{when(a.created_at)}</Td>
                  <Td className="font-mono text-xs">{a.actor}</Td>
                  <Td>{humanize(a.action)}</Td>
                  <Td>
                    {a.subject_user_id ? (
                      <Link
                        href={`/users/${a.subject_user_id}`}
                        className="font-mono text-xs text-accent hover:underline"
                      >
                        {shortId(a.subject_user_id)}
                      </Link>
                    ) : (
                      "—"
                    )}
                  </Td>
                  <Td className="max-w-md font-mono text-xs break-all text-muted-foreground">
                    {a.detail ? JSON.stringify(a.detail) : ""}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </>
  );
}
