"use client";

import Link from "next/link";
import { useState } from "react";

import { Empty, ErrorNote, PageHeader } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody } from "@/components/ui/card";
import { Select } from "@/components/ui/input";
import { errorText, useApi, useResource } from "@/lib/api";
import type { PaymentIssue } from "@/lib/types";
import { shortId, when } from "@/lib/utils";

const KIND: Record<PaymentIssue["kind"], string> = {
  dispute: "Chargeback dispute",
  duplicate: "Paid twice",
  partial_refund: "Partial refund",
};

const WHAT_TO_DO: Record<PaymentIssue["kind"], string> = {
  dispute:
    "Answer the dispute in the Razorpay dashboard. If it is lost, the unused credits are taken back automatically.",
  duplicate:
    "Check the payment in the Razorpay dashboard and refund the extra one there. The user keeps what the first payment bought.",
  partial_refund:
    "Credits were not changed for a partial refund. Adjust them by hand on the user's page if needed.",
};

function rupees(paise: number | null): string {
  return paise === null ? "—" : `₹${(paise / 100).toFixed(2)}`;
}

export default function PaymentsPage() {
  const api = useApi();
  const [status, setStatus] = useState<"open" | "all">("open");
  const { data, error, reload } = useResource<PaymentIssue[]>(`/payment-issues?status=${status}`);
  const [busy, setBusy] = useState<number | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  async function settle(id: number) {
    setBusy(id);
    try {
      await api(`/payment-issues/${id}/resolve`, { method: "POST" });
      setActionError(null);
      reload();
    } catch (e) {
      setActionError(errorText(e));
    } finally {
      setBusy(null);
    }
  }

  const rows = data ?? [];
  return (
    <>
      <PageHeader
        title="Payments"
        description="Disputes, double payments and partial refunds: things a person settles in the Razorpay dashboard. Full refunds need nothing here; unused credits are taken back automatically."
      />
      <div className="mb-4 flex gap-2">
        <Select
          className="max-w-[12rem]"
          value={status}
          onChange={(e) => setStatus(e.target.value as "open" | "all")}
        >
          <option value="open">Open</option>
          <option value="all">All</option>
        </Select>
      </div>
      <ErrorNote>{error ?? actionError}</ErrorNote>
      {rows.length === 0 ? (
        <Card>
          <Empty>{status === "open" ? "Nothing to settle." : "No payment issues yet."}</Empty>
        </Card>
      ) : (
        <div className="space-y-3">
          {rows.map((i) => (
            <Card key={i.id}>
              <CardBody className="space-y-2">
                <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                  <Badge tone={i.kind === "dispute" ? "danger" : "accent"}>{KIND[i.kind]}</Badge>
                  <span>{when(i.created_at)}</span>
                  <span>·</span>
                  <span>{rupees(i.amount_paise)}</span>
                  {i.user_id && (
                    <>
                      <span>·</span>
                      <Link
                        href={`/users/${i.user_id}`}
                        className="font-mono text-accent hover:underline"
                      >
                        {shortId(i.user_id)}
                      </Link>
                    </>
                  )}
                </div>
                <p className="font-mono text-xs">
                  order {i.reference_id ?? "unknown"} · payment {i.payment_id}
                  {typeof i.details.dispute_id === "string" && ` · dispute ${i.details.dispute_id}`}
                  {typeof i.details.status === "string" && ` · ${i.details.status}`}
                </p>
                <p className="text-sm">{WHAT_TO_DO[i.kind]}</p>
                {i.resolved_at ? (
                  <p className="text-xs text-muted-foreground">
                    Settled {when(i.resolved_at)} by {i.resolved_by}
                  </p>
                ) : (
                  <Button
                    variant="outline"
                    disabled={busy === i.id}
                    onClick={() => void settle(i.id)}
                  >
                    Mark settled
                  </Button>
                )}
              </CardBody>
            </Card>
          ))}
        </div>
      )}
    </>
  );
}
