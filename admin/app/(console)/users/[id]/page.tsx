"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { Conversation } from "@/components/conversation";
import { Empty, ErrorNote, PageHeader, SeverityBadge, StateBadge, StatusBadge } from "@/components/page";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { Table, Td, Th } from "@/components/ui/table";
import { errorText, useApi, useResource } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { Message, Revealed, UserRecord } from "@/lib/types";
import { ago, clientId, humanize, shortId, when } from "@/lib/utils";

export default function UserPage() {
  const { id } = useParams<{ id: string }>();
  const api = useApi();
  const owner = useAuth().me?.role === "owner";
  const record = useResource<UserRecord>(`/users/${id}`);
  const [messages, setMessages] = useState<Message[]>([]);
  const [hasOlder, setHasOlder] = useState(false);
  const [revealed, setRevealed] = useState<Revealed | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [delta, setDelta] = useState("");
  const [creditNote, setCreditNote] = useState("");

  useEffect(() => {
    api<Message[]>(`/users/${id}/messages?limit=50`)
      .then((page) => {
        setMessages(page);
        setHasOlder(page.length === 50);
      })
      .catch((e: unknown) => setError(errorText(e)));
  }, [api, id]);

  async function older() {
    const first = messages[0];
    if (!first) return;
    const page = await api<Message[]>(`/users/${id}/messages?limit=50&before=${first.id}`);
    setMessages((m) => [...page, ...m]);
    setHasOlder(page.length === 50);
  }

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      record.reload();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  const r = record.data;
  if (!r) {
    return record.error ? (
      <ErrorNote>{record.error}</ErrorNote>
    ) : (
      <p className="text-sm text-muted-foreground">Loading…</p>
    );
  }
  const u = r.user;
  const active = r.escalations.find((e) => e.status === "open" || e.status === "acknowledged");

  return (
    <>
      <PageHeader
        title={
          <span className="flex flex-wrap items-center gap-2">
            User <span className="font-mono text-base">{shortId(u.id)}</span> <StateBadge state={u.state} />
          </span>
        }
        description={`Joined ${when(u.created_at)} · last wrote ${ago(u.last_inbound_at)} · language ${u.language ?? "unknown"}`}
        action={
          active ? (
            <Link href={`/escalations/${active.id}`}>
              <Button>Open the active case</Button>
            </Link>
          ) : null
        }
      />
      <div className="mb-4">
        <ErrorNote>{error}</ErrorNote>
      </div>
      <div className="grid gap-4 xl:grid-cols-[1fr_380px]">
        <div className="min-w-0 space-y-4">
          <Card className="flex flex-col">
            <CardHeader
              title="Conversation"
              description="Read-only. Replies go through an escalation case."
            />
            <Conversation
              messages={messages}
              onOlder={hasOlder ? () => void older() : undefined}
              className="max-h-[65vh]"
            />
          </Card>
          <Card>
            <CardHeader
              title="What Guruji remembers"
              description="Life facts the user shared, newest last."
            />
            {r.facts.length === 0 ? (
              <Empty>None yet.</Empty>
            ) : (
              <Table>
                <tbody>
                  {r.facts.map((f, i) => (
                    <tr key={i}>
                      <Td className="w-32 text-muted-foreground">{f.category}</Td>
                      <Td>{f.fact}</Td>
                      <Td className="w-32 whitespace-nowrap text-muted-foreground">{when(f.created_at)}</Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            )}
          </Card>
          <Card>
            <CardHeader
              title="Readings ledger"
              description="What Guruji has told them, so he stays consistent."
            />
            {r.readings.length === 0 ? (
              <Empty>None yet.</Empty>
            ) : (
              <Table>
                <tbody>
                  {r.readings.map((x, i) => (
                    <tr key={i}>
                      <Td className="w-32 text-muted-foreground">{x.topic}</Td>
                      <Td>
                        {x.summary}
                        {x.factors.length > 0 && (
                          <p className="mt-1 font-mono text-xs text-muted-foreground">
                            {x.factors.join(", ")}
                          </p>
                        )}
                      </Td>
                      <Td className="w-32 whitespace-nowrap text-muted-foreground">{when(x.created_at)}</Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            )}
          </Card>
        </div>

        <div className="space-y-4">
          <Card>
            <CardHeader
              title="Personal details"
              description={
                owner ? "Masked. Revealing them is logged." : "Masked. Only an owner can reveal them."
              }
              action={
                owner && !revealed ? (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy}
                    onClick={() =>
                      void act(async () => {
                        if (!confirm("Reveal this user's phone number and birth details? This is logged."))
                          return;
                        setRevealed(await api<Revealed>(`/users/${id}/reveal`, { method: "POST" }));
                      })
                    }
                  >
                    Reveal
                  </Button>
                ) : null
              }
            />
            <CardBody className="space-y-1 text-sm">
              {revealed ? (
                <>
                  <p>Phone: {revealed.phone ?? "—"}</p>
                  <p>Name: {revealed.name ?? "—"}</p>
                  <p>
                    Born: {revealed.birth_date ?? "—"} {revealed.birth_time ?? "(time not known)"}
                  </p>
                  <p>Place: {revealed.place ?? "—"}</p>
                  <p className="text-xs text-muted-foreground">
                    {revealed.latitude}, {revealed.longitude}
                  </p>
                  <Button size="sm" variant="ghost" onClick={() => setRevealed(null)}>
                    Hide
                  </Button>
                </>
              ) : r.birth ? (
                <>
                  <p>Birth details on file{r.birth.name_on_file ? ", with a name" : ""}.</p>
                  <p>
                    Birth time {r.birth.time_known ? "known" : "not known"} · {r.birth.tz_name}
                  </p>
                </>
              ) : (
                <p className="text-muted-foreground">No birth details yet (not onboarded).</p>
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader title={`Credits: ${u.balance}`} description="Append-only ledger, newest first." />
            {r.ledger.length > 0 && (
              <Table>
                <tbody>
                  {r.ledger.map((l, i) => (
                    <tr key={i}>
                      <Td className="tabular-nums">{l.delta > 0 ? `+${l.delta}` : l.delta}</Td>
                      <Td>
                        {l.reason}
                        {typeof l.ref?.note === "string" && (
                          <p className="text-xs text-muted-foreground">{l.ref.note}</p>
                        )}
                      </Td>
                      <Td className="whitespace-nowrap text-muted-foreground">{when(l.created_at)}</Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            )}
            {owner && (
              <form
                className="space-y-2 border-t border-border p-4"
                onSubmit={(e) => {
                  e.preventDefault();
                  void act(async () => {
                    await api(`/users/${id}/credits`, {
                      method: "POST",
                      body: { client_id: clientId(), delta: Number(delta), note: creditNote.trim() },
                    });
                    setDelta("");
                    setCreditNote("");
                  });
                }}
              >
                <p className="text-xs font-medium text-muted-foreground">Adjust (e.g. a goodwill refund)</p>
                <div className="flex gap-2">
                  <div className="w-24 shrink-0">
                    <Input
                      type="number"
                      min={-1000}
                      max={1000}
                      required
                      placeholder="+2"
                      value={delta}
                      onChange={(e) => setDelta(e.target.value)}
                    />
                  </div>
                  <Input
                    required
                    maxLength={500}
                    placeholder="Reason"
                    value={creditNote}
                    onChange={(e) => setCreditNote(e.target.value)}
                  />
                </div>
                <Button size="sm" disabled={busy || !delta || Number(delta) === 0}>
                  Apply
                </Button>
              </form>
            )}
          </Card>

          <Card>
            <CardHeader title="Escalations" />
            {r.escalations.length === 0 ? (
              <Empty>None.</Empty>
            ) : (
              <Table>
                <tbody>
                  {r.escalations.map((e) => (
                    <tr key={e.id}>
                      <Td>
                        <Link href={`/escalations/${e.id}`} className="hover:underline">
                          {humanize(e.category)}
                        </Link>
                      </Td>
                      <Td>
                        <SeverityBadge severity={e.severity} />
                      </Td>
                      <Td>
                        <StatusBadge status={e.status} />
                      </Td>
                      <Td className="whitespace-nowrap text-muted-foreground">{when(e.opened_at)}</Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            )}
          </Card>

          <Card>
            <CardHeader
              title="Consent"
              description="Proof: notice version and the button reply's message id."
            />
            {r.consents.length === 0 ? (
              <Empty>No consent recorded.</Empty>
            ) : (
              <CardBody className="space-y-2 text-sm">
                {r.consents.map((c, i) => (
                  <div key={i}>
                    <p>
                      {c.granted ? "Agreed" : "Declined"} · {c.purpose} · notice {c.notice_version}
                      {c.age_confirmed && " · 18+ confirmed"}
                    </p>
                    <p className="break-all font-mono text-xs text-muted-foreground">
                      {when(c.given_at)} · {c.wamid}
                    </p>
                  </div>
                ))}
              </CardBody>
            )}
          </Card>

          {owner && (
            <Card>
              <CardHeader title="Access" />
              <CardBody className="space-y-2">
                {u.state === "blocked" ? (
                  <Button
                    variant="outline"
                    disabled={busy}
                    onClick={() => void act(() => api(`/users/${id}/unblock`, { method: "POST" }))}
                  >
                    Unblock
                  </Button>
                ) : (
                  <Button
                    variant="danger"
                    disabled={busy || u.state === "escalated"}
                    onClick={() =>
                      void act(async () => {
                        if (!confirm("Block this user? Guruji will stop replying to them.")) return;
                        await api(`/users/${id}/block`, { method: "POST" });
                      })
                    }
                  >
                    Block
                  </Button>
                )}
                <Button
                  variant="outline"
                  disabled={busy}
                  onClick={() =>
                    void act(async () => {
                      const typed = prompt(
                        "Erase ALL of this user's personal data? This cannot be undone. " +
                          "Only for a verified request. Type ERASE to confirm.",
                      );
                      if (typed !== "ERASE") return;
                      await api(`/users/${id}/erase`, { method: "POST" });
                    })
                  }
                >
                  Erase data
                </Button>
                <Label className="mt-2">
                  {u.state === "escalated"
                    ? "Close the active case before blocking."
                    : "Blocked users get no replies. Unblocking returns them to Guruji (or to onboarding)."}
                </Label>
              </CardBody>
            </Card>
          )}
        </div>
      </div>
    </>
  );
}
