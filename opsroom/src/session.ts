/**
 * Conversation memory for the Ops Room.
 *
 * The agent is spawned fresh for every question and exits when it has answered,
 * which is a good property, a broken edit cannot wedge a long-lived process,
 * but it meant the Ops Room could not hold a conversation. "Which repos are
 * dirty?" followed by "commit the first one" was two strangers talking.
 *
 * pi contexts are plain JSON, and `agent.state.messages` both seeds a run and
 * holds the result of one, so the fix is to write that array to disk after each
 * turn and read it back before the next. The process still dies; the
 * conversation does not.
 *
 * Two things this deliberately does not do:
 *
 * * It does not keep everything. A local model has a small context window, and
 *   a conversation that grows without limit stops fitting in it, silently and
 *   at the worst moment. Old turns are dropped once the transcript passes a
 *   budget, oldest first, always leaving the most recent exchanges intact.
 * * It does not share one history between unrelated callers. Each session has
 *   a key, so the dock, the phone and a scripted `./ops` call can be the same
 *   conversation or three different ones, decided by the caller rather than by
 *   accident.
 */

import { mkdirSync, readFileSync, renameSync, writeFileSync, unlinkSync, existsSync } from "node:fs";
import path from "node:path";

import { dataDir } from "./settings.ts";

/** Roughly how much transcript to carry. Characters, because counting tokens
 *  properly means a tokeniser per model and the budget only needs to be in the
 *  right order of magnitude. Four characters to a token is the usual rule. */
const BUDGET_CHARS = Number(process.env.OPSROOM_HISTORY_CHARS ?? 24000);

/** Never carry more than this many messages regardless of size, so a session
 *  of many tiny turns cannot grow unbounded either. */
const MAX_MESSAGES = 120;

export type StoredSession = {
  key: string;
  updated: number;
  messages: unknown[];
};

function file(): string {
  return path.join(dataDir(), "opsroom-sessions.json");
}

function readAll(): Record<string, StoredSession> {
  try {
    const raw = JSON.parse(readFileSync(file(), "utf8"));
    return raw && typeof raw === "object" ? raw : {};
  } catch {
    return {};
  }
}

function writeAll(data: Record<string, StoredSession>): void {
  mkdirSync(path.dirname(file()), { recursive: true });
  const tmp = file() + ".tmp";
  writeFileSync(tmp, JSON.stringify(data, null, 1), "utf8");
  renameSync(tmp, file());
}

/**
 * Drop the oldest turns until the transcript fits the budget.
 *
 * Trimming from the front rather than summarising is the honest choice here:
 * summarising costs a model call the user did not ask for, and on a small local
 * model produces a worse record than simply forgetting. The most recent
 * exchanges are the ones a follow-up refers to.
 */
export function trim(messages: unknown[]): unknown[] {
  let kept = messages.slice(-MAX_MESSAGES);
  const size = (m: unknown) => JSON.stringify(m ?? "").length;
  let total = kept.reduce((n, m) => n + size(m), 0);
  while (kept.length > 2 && total > BUDGET_CHARS) {
    total -= size(kept[0]);
    kept = kept.slice(1);
  }
  return kept;
}

/** The stored transcript for a session, ready to seed `initialState.messages`. */
export function load(key: string): unknown[] {
  const entry = readAll()[key];
  return Array.isArray(entry?.messages) ? entry.messages : [];
}

/** Replace a session's transcript with what the agent now holds. */
export function save(key: string, messages: unknown[]): void {
  const all = readAll();
  all[key] = { key, updated: Date.now(), messages: trim(messages) };
  writeAll(all);
}

export function clear(key: string): boolean {
  const all = readAll();
  if (!(key in all)) return false;
  delete all[key];
  writeAll(all);
  return true;
}

/** Every session, newest first, without the transcripts. */
export function list(): Array<{ key: string; updated: number; messages: number }> {
  return Object.values(readAll())
    .map((s) => ({ key: s.key, updated: s.updated, messages: (s.messages || []).length }))
    .sort((a, b) => b.updated - a.updated);
}

export const SESSIONS_FILE = () => file();
export const sessionsExist = () => existsSync(file());
export const forgetAll = () => {
  try { unlinkSync(file()); } catch { /* nothing stored yet */ }
};
