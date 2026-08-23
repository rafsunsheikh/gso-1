/**
 * M1 tools — read-only wrappers over GSO-1 endpoints that already exist.
 *
 * Deliberately NOT giving the agent bash or file access here. GSO-1's Python
 * already does these operations deterministically; the agent's job is to decide
 * *when* to call them, not to reimplement them in shell.
 */

import type { AgentTool } from "@earendil-works/pi-agent-core";
import { Type } from "typebox";
import { uptimeInfoTool } from "./uptime.ts";

const GSO1 = process.env.OPSROOM_GSO1_URL ?? "http://127.0.0.1:8420";
const TIMEOUT_MS = Number(process.env.OPSROOM_HTTP_TIMEOUT ?? 60000);

async function get(path: string): Promise<unknown> {
  const res = await fetch(`${GSO1}${path}`, { signal: AbortSignal.timeout(TIMEOUT_MS) });
  if (!res.ok) throw new Error(`GET ${path} -> ${res.status} ${res.statusText}`);
  return res.json();
}

function text(value: unknown) {
  return { content: [{ type: "text" as const, text: JSON.stringify(value, null, 2) }] };
}

/** Shrink the app list so 165+ repos don't blow the context window. */
function summariseApps(raw: unknown): Array<Record<string, unknown>> {
  const list = Array.isArray(raw)
    ? raw
    : ((raw as Record<string, unknown>)?.apps as unknown[]) ?? [];
  return list.map((a) => {
    const app = a as Record<string, unknown>;
    return {
      name: app.name,
      status: app.status ?? app.state,
      favourite: app.favourite ?? undefined,
      description:
        typeof app.description === "string" && app.description.length > 120
          ? `${app.description.slice(0, 120)}…`
          : app.description,
    };
  });
}

export const listAppsTool: AgentTool = {
  name: "list_apps",
  label: "List apps",
  description:
    "List every application GSO-1 knows about, with its run status. " +
    "Use this first to discover app names before calling git_status.",
  parameters: Type.Object({}),
  execute: async () => {
    const apps = summariseApps(await get("/api/apps"));
    return {
      content: [{ type: "text", text: JSON.stringify({ count: apps.length, apps }, null, 2) }],
      details: { count: apps.length },
    };
  },
};

export const gitStatusTool: AgentTool = {
  name: "git_status",
  label: "Git status",
  description:
    "Git state for ONE app: branch, changed_count (uncommitted files), " +
    "ahead/behind, last_commit, remote. Takes an app name from list_apps. " +
    "For a question spanning many repos use git_dirty_sweep instead.",
  parameters: Type.Object({
    name: Type.String({ description: "App name exactly as returned by list_apps" }),
  }),
  execute: async (_id, params) => {
    const { name } = params as { name: string };
    if (!name?.trim()) throw new Error("name is required");
    const g = (await get(`/api/apps/${encodeURIComponent(name)}/git`)) as Record<string, unknown>;
    // changed_files can be long; keep a sample so it cannot swamp the context.
    const files = Array.isArray(g.changed_files) ? (g.changed_files as string[]) : [];
    return text({
      ...g,
      changed_files: files.slice(0, 15),
      changed_files_truncated: files.length > 15 ? files.length - 15 : undefined,
    });
  },
};

/** Bounded-concurrency map — avoids 270 concurrent git subprocesses. */
async function mapPool<T, R>(items: T[], limit: number, fn: (t: T) => Promise<R>): Promise<R[]> {
  const out: R[] = new Array(items.length);
  let next = 0;
  await Promise.all(
    Array.from({ length: Math.min(limit, items.length) }, async () => {
      while (true) {
        const i = next++;
        if (i >= items.length) return;
        out[i] = await fn(items[i]);
      }
    }),
  );
  return out;
}

export const gitDirtySweepTool: AgentTool = {
  name: "git_dirty_sweep",
  label: "Git dirty sweep",
  description:
    "Check EVERY registered app's git state in one call and return only the " +
    "repositories that have uncommitted changes, or are ahead/behind their " +
    "remote. Use this to answer questions about many repos at once — it is far " +
    "cheaper than calling git_status repeatedly.",
  parameters: Type.Object({
    include_ahead_behind: Type.Optional(
      Type.Boolean({ description: "Also include repos that are clean but ahead/behind (default false)" }),
    ),
  }),
  execute: async (_id, params) => {
    const { include_ahead_behind = false } = (params ?? {}) as { include_ahead_behind?: boolean };
    const apps = summariseApps(await get("/api/apps"));
    const names = apps.map((a) => String(a.name));

    const results = await mapPool(names, 8, async (name) => {
      try {
        const g = (await get(`/api/apps/${encodeURIComponent(name)}/git`)) as Record<string, unknown>;
        return { name, ...g };
      } catch {
        return { name, is_repo: false, error: true };
      }
    });

    const repos = results.filter((r) => r.is_repo);
    const dirty = repos.filter(
      (r) =>
        r.dirty === true ||
        (include_ahead_behind && ((r.ahead as number) > 0 || (r.behind as number) > 0)),
    );

    return {
      content: [
        {
          type: "text",
          text: JSON.stringify(
            {
              scanned: names.length,
              git_repos: repos.length,
              dirty_count: dirty.length,
              dirty: dirty
                .map((r) => ({
                  name: r.name,
                  branch: r.branch,
                  changed_count: r.changed_count,
                  ahead: r.ahead,
                  behind: r.behind,
                }))
                .sort((a, b) => Number(b.changed_count ?? 0) - Number(a.changed_count ?? 0)),
            },
            null,
            2,
          ),
        },
      ],
      details: { scanned: names.length, dirty: dirty.length },
    };
  },
};

export const M1_TOOLS: AgentTool[] = [listAppsTool, gitStatusTool, gitDirtySweepTool, uptimeInfoTool];
