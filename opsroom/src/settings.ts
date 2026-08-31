/**
 * Where the sidecar's own state lives, and what GSO-1 has configured.
 *
 * Ops Room has to behave the same whether GSO-1 spawned it or somebody typed
 * `./ops` in a terminal. GSO-1 injects OPSROOM_PROVIDER when it spawns the
 * agent, so for a while the provider chosen in the app was invisible from the
 * command line and `./ops` went on talking to llama-server after the user had
 * switched to Claude. The sidecar reads the same settings.json instead, with
 * the environment still winning so a one-off override stays possible.
 */

import { readFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

/** GSO-1's data directory, resolved the way config.py resolves it. */
export function dataDir(): string {
  const override = process.env.MANAGER_DATA_DIR?.trim();
  if (override) {
    return override.startsWith("~")
      ? path.join(os.homedir(), override.slice(1))
      : override;
  }
  // A source checkout keeps state beside the code, and the sandbox root is
  // that checkout, which is exactly what `ops` exports.
  const root = process.env.OPSROOM_SANDBOX_ROOT;
  if (root) return path.join(root, "data");
  return path.join(os.homedir(), ".gso-1");
}

type Settings = Record<string, any>;

let cached: Settings | null = null;

/** settings.json, or an empty object when it does not exist yet. */
export function settings(): Settings {
  if (cached) return cached;
  try {
    const raw = JSON.parse(readFileSync(path.join(dataDir(), "settings.json"), "utf8"));
    cached = raw && typeof raw === "object" ? raw : {};
  } catch {
    cached = {};
  }
  return cached!;
}

/** One value from the `opsroom` block, environment first. */
export function opsSetting(key: string, envVar?: string): string | undefined {
  const fromEnv = envVar ? process.env[envVar]?.trim() : undefined;
  if (fromEnv) return fromEnv;
  const block = settings().opsroom;
  const value = block && typeof block === "object" ? block[key] : undefined;
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}
