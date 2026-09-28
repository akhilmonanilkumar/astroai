"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { Conversation } from "@/components/conversation";
import { ErrorNote, PageHeader, SeverityBadge, StatusBadge, WindowBadge } from "@/components/page";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Label, Textarea } from "@/components/ui/input";
import { errorText, useApi, useResource } from "@/lib/api";
import type { Escalation, Message } from "@/lib/types";
import { ago, clientId, humanize, shortId, when } from "@/lib/utils";

const MAX_CHARS = 3000;

export default function EscalationPage() {
  const { id } = useParams<{ id: string }>();
  const api = useApi();
  const esc = useResource<Escalation>(`/escalations/${id}`, 10000);
  const e = esc.data;
  const thread = useResource<Message[]>(e ? `/users/${e.user_id}/messages?limit=100` : null, 5000);

  const [draft, setDraft] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // One id per message being written, so a retry after a network error is not sent twice.
  const [pendingId, setPendingId] = useState(clientId);

  const active = e?.status === "open" || e?.status === "acknowledged";

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      esc.reload();
      thread.reload();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  const send = () =>
    act(async () => {
      await api(`/escalations/${id}/reply`, {
        method: "POST",
        body: { client_id: pendingId, text: draft.trim() },
      });
      setDraft("");
      setPendingId(clientId());
    });

  const sendTemplate = () =>
    act(async () => {
      if (!confirm("Send the approved follow-up template to this user?")) return;
      await api(`/escalations/${id}/template`, { method: "POST", body: { client_id: clientId() } });
    });

  const close = (handBack: boolean) =>
    act(async () => {
      const what = handBack ? "Hand the chat back to Guruji?" : "Close this case? Guruji resumes too.";
      if (!confirm(what)) return;
      await api(`/escalations/${id}/close`, {
        method: "POST",
        body: { hand_back: handBack, note: note.trim() || null },
      });
    });

  if (!e) {
    return esc.error ? (
      <ErrorNote>{esc.error}</ErrorNote>
    ) : (
      <p className="text-sm text-muted-foreground">Loading…</p>
    );
  }

  return (
    <>
      <PageHeader
        title={
          <span className="flex flex-wrap items-center gap-2">
            {humanize(e.category)} <SeverityBadge severity={e.severity} /> <StatusBadge status={e.status} />
          </span>
        }
        description={
          <>
            Opened {when(e.opened_at)} · user{" "}
            <Link href={`/users/${e.user_id}`} className="font-mono text-accent hover:underline">
              {shortId(e.user_id)}
            </Link>{" "}
            · writes in {e.language ?? "unknown"}
          </>
        }
        action={
          active && e.status === "open" ? (
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => void act(() => api(`/escalations/${id}/ack`, { method: "POST" }))}
            >
              Acknowledge
            </Button>
          ) : null
        }
      />
      {e.severity === 1 && active && (
        <p className="mb-4 rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
          Urgent: the user may be at risk. Guruji has already shared Tele-MANAS 14416 and 112. Please reply as
          soon as you can.
        </p>
      )}
      <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
        <Card className="flex min-h-[420px] flex-col">
          <CardHeader
            title="Conversation"
            description={`User last wrote ${ago(e.last_inbound_at)}`}
            action={active ? <WindowBadge open={e.window_open} /> : undefined}
          />
          <Conversation messages={thread.data ?? []} className="max-h-[60vh] flex-1" />
          {active && (
            <div className="border-t border-border p-4">
              {e.window_open ? (
                <form
                  onSubmit={(ev) => {
                    ev.preventDefault();
                    if (draft.trim()) void send();
                  }}
                  className="space-y-2"
                >
                  <Textarea
                    rows={3}
                    maxLength={MAX_CHARS}
                    placeholder="Write as a person from the team. It is sent labelled “Guruji team (a person)” in their language."
                    value={draft}
                    onChange={(ev) => setDraft(ev.target.value)}
                  />
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-xs text-muted-foreground">
                      {draft.length}/{MAX_CHARS}
                    </span>
                    <Button disabled={busy || !draft.trim()}>Send on WhatsApp</Button>
                  </div>
                </form>
              ) : (
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <p className="text-sm text-muted-foreground">
                    More than 24 hours since they last wrote. WhatsApp only allows the approved follow-up
                    template; you can write freely once they reply.
                  </p>
                  <Button variant="outline" disabled={busy} onClick={() => void sendTemplate()}>
                    Send follow-up template
                  </Button>
                </div>
              )}
            </div>
          )}
        </Card>
        <div className="space-y-4">
          <ErrorNote>{error ?? thread.error}</ErrorNote>
          <Card>
            <CardHeader title={active ? "Close the case" : "Closed"} />
            <CardBody className="space-y-3">
              {active ? (
                <>
                  <div>
                    <Label htmlFor="note">Note for the team (optional)</Label>
                    <Textarea
                      id="note"
                      rows={3}
                      maxLength={2000}
                      value={note}
                      onChange={(ev) => setNote(ev.target.value)}
                    />
                  </div>
                  <Button className="w-full" disabled={busy} onClick={() => void close(true)}>
                    Hand back to Guruji
                  </Button>
                  <Button
                    variant="outline"
                    className="w-full"
                    disabled={busy}
                    onClick={() => void close(false)}
                  >
                    Mark resolved
                  </Button>
                  <p className="text-xs text-muted-foreground">
                    Either way the user returns to where they were (
                    {e.user_state === "escalated" ? "before this case" : e.user_state}) and Guruji answers
                    again.
                  </p>
                </>
              ) : (
                <>
                  <p className="text-sm">
                    {humanize(e.status)} {when(e.resolved_at)}
                  </p>
                  {e.notes && <p className="text-sm whitespace-pre-wrap text-muted-foreground">{e.notes}</p>}
                </>
              )}
            </CardBody>
          </Card>
          <Card>
            <CardHeader title="Details" />
            <CardBody className="space-y-1 text-sm">
              <p>Alerts sent: {e.alert_count}</p>
              <p>Acknowledged: {when(e.acknowledged_at)}</p>
              <p className="font-mono text-xs text-muted-foreground">{e.id}</p>
            </CardBody>
          </Card>
        </div>
      </div>
    </>
  );
}
