"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Empty, ErrorNote, PageHeader, StateBadge } from "@/components/page";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/input";
import { Table, Td, Th } from "@/components/ui/table";
import { errorText, useApi } from "@/lib/api";
import type { UserRow, UserState } from "@/lib/types";
import { ago, shortId, when } from "@/lib/utils";

const STATES: UserState[] = ["new", "consented", "onboarding", "active", "escalated", "blocked"];
const PAGE = 50;

export default function UsersPage() {
  const api = useApi();
  const [state, setState] = useState<UserState | "">("");
  const [phone, setPhone] = useState("");
  const [query, setQuery] = useState<{ state: string; phone: string }>({ state: "", phone: "" });
  const [rows, setRows] = useState<UserRow[]>([]);
  const [more, setMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    async (before?: string) => {
      const params = new URLSearchParams({ limit: String(PAGE) });
      if (query.state) params.set("state", query.state);
      if (query.phone) params.set("phone", query.phone);
      if (before) params.set("before", before);
      try {
        const page = await api<UserRow[]>(`/users?${params}`);
        setRows((old) => (before ? [...old, ...page] : page));
        setMore(page.length === PAGE);
        setError(null);
      } catch (e) {
        setError(errorText(e));
      }
    },
    [api, query],
  );

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <>
      <PageHeader title="Users" description="Opening a user's record is written to the audit log." />
      <form
        className="mb-4 flex flex-wrap gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          setQuery({ state, phone: phone.trim() });
        }}
      >
        <Input
          className="max-w-xs"
          type="tel"
          placeholder="Find by WhatsApp number"
          value={phone}
          onChange={(e) => setPhone(e.target.value)}
        />
        <Select
          className="max-w-[12rem]"
          value={state}
          onChange={(e) => setState(e.target.value as UserState | "")}
        >
          <option value="">All states</option>
          {STATES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </Select>
        <Button>Search</Button>
        {(query.phone || query.state) && (
          <Button
            type="button"
            variant="ghost"
            onClick={() => {
              setPhone("");
              setState("");
              setQuery({ state: "", phone: "" });
            }}
          >
            Clear
          </Button>
        )}
      </form>
      <ErrorNote>{error}</ErrorNote>
      <Card>
        {rows.length === 0 ? (
          <Empty>No users found.</Empty>
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>User</Th>
                <Th>State</Th>
                <Th>Language</Th>
                <Th>Chart</Th>
                <Th className="text-right">Credits</Th>
                <Th>Joined</Th>
                <Th>Last message</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((u) => (
                <tr key={u.id} className="hover:bg-muted/60">
                  <Td>
                    <Link href={`/users/${u.id}`} className="font-mono text-xs text-accent hover:underline">
                      {shortId(u.id)}
                    </Link>
                  </Td>
                  <Td>
                    <StateBadge state={u.state} />
                  </Td>
                  <Td>{u.language ?? "—"}</Td>
                  <Td>{u.onboarded ? "yes" : "—"}</Td>
                  <Td className="text-right tabular-nums">{u.balance}</Td>
                  <Td>{when(u.created_at)}</Td>
                  <Td>{ago(u.last_inbound_at)}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
      {more && (
        <div className="mt-4 text-center">
          <Button variant="outline" onClick={() => void load(rows.at(-1)?.created_at)}>
            Load more
          </Button>
        </div>
      )}
    </>
  );
}
