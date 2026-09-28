"use client";

import { useState } from "react";

import { ErrorNote, PageHeader } from "@/components/page";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Textarea } from "@/components/ui/input";
import { errorText, useApi, useResource } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { ConfigEntry } from "@/lib/types";
import { humanize, when } from "@/lib/utils";

const HELP: Record<string, string> = {
  flags: "Only voice_enabled is enforced so far; busy_mode and new_user_admission are not built yet.",
  packs: "Dakshina credit packs. Prices in rupees, GST-inclusive. Payments arrive in M7.",
  passes: "Guru Plus passes. Payments arrive in M7.",
  free_tier: "Free usage before and after the 72-hour welcome window. Enforced from M7.",
  prashna: "What one question costs. Enforced from M7.",
  plus_limits: "Fair-use limits for Guru Plus. Enforced from M7.",
  human_template:
    "The approved WhatsApp utility template the team sends after the 24-hour window, and its language code for each user language.",
};

function Entry({ entry, canEdit, onSaved }: { entry: ConfigEntry; canEdit: boolean; onSaved: () => void }) {
  const api = useApi();
  const original = JSON.stringify(entry.value, null, 2);
  const [text, setText] = useState(original);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const dirty = text !== original;

  async function save() {
    let value: unknown;
    try {
      value = JSON.parse(text);
    } catch {
      setError("That is not valid JSON.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api(`/config/${entry.key}`, { method: "PUT", body: { value } });
      onSaved();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader
        title={humanize(entry.key)}
        description={HELP[entry.key]}
        action={
          <span className="text-right text-xs whitespace-nowrap text-muted-foreground">
            {entry.updated_at ? when(entry.updated_at) : "default"}
            <br />
            {entry.updated_by ?? ""}
          </span>
        }
      />
      <CardBody className="space-y-2">
        <Textarea
          rows={Math.min(16, text.split("\n").length + 1)}
          className="font-mono text-xs"
          spellCheck={false}
          readOnly={!canEdit}
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        <ErrorNote>{error}</ErrorNote>
        {canEdit && (
          <div className="flex gap-2">
            <Button size="sm" disabled={!dirty || busy} onClick={() => void save()}>
              Save
            </Button>
            {dirty && (
              <Button size="sm" variant="ghost" onClick={() => setText(original)}>
                Undo
              </Button>
            )}
          </div>
        )}
      </CardBody>
    </Card>
  );
}

export default function ConfigPage() {
  const owner = useAuth().me?.role === "owner";
  const { data, error, reload } = useResource<ConfigEntry[]>("/config");
  return (
    <>
      <PageHeader
        title="Config"
        description={`Business knobs from app_config. Workers pick up changes within about 30 seconds.${owner ? " Every save is audited." : " Only owners can edit."}`}
      />
      <ErrorNote>{error}</ErrorNote>
      <div className="grid gap-4 lg:grid-cols-2">
        {(data ?? []).map((e) => (
          <Entry key={`${e.key}:${e.updated_at}`} entry={e} canEdit={owner} onSaved={reload} />
        ))}
      </div>
    </>
  );
}
