"use client";

import { useEffect, useState } from "react";
import { api } from "./api";

type Mailbox = { id: string; email: string; status: string; ai_consent: boolean };

export function useConnectedMailbox() {
  const [mailboxes, setMailboxes] = useState<Mailbox[]>([]);
  const [mailboxId, setMailboxId] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;

    api<Mailbox[]>("/api/mailboxes")
      .then(items => {
        if (!active) return;
        const connected = items.filter(item => item.status === "active");
        setMailboxes(connected);
        setMailboxId(current => connected.some(item => item.id === current) ? current : connected[0]?.id || "");
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : "Could not load connected Outlook accounts.");
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, []);

  return { mailboxes, mailboxId, setMailboxId, loading, error };
}
