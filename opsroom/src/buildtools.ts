/**
 * Self-build tools, the M4 loop.
 *
 * The agent edits the repo working tree with `write`/`edit` (its sandbox), then
 * drives the supervisor through these tools to snapshot, verify, and promote.
 *
 * The agent never writes into var/ directly, that path is immutable to its
 * file tools. Only the supervisor mutates release state, and the supervisor
 * itself is immutable to the agent. That is the invariant that lets a bad
 * self-edit be undone: the thing that rolls back is never the thing replaced.
 *
 * promote and rollback change what runs on the machine, so both are gated on
 * human approval (plan invariant #6).
 */

import { spawn } from "node:child_process";
import path from "node:path";

import type { AgentTool } from "@earendil-works/pi-agent-core";
import { Type } from "typebox";

import { requireApproval } from "./approval.ts";
import { SANDBOX_ROOT } from "./policy.ts";
import { diskFreeTool } from "./disktool.ts";

const PY = process.env.OPSROOM_PYTHON ?? path.join(SANDBOX_ROOT, ".venv/bin/python");
const TIMEOUT_MS = Number(process.env.OPSROOM_BUILD_TIMEOUT_MS ?? 180_000);

interface Ran {
  code: number;
  stdout: string;
  stderr: string;
}

/** Run `python -m supervisor <args>` in the repo and capture output. */
function supervisor(args: string[], timeoutMs = TIMEOUT_MS): Promise<Ran> {
  return new Promise((resolve, reject) => {
    const child = spawn(PY, ["-m", "supervisor", ...args], {
      cwd: SANDBOX_ROOT,
      env: { ...process.env },
    });
    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      reject(new Error(`supervisor ${args[0]} timed out after ${timeoutMs}ms`));
    }, timeoutMs);

    child.stdout.on("data", (d) => (stdout += d));
    child.stderr.on("data", (d) => (stderr += d));
    child.on("error", (e) => {
      clearTimeout(timer);
      reject(e);
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      resolve({ code: code ?? -1, stdout: stdout.trim(), stderr: stderr.trim() });
    });
  });
}

function text(s: string) {
  return { content: [{ type: "text" as const, text: s }] };
}

export const buildReleaseTool: AgentTool = {
  name: "build_release",
  label: "Build release",
  description:
    "Snapshot the current working tree into a new, unpromoted release and return " +
    "its stamp. Nothing that is running changes. Call this after editing files, " +
    "then verify_release, then promote_release.",
  parameters: Type.Object({}),
  execute: async () => {
    const r = await supervisor(["release", "create"]);
    if (r.code !== 0) throw new Error(`build_release failed: ${r.stderr || r.stdout}`);
    const stamp = r.stdout.split("\n").pop()?.trim();
    if (!stamp) throw new Error("build_release produced no release stamp");
    return {
      content: [{ type: "text", text: `Built release ${stamp} (not yet promoted).` }],
      details: { stamp },
    };
  },
};

export const verifyReleaseTool: AgentTool = {
  name: "verify_release",
  label: "Verify release",
  description:
    "Check that a release actually works before promoting it: boots GSO-1 on a " +
    "scratch port and health-checks it, and runs the sidecar self-check so a " +
    "broken edit to Ops Room's own code is caught. Promotes nothing. " +
    "A release that fails verification MUST NOT be promoted.",
  parameters: Type.Object({
    stamp: Type.String({ description: "Release stamp from build_release, or 'latest'" }),
  }),
  execute: async (_id, params) => {
    const { stamp } = (params ?? {}) as { stamp?: string };
    if (!stamp?.trim()) throw new Error("stamp is required");
    const r = await supervisor(["verify", stamp.trim()]);
    const detail = [r.stdout, r.stderr].filter(Boolean).join("\n").slice(-1200);
    if (r.code !== 0) {
      return {
        content: [{ type: "text", text: `VERIFY FAILED for ${stamp}. Do not promote.\n\n${detail}` }],
        details: { stamp, passed: false },
      };
    }
    return {
      content: [{ type: "text", text: `VERIFY PASSED for ${stamp}.\n\n${detail}` }],
      details: { stamp, passed: true },
    };
  },
};

export const promoteReleaseTool: AgentTool = {
  name: "promote_release",
  label: "Promote release",
  description:
    "Make a verified release the live one and restart the app. Requires human " +
    "approval. Only call this after verify_release has PASSED for this exact stamp.",
  parameters: Type.Object({
    stamp: Type.String({ description: "Release stamp that passed verification" }),
  }),
  execute: async (_id, params) => {
    const { stamp } = (params ?? {}) as { stamp?: string };
    if (!stamp?.trim()) throw new Error("stamp is required");
    await requireApproval("promote_release", { stamp });
    const r = await supervisor(["promote", stamp.trim()]);
    if (r.code !== 0) throw new Error(`promote failed: ${r.stderr || r.stdout}`);
    return text(`Promoted ${stamp}. ${r.stdout}`);
  },
};

export const rollbackReleaseTool: AgentTool = {
  name: "rollback_release",
  label: "Roll back",
  description:
    "Revert to the previously running release. Requires human approval. " +
    "Use when a promoted release turns out to be broken.",
  parameters: Type.Object({}),
  execute: async () => {
    await requireApproval("rollback_release", {});
    const r = await supervisor(["rollback"]);
    if (r.code !== 0) throw new Error(`rollback failed: ${r.stderr || r.stdout}`);
    return text(r.stdout || "Rolled back.");
  },
};

export const releaseStatusTool: AgentTool = {
  name: "release_status",
  label: "Release status",
  description:
    "Show which release is live, which is the rollback target, how many exist, " +
    "and whether the app is currently healthy.",
  parameters: Type.Object({}),
  execute: async () => {
    const r = await supervisor(["status"], 30_000);
    return text(r.stdout || r.stderr || "(no output)");
  },
};

export const M4_TOOLS: AgentTool[] = [
  buildReleaseTool,
  verifyReleaseTool,
  promoteReleaseTool,
  rollbackReleaseTool,
  releaseStatusTool,
  diskFreeTool,
];
