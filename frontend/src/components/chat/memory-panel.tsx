"use client";

import { useCallback, useEffect, useState } from "react";
import {
  MemoryNoteItem,
  deleteAllMemoryNotes,
  deleteMemoryNote,
  listMemoryNotes,
} from "../../lib/chats";

/**
 * The preferences the assistant holds, so the reader can see and remove them.
 *
 * This panel exists because a memory the reader cannot see is not consent. It
 * lists every note in the reader's own words, shows how often each has actually
 * been used, and removes on request.
 */
export default function MemoryPanel() {
  const [notes, setNotes] = useState<MemoryNoteItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => {
    setLoading(true);
    listMemoryNotes()
      .then(setNotes)
      .catch((cause: unknown) =>
        setError(cause instanceof Error ? cause.message : "Could not load your preferences."),
      )
      .finally(() => setLoading(false));
  }, []);

  useEffect(reload, [reload]);

  async function remove(id: string) {
    setNotes((current) => current.filter((note) => note.id !== id));
    try {
      await deleteMemoryNote(id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not remove that preference.");
      reload();
    }
  }

  async function removeAll() {
    if (!notes.length) return;
    if (!window.confirm(`Remove all ${notes.length} saved preferences?`)) return;
    setNotes([]);
    try {
      await deleteAllMemoryNotes();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not remove your preferences.");
      reload();
    }
  }

  return (
    <section className="card" aria-labelledby="memory-heading">
      <h2 id="memory-heading">What the assistant remembers</h2>
      <p className="page-description">
        Only preferences you asked for, in your own words. Nothing is inferred from what you read,
        and removing one takes effect on your next question.
      </p>

      {error && (
        <p className="status-message" role="alert">
          {error}
        </p>
      )}
      {loading && <p className="status-message">Loading your preferences&hellip;</p>}
      {!loading && notes.length === 0 && (
        <p className="status-message">
          Nothing saved yet. You can say &ldquo;always give me the dates&rdquo; in a chat and it will
          be remembered here.
        </p>
      )}

      {notes.length > 0 && (
        <>
          <ul className="memory-list">
            {notes.map((note) => (
              <li key={note.id} className="memory-item">
                <span className="memory-item-text">{note.note_text}</span>
                <span className="memory-item-uses">
                  {note.use_count === 0 ? "not used yet" : `used ${note.use_count}×`}
                </span>
                <button
                  type="button"
                  className="memory-item-remove"
                  onClick={() => void remove(note.id)}
                >
                  Remove
                  <span className="sr-only">: {note.note_text}</span>
                </button>
              </li>
            ))}
          </ul>
          <div className="button-row">
            <button type="button" onClick={() => void removeAll()}>
              Remove all preferences
            </button>
          </div>
        </>
      )}
    </section>
  );
}