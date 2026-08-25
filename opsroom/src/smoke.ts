/**
 * Behavioural checks for the health gate.
 *
 * Why this exists: on 2026-08-22 the agent was asked to add an `uptime_info`
 * tool. It wrote clean, well-structured code that fetched
 * `http://127.0.0.1:8420/api/system/uptime`: an endpoint that does not exist.
 * Its own `catch { return 0 }` swallowed the 404, so the tool reported "0s"
 * uptime on a machine that had been up for a day. `selfcheck` passed it and
 * `verify_release` PASSED, because both only proved the module *loads*.
 *
 * A broken build is easy to catch. A build that runs perfectly and lies is not.
 * These checks test behaviour, not structure.
 */

import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import type { AgentTool } from "@earendil-works/pi-agent-core";

const SRC = path.dirname(fileURLToPath(import.meta.url));
const GSO1 = process.env.OPSROOM_GSO1_URL ?? "http://127.0.0.1:8420";

/**
 * Tools that are safe to actually run during verification: no side effects, no
 * required parameters. Anything that mutates state or costs money stays out, 
 * notably build/promote/rollback (destructive) and web_search (spends credits).
 */
const SMOKE_SAFE = new Set([
  "list_apps",
  "git_dirty_sweep",
  "release_status",
  "disk_free",
  "uptime_info",
  "ls",
]);

export interface Failure {
  check: string;
  detail: string;
}

/**
 * Every GSO-1 URL referenced in the sidecar's source must resolve to a real
 * route. This is the direct fix for the hallucinated-endpoint failure: a made-up
 * path is caught at verify time instead of silently returning zeros forever.
 */
export async function checkEndpoints(): Promise<Failure[]> {
  const failures: Failure[] = [];
  const files = (await readdir(SRC)).filter((f) => f.endsWith(".ts"));

  // Only quoted URLs in real code. Comments are stripped first, otherwise a
  // docstring describing a past bug is flagged as the bug itself.
  const literal = /["'`]https?:\/\/127\.0\.0\.1:8420(\/[^"'`\s]*)["'`]/g;
  const seen = new Set<string>();

  const stripComments = (src: string): string =>
    src
      .replace(/\/\*[\s\S]*?\*\//g, "") // block comments, including JSDoc
      .replace(/(^|[^:"'`\\])\/\/.*$/gm, "$1"); // line comments, sparing "://"

  for (const file of files) {
    const body = stripComments(await readFile(path.join(SRC, file), "utf-8"));
    for (const m of body.matchAll(literal)) {
      const route = m[1].split("?")[0];
      if (route && !seen.has(route)) seen.add(route);
    }
  }

  for (const route of seen) {
    // Skip parameterised paths, we cannot invent a valid id for them.
    if (route.includes("${") || route.includes("{")) continue;
    try {
      const res = await fetch(`${GSO1}${route}`, { signal: AbortSignal.timeout(20_000) });
      if (res.status === 404) {
        failures.push({
          check: "endpoint",
          detail: `${route} returns 404, this route does not exist in GSO-1. ` +
            `A tool calling it will fail silently if its error handling returns a default.`,
        });
      }
    } catch (err) {
      failures.push({ check: "endpoint", detail: `${route} unreachable: ${(err as Error).message}` });
    }
  }
  return failures;
}

/**
 * Run the side-effect-free tools and reject results that look like a swallowed
 * failure: a throw, empty content, or an all-zero numeric payload.
 */
export async function smokeTools(tools: AgentTool[]): Promise<Failure[]> {
  const failures: Failure[] = [];

  for (const tool of tools) {
    if (!SMOKE_SAFE.has(tool.name)) continue;
    try {
      const res: any = await tool.execute(`smoke-${tool.name}`, {}, undefined, undefined);
      const text = res?.content?.[0]?.text ?? "";
      if (!text.trim()) {
        failures.push({ check: "smoke", detail: `${tool.name} returned empty content` });
        continue;
      }
      // A measurement tool whose every number is zero is almost always a
      // swallowed error rather than a real reading.
      const numbers = [...String(text).matchAll(/:\s*(-?\d+(?:\.\d+)?)/g)].map((m) => Number(m[1]));
      if (numbers.length >= 2 && numbers.every((n) => n === 0)) {
        failures.push({
          check: "smoke",
          detail: `${tool.name} returned all-zero values (${text.replace(/\s+/g, " ").slice(0, 120)}), ` +
            `likely a swallowed error rather than a real measurement`,
        });
      }
    } catch (err) {
      failures.push({ check: "smoke", detail: `${tool.name} threw: ${(err as Error).message}` });
    }
  }
  return failures;
}
