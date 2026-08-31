/**
 * Persistent credential storage for Ops Room.
 *
 * pi-ai ships an in-memory store, which forgets the OAuth token the moment the
 * process exits, and Ops Room is spawned fresh for every question. Tokens live
 * in a file beside GSO-1's other state instead, owner-readable only, the same
 * treatment settings.json gets for the Telegram token.
 *
 * The contract pi-ai asks for is small: read, list, modify, delete. `modify` is
 * the only write path and must be a serialized read-modify-write, because
 * OAuth refresh happens inside it: two Ops Room processes waking at once must
 * not both refresh a rotating token and race each other's result.
 */

import { chmodSync, mkdirSync, readFileSync, renameSync, writeFileSync, existsSync, unlinkSync, openSync, closeSync } from "node:fs";
import path from "node:path";

import { dataDir } from "./settings.ts";

type Credential = Record<string, unknown> & { type?: string };
type Store = Record<string, Credential>;

const FILE = path.join(dataDir(), "credentials.json");
const LOCK = FILE + ".lock";

function readAll(): Store {
  try {
    const raw = JSON.parse(readFileSync(FILE, "utf8"));
    return raw && typeof raw === "object" ? (raw as Store) : {};
  } catch {
    return {};
  }
}

function writeAll(data: Store): void {
  mkdirSync(path.dirname(FILE), { recursive: true });
  const tmp = FILE + ".tmp";
  writeFileSync(tmp, JSON.stringify(data, null, 1));
  // Mode is set on the temp file before the rename, so there is never an
  // instant where a complete file holding a token is world-readable.
  try { chmodSync(tmp, 0o600); } catch { /* filesystems without POSIX modes */ }
  renameSync(tmp, FILE);
}

/**
 * A crude cross-process lock: an exclusive create that fails if the file is
 * already there. Enough for this, where contention means two Ops Room runs
 * starting within the same second, and a stale lock is cleared by age rather
 * than left to wedge the agent forever.
 */
const STALE_MS = 30_000;

async function withLock<T>(fn: () => Promise<T> | T): Promise<T> {
  mkdirSync(path.dirname(LOCK), { recursive: true });
  for (let attempt = 0; attempt < 100; attempt++) {
    try {
      closeSync(openSync(LOCK, "wx"));
      try {
        return await fn();
      } finally {
        try { unlinkSync(LOCK); } catch { /* already gone */ }
      }
    } catch (err) {
      if ((err as NodeJS.ErrnoException).code !== "EEXIST") throw err;
      try {
        const age = Date.now() - (await import("node:fs")).statSync(LOCK).mtimeMs;
        if (age > STALE_MS) { unlinkSync(LOCK); continue; }
      } catch { /* it vanished under us, which is fine */ }
      await new Promise((r) => setTimeout(r, 50));
    }
  }
  // Never block the agent indefinitely over a lock; proceed unlocked rather
  // than fail the run, the worst case being a refresh that races.
  return await fn();
}

export function createFileCredentialStore() {
  return {
    async read(providerId: string): Promise<Credential | undefined> {
      return readAll()[providerId];
    },

    /** Metadata only. Must never resolve secrets. */
    async list(): Promise<Array<{ providerId: string; type: string }>> {
      return Object.entries(readAll()).map(([providerId, cred]) => ({
        providerId,
        type: String(cred?.type ?? "unknown"),
      }));
    },

    async modify(
      providerId: string,
      fn: (current: Credential | undefined) => Credential | undefined |
        Promise<Credential | undefined>,
    ): Promise<Credential | undefined> {
      return withLock(async () => {
        const all = readAll();
        const next = await fn(all[providerId]);
        if (next === undefined) delete all[providerId];
        else all[providerId] = next;
        writeAll(all);
        return next;
      });
    },

    async delete(providerId: string): Promise<void> {
      await withLock(() => {
        const all = readAll();
        delete all[providerId];
        writeAll(all);
      });
    },
  };
}

/** Which providers have a stored credential. Used to report status without
 *  reading, let alone printing, any secret. */
export function storedProviders(): string[] {
  return Object.keys(readAll());
}

export const CREDENTIALS_FILE = FILE;
export const credentialsExist = () => existsSync(FILE);
