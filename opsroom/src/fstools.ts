/**
 * pi's built-in file and shell tools, constrained by policy.
 *
 * We do NOT fork pi. Each tool is built through its factory with custom
 * `operations` that enforce the path policy, plus an execute-level guard as
 * defence in depth (find shells out to `fd`, so its operations layer alone is
 * not a reliable choke point).
 *
 * bash additionally requires human approval, it is the one tool that can do
 * anything, so it is gated rather than path-restricted.
 */

import { constants as fsConstants } from "node:fs";
import {
  access as fsAccess,
  mkdir as fsMkdir,
  readFile as fsReadFile,
  readdir as fsReaddir,
  stat as fsStat,
  writeFile as fsWriteFile,
} from "node:fs/promises";

import type { AgentTool } from "@earendil-works/pi-agent-core";
import {
  createBashTool,
  createEditTool,
  createGrepTool,
  createLsTool,
  createReadTool,
  createWriteTool,
} from "@earendil-works/pi-coding-agent";

import { requireApproval } from "./approval.ts";
import { assertParamPaths, assertReadable, assertWritable, SANDBOX_ROOT } from "./policy.ts";

/** Wrap a tool's execute with a path-param guard. */
function guardParams(tool: AgentTool, mode: "read" | "write"): AgentTool {
  const inner = tool.execute.bind(tool);
  return {
    ...tool,
    execute: async (id, params, signal, onUpdate) => {
      assertParamPaths(params as Record<string, unknown>, mode);
      return inner(id, params, signal, onUpdate);
    },
  };
}

/** Wrap a tool's execute with a human approval gate. */
function guardApproval(tool: AgentTool): AgentTool {
  const inner = tool.execute.bind(tool);
  return {
    ...tool,
    execute: async (id, params, signal, onUpdate) => {
      await requireApproval(tool.name, params);
      return inner(id, params, signal, onUpdate);
    },
  };
}

// ------------------------------------------------------------------ read ring

const readTool = createReadTool(SANDBOX_ROOT, {
  operations: {
    readFile: async (p) => fsReadFile(assertReadable(p)),
    access: async (p) => fsAccess(assertReadable(p), fsConstants.R_OK),
  },
});

const lsTool = createLsTool(SANDBOX_ROOT, {
  operations: {
    exists: async (p) => {
      try {
        await fsAccess(assertReadable(p));
        return true;
      } catch {
        return false;
      }
    },
    stat: async (p) => fsStat(assertReadable(p)),
    readdir: async (p) => fsReaddir(assertReadable(p)),
  },
});

const grepTool = createGrepTool(SANDBOX_ROOT, {
  operations: {
    isDirectory: async (p) => (await fsStat(assertReadable(p))).isDirectory(),
    readFile: async (p) => fsReadFile(assertReadable(p), "utf-8"),
  },
});

// ----------------------------------------------------------------- write ring

const writeTool = createWriteTool(SANDBOX_ROOT, {
  operations: {
    writeFile: async (p, content) => fsWriteFile(assertWritable(p), content, "utf-8"),
    mkdir: async (dir) => {
      await fsMkdir(assertWritable(dir), { recursive: true });
    },
  },
});

const editTool = createEditTool(SANDBOX_ROOT, {
  operations: {
    // Editing requires reading the original, but the write target must still be
    // inside the sandbox, so both checks apply.
    readFile: async (p) => fsReadFile(assertWritable(p)),
    writeFile: async (p, content) => fsWriteFile(assertWritable(p), content, "utf-8"),
    access: async (p) => fsAccess(assertWritable(p), fsConstants.R_OK | fsConstants.W_OK),
  },
});

// ------------------------------------------------------------------ bash ring

const bashTool = createBashTool(SANDBOX_ROOT, {
  // Anchor the shell in the sandbox; the approval gate is the real control.
  spawnHook: (ctx) => ({ ...ctx, cwd: ctx.cwd || SANDBOX_ROOT }),
});

// --------------------------------------------------------------------- export

export const READ_TOOLS: AgentTool[] = [
  guardParams(readTool, "read"),
  guardParams(lsTool, "read"),
  guardParams(grepTool, "read"),
];

export const WRITE_TOOLS: AgentTool[] = [
  guardParams(writeTool, "write"),
  guardParams(editTool, "write"),
];

/** bash is approval-gated, not path-gated, it can reach anything. */
export const SHELL_TOOLS: AgentTool[] = [guardApproval(bashTool)];

/**
 * bash is OFF by default (set OPSROOM_ENABLE_BASH=1 to include it).
 *
 * Measured 2026-08-22: GLM-4.7-Flash reaches for `bash` even when the prompt
 * explicitly names the `write` tool, it answered "write M2-OK to <path>" with
 * `echo -n 'M2-OK' > <path>`. With bash present, ordinary file edits therefore
 * stall on a human approval prompt, which defeats the point of having
 * purpose-built tools. Keeping it opt-in makes the safe path the default one.
 */
export const BASH_ENABLED = process.env.OPSROOM_ENABLE_BASH === "1";

export const M2_TOOLS: AgentTool[] = [
  ...READ_TOOLS,
  ...WRITE_TOOLS,
  ...(BASH_ENABLED ? SHELL_TOOLS : []),
];
