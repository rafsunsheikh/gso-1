/**
 * Load the repo-root .env before anything reads process.env.
 *
 * Import this FIRST in every entry point. Secrets (TAVILY_API_KEY) live in
 * <repo>/.env, which is gitignored, never in the source tree, and never in a
 * release snapshot.
 *
 * Uses Node's built-in loader (node >= 20.12), so no dotenv dependency.
 * Real environment variables always win over the file.
 */

import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

/**
 * Candidate locations, in order. A release runs from
 * var/releases/<ts>/opsroom/src, so the repo root is two levels up from the
 * package, but the canonical secret lives in the real repo, which the
 * supervisor passes through via OPSROOM_ENV_FILE when it differs.
 */
function candidates(): string[] {
  const list: string[] = [];
  if (process.env.OPSROOM_ENV_FILE) list.push(process.env.OPSROOM_ENV_FILE);
  list.push(path.resolve(here, "../../.env")); // <repo>/.env
  list.push(path.resolve(here, "../.env")); // opsroom/.env
  return list;
}

let loadedFrom: string | null = null;

for (const file of candidates()) {
  try {
    process.loadEnvFile(file);
    loadedFrom = file;
    break;
  } catch {
    // missing or unreadable, try the next candidate
  }
}

export const ENV_FILE = loadedFrom;
