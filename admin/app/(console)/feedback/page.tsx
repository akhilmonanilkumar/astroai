"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Empty, ErrorNote, PageHeader } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody } from "@/components/ui/card";
import { Select } from "@/components/ui/input";
import { errorText, useApi } from "@/lib/api";
import type { Feedback } from "@/lib/types";
import { shortId, when } from "@/lib/utils";

const PAGE = 30;

function Said({ label, text }: { label: string; text: string | null }) {
  return (
    <div>
      <div className="mb-1 text-xs font-medium text-muted-foreground">{label}</div>
      {text ? (
        <p className="text-sm whitespace-pre-wrap">{text}</p>
      ) : (
        <p className="text-sm text-muted-foreground italic">Not kept (retention or erasure).</p>
      )}
    </div>
  );
}

export default function FeedbackPage() {
  const api = useApi();
  const [rating, setRating] = useState<"down" | "up" | "">("down");
  const [rows, setRows] = useState<Feedback[]>([]);
  const [more, setMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    async (before?: number) => {
      const params = new URLSearchParams({ limit: String(PAGE) });
      if (rating) params.set("rating", rating);
      if (before !== undefined) params.set("before", String(before));
      try {
        const page = await api<Feedback[]>(`/feedback?${params}`);
        setRows((old) => (before !== undefined ? [...old, ...page] : page));
        setMore(page.length === PAGE);
        setError(null);
      } catch (e) {
        setError(errorText(e));
      }
    },
    [api, rating],
  );

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <>
      <PageHeader
        title="Feedback"
        description="Answers users reacted to, with the message they answered: the daily persona-tuning queue. Opening this page is written to the audit log for each user shown."
      />
      <div className="mb-4 flex gap-2">
        <Select
          className="max-w-[12rem]"
          value={rating}
          onChange={(e) => setRating(e.target.value as "down" | "up" | "")}
        >
          <option value="down">👎 Disliked</option>
          <option value="up">👍 Liked</option>
          <option value="">All</option>
        </Select>
      </div>
      <ErrorNote>{error}</ErrorNote>
      {rows.length === 0 ? (
        <Card>
          <Empty>No rated answers yet.</Empty>
        </Card>
      ) : (
        <div className="space-y-3">
          {rows.map((f) => (
            <Card key={f.id}>
              <CardBody className="space-y-3">
                <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                  <Badge tone={f.rating === "down" ? "danger" : "ok"}>
                    {f.rating === "down" ? "👎 disliked" : "👍 liked"}
                  </Badge>
                  <span>{when(f.created_at)}</span>
                  <span>·</span>
                  <Link href={`/users/${f.user_id}`} className="font-mono text-accent hover:underline">
                    {shortId(f.user_id)}
                  </Link>
                  <span>·</span>
                  <span>{f.language ?? "—"}</span>
                </div>
                <Said label="User" text={f.question} />
                <Said label="Guruji" text={f.answer} />
              </CardBody>
            </Card>
          ))}
        </div>
      )}
      {more && (
        <div className="mt-4 text-center">
          <Button variant="outline" onClick={() => void load(rows.at(-1)?.id)}>
            Load more
          </Button>
        </div>
      )}
    </>
  );
}
