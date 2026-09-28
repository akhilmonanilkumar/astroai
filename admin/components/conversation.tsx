"use client";

import { useEffect, useRef } from "react";

import type { Message } from "@/lib/types";
import { cn, when } from "@/lib/utils";

const WHO = { user: "User", guru: "Guruji", human: "Team" } as const;

function text(m: Message): string {
  if (m.body) return m.body;
  if (m.kind === "template") return `[template sent: ${String(m.meta?.template ?? "")}]`;
  if (m.direction === "in") return "[not kept: sent before consent]";
  return "[no reply]";
}

/** A chat transcript, oldest first; sticks to the bottom as new messages arrive. */
export function Conversation({
  messages,
  onOlder,
  className,
}: {
  messages: Message[];
  onOlder?: () => void;
  className?: string;
}) {
  const end = useRef<HTMLDivElement>(null);
  const lastId = messages.at(-1)?.id;
  useEffect(() => {
    end.current?.scrollIntoView({ block: "nearest" });
  }, [lastId]);

  return (
    <div className={cn("space-y-3 overflow-y-auto px-4 py-4", className)}>
      {onOlder && (
        <button onClick={onOlder} className="mx-auto block text-xs text-accent hover:underline">
          Load older messages
        </button>
      )}
      {messages.length === 0 && <p className="text-center text-sm text-muted-foreground">No messages yet.</p>}
      {messages.map((m) => {
        const mine = m.direction === "out";
        const quiet = !m.body;
        return (
          <div key={m.id} className={cn("flex", mine ? "justify-end" : "justify-start")}>
            <div
              className={cn(
                "max-w-[85%] rounded-lg border border-border px-3 py-2 shadow-sm sm:max-w-[70%]",
                m.sent_by === "user" && "bg-bubble-in",
                m.sent_by === "guru" && "bg-bubble-guru",
                m.sent_by === "human" && "bg-bubble-human",
              )}
            >
              <p className="mb-1 text-[11px] font-medium text-muted-foreground">
                {WHO[m.sent_by]}
                {m.kind === "audio" && " · voice note"}
                {m.meta?.kind === "safety" && " · safety reply"}
                {m.meta?.kind === "holding" && " · holding reply"}
                {" · "}
                {when(m.created_at)}
              </p>
              <p
                className={cn(
                  "text-sm whitespace-pre-wrap break-words",
                  quiet && "italic text-muted-foreground",
                )}
              >
                {text(m)}
              </p>
            </div>
          </div>
        );
      })}
      <div ref={end} />
    </div>
  );
}
