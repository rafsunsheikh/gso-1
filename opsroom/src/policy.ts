/**
 * Path policy for Ops Room.
 *
 * Two rings:
 *   READ_ROOTS    — may be read, searched, listed
 *   SANDBOX_ROOT  — the only place the agent may write
 *   IMMUTABLE     — never writable, even inside the sandbox (plan invariant #1:
 *                   the supervisor is what rolls back a bad self-edit, so the
 *                   agent must never be able to touch it)
 *
 * Every check resolves symlinks and `..` first, so `sandbox/../../etc/passwd`
 * and a symlink pointing outside both fail closed.
 */

import { realpathSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const home = os.homedir();

/**
 * The GSO-1 checkout this sidecar ships inside, found from its own location
 * rather than guessed from a path under $HOME. The guess was wrong on every
 * machine but the author's, and a sandbox root that does not exist fails in
 * the least useful direction.
 */
const INSTALL_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");

function expand(p: string): string {
  return path.resolve(p.startsWith("~") ? path.join(home, p.slice(1)) : p);
}

/** The one writable root. Everything else is read-only at best. */
export const SANDBOX_ROOT = expand(
  process.env.OPSROOM_SANDBOX_ROOT ?? INSTALL_ROOT,
);

/**
 * The project roots GSO-1 itself is configured with, as passed through the
 * environment. Falls back to ~/Projects, the same default the server uses.
 */
function projectRootsFromEnv(): string[] {
  const raw = process.env.MANAGER_PROJECTS_DIRS ?? process.env.MANAGER_PROJECTS_DIR ?? "";
  const entries = raw
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean)
    // Entries may carry a display label as "Label:/path".
    .map((e) => {
      const i = e.indexOf(":");
      if (i > 0 && /^\s*[~/.]/.test(e.slice(i + 1))) return e.slice(i + 1).trim();
      return e;
    });
  return entries.length ? entries : [path.join(home, "Projects")];
}

/** Readable roots. Defaults to the sandbox plus the project directories. */
export const READ_ROOTS: string[] = (
  process.env.OPSROOM_READ_ROOTS ??
  // The server already knows where the user keeps their code; reuse that
  // rather than naming somebody's folders here.
  [SANDBOX_ROOT, ...projectRootsFromEnv()].join(",")
)
  .split(",")
  .map((s) => s.trim())
  .filter(Boolean)
  .map(expand);

/** Never writable. */
export const IMMUTABLE: string[] = (
  process.env.OPSROOM_IMMUTABLE ??
  [
    path.join(SANDBOX_ROOT, "supervisor"),
    path.join(SANDBOX_ROOT, "var"),
    // The launcher decides which code runs; if the agent breaks it, nothing
    // starts. Same reasoning as the supervisor: keep it boring and untouchable.
    path.join(SANDBOX_ROOT, "ops"),
  ].join(",")
)
  .split(",")
  .map((s) => s.trim())
  .filter(Boolean)
  .map(expand);

export class PolicyError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PolicyError";
  }
}

/**
 * Resolve a path as far as it exists, so non-existent targets (a file about to
 * be written) still get their real parent checked. Prevents symlink escape.
 */
export function resolveReal(p: string): string {
  const abs = expand(p);
  let cur = abs;
  const tail: string[] = [];
  // Walk up until something exists, then realpath that and re-append.
  for (let i = 0; i < 64; i++) {
    try {
      return path.join(realpathSync(cur), ...tail.reverse());
    } catch {
      const parent = path.dirname(cur);
      if (parent === cur) return abs; // hit the root; nothing resolvable
      tail.push(path.basename(cur));
      cur = parent;
    }
  }
  return abs;
}

function isUnder(target: string, root: string): boolean {
  return target === root || target.startsWith(root + path.sep);
}

export function readable(p: string): boolean {
  const t = resolveReal(p);
  return READ_ROOTS.some((r) => isUnder(t, r));
}

export function writable(p: string): boolean {
  const t = resolveReal(p);
  if (IMMUTABLE.some((r) => isUnder(t, r))) return false;
  return isUnder(t, SANDBOX_ROOT);
}

export function assertReadable(p: string): string {
  const t = resolveReal(p);
  if (!readable(t)) {
    throw new PolicyError(
      `read denied: ${t}\nOps Room may only read under: ${READ_ROOTS.join(", ")}`,
    );
  }
  return t;
}

export function assertWritable(p: string): string {
  const t = resolveReal(p);
  if (IMMUTABLE.some((r) => isUnder(t, r))) {
    throw new PolicyError(
      `write denied: ${t}\nThis path is immutable (supervisor and release state ` +
        `must stay editable only by a human).`,
    );
  }
  if (!isUnder(t, SANDBOX_ROOT)) {
    throw new PolicyError(
      `write denied: ${t}\nOps Room may only write under: ${SANDBOX_ROOT}`,
    );
  }
  return t;
}

/** Param names that carry a filesystem path across pi's built-in tools. */
const PATH_KEYS = ["path", "file_path", "filePath", "dir", "directory", "cwd", "root"];

/** Defence in depth: validate path-ish params before a tool runs. */
export function assertParamPaths(
  params: Record<string, unknown> | undefined,
  mode: "read" | "write",
): void {
  if (!params) return;
  for (const key of PATH_KEYS) {
    const v = params[key];
    if (typeof v === "string" && v.trim()) {
      mode === "write" ? assertWritable(v) : assertReadable(v);
    }
  }
}

export function describePolicy(): string {
  return [
    `sandbox (writable): ${SANDBOX_ROOT}`,
    `readable roots    : ${READ_ROOTS.join(", ")}`,
    `immutable         : ${IMMUTABLE.join(", ")}`,
  ].join("\n");
}
