/**
 * Skills: capability as documents rather than code.
 *
 * Every tool the Ops Room has is TypeScript compiled into the sidecar, so
 * teaching it to use `gh` or `docker` meant writing a tool, rebuilding, and
 * cutting a release. Most of what such a tool would contain is not code at
 * all: it is knowledge about which command to run and what its output means.
 * The agent already has a shell.
 *
 * A skill is therefore a folder with a SKILL.md in it. The frontmatter says
 * what the skill is for and which binaries it needs; the body is instructions
 * written for the model. Adding one is adding a file.
 *
 * Two rules make this safe to put in a prompt:
 *
 * * **A skill is never offered when it cannot work.** Each declares its
 *   binaries and they are checked on this machine. Handing the model
 *   instructions for a `gh` it does not have produces a confident attempt and a
 *   command-not-found, which is worse than not mentioning GitHub at all.
 * * **Only the index goes in the prompt.** Names and one-line descriptions
 *   cost a few hundred tokens; the bodies are read on demand through the tool.
 *   A local model's context is small and a dozen full skills would fill it.
 *
 * Skill bodies are instructions the model will act on, so they are trusted
 * input: bundled ones ship with the release, and a user's own live in their
 * data directory, which is theirs. Nothing here reads a skill from anywhere
 * else.
 */

import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import type { AgentTool } from "@earendil-works/pi-agent-core";
import { Type } from "typebox";

import { dataDir } from "./settings.ts";

export type Skill = {
  name: string;
  description: string;
  requires: string[];
  missing: string[];
  available: boolean;
  source: "bundled" | "user";
  dir: string;
};

/** Bundled skills travel with the sidecar; a release snapshot carries them. */
const BUNDLED = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "skills");
/** A user's own, which survive updates because they are not in the release. */
const USER = () => path.join(dataDir(), "skills");

/**
 * Minimal frontmatter reader: `key: value` and `key: [a, b]` between `---`
 * fences. Not YAML, deliberately. A skill file is written by hand and read at
 * startup, and pulling in a parser for three fields is how a small thing
 * becomes a dependency.
 */
function parseFrontmatter(text: string): { meta: Record<string, string | string[]>; body: string } {
  const m = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?([\s\S]*)$/.exec(text);
  if (!m) return { meta: {}, body: text };
  const meta: Record<string, string | string[]> = {};
  for (const line of m[1].split(/\r?\n/)) {
    const kv = /^([A-Za-z_][\w-]*)\s*:\s*(.*)$/.exec(line.trim());
    if (!kv) continue;
    const key = kv[1];
    let value = kv[2].trim();
    if (value.startsWith("[") && value.endsWith("]")) {
      meta[key] = value
        .slice(1, -1)
        .split(",")
        .map((s) => s.trim().replace(/^['"]|['"]$/g, ""))
        .filter(Boolean);
    } else {
      meta[key] = value.replace(/^['"]|['"]$/g, "");
    }
  }
  return { meta, body: m[2] };
}

const _binCache = new Map<string, boolean>();

/** Is this binary on the machine? Cached: a skill list checks the same few. */
export function hasBin(bin: string): boolean {
  if (!/^[\w.+-]+$/.test(bin)) return false;   // never hand a shell a wildcard
  const hit = _binCache.get(bin);
  if (hit !== undefined) return hit;
  let ok = false;
  try {
    execFileSync("/usr/bin/which", [bin], { stdio: "ignore" });
    ok = true;
  } catch {
    ok = false;
  }
  _binCache.set(bin, ok);
  return ok;
}

function readSkill(dir: string, source: Skill["source"]): Skill | null {
  const file = path.join(dir, "SKILL.md");
  if (!existsSync(file)) return null;
  let text: string;
  try {
    text = readFileSync(file, "utf8");
  } catch {
    return null;
  }
  const { meta } = parseFrontmatter(text);
  const name = String(meta.name || path.basename(dir)).trim();
  const description = String(meta.description || "").trim();
  const requires = Array.isArray(meta.requires)
    ? meta.requires.map(String)
    : meta.requires
      ? [String(meta.requires)]
      : [];
  const missing = requires.filter((b) => !hasBin(b));
  return {
    name,
    description,
    requires,
    missing,
    available: missing.length === 0,
    source,
    dir,
  };
}

function scan(root: string, source: Skill["source"]): Skill[] {
  let entries: string[];
  try {
    entries = readdirSync(root);
  } catch {
    return [];
  }
  const out: Skill[] = [];
  for (const entry of entries.sort()) {
    const dir = path.join(root, entry);
    try {
      if (!statSync(dir).isDirectory()) continue;
    } catch {
      continue;
    }
    const skill = readSkill(dir, source);
    if (skill?.name) out.push(skill);
  }
  return out;
}

/** Every skill on this machine. A user skill shadows a bundled one by name,
 *  so somebody can replace what ships without editing the release. */
export function listSkills(): Skill[] {
  const byName = new Map<string, Skill>();
  for (const s of scan(BUNDLED, "bundled")) byName.set(s.name, s);
  for (const s of scan(USER(), "user")) byName.set(s.name, s);
  return [...byName.values()].sort((a, b) => a.name.localeCompare(b.name));
}

export function readSkillBody(name: string): string | null {
  const skill = listSkills().find((s) => s.name === name);
  if (!skill) return null;
  try {
    return parseFrontmatter(readFileSync(path.join(skill.dir, "SKILL.md"), "utf8")).body.trim();
  } catch {
    return null;
  }
}

/**
 * The lines that go in the system prompt.
 *
 * Unavailable skills are named rather than hidden, with what they need, so the
 * agent can tell somebody "I could do that if you installed gh" instead of
 * either failing at it or pretending the capability does not exist.
 */
export function skillsPromptSection(): string {
  const skills = listSkills();
  if (!skills.length) return "";
  const ready = skills.filter((s) => s.available);
  const blocked = skills.filter((s) => !s.available);
  const lines = ["", "Skills. Instructions for things this machine can do. Read one with the",
    "`skill` tool before attempting the work it covers; the descriptions below are",
    "only enough to choose."];
  if (ready.length) {
    lines.push("", "Available:");
    for (const s of ready) lines.push(`- ${s.name}: ${s.description}`);
  }
  if (blocked.length) {
    lines.push("", "Not usable here, the command is not installed. Say so rather than trying:");
    for (const s of blocked) lines.push(`- ${s.name}: needs ${s.missing.join(", ")}`);
  }
  return lines.join("\n");
}

export const skillTool: AgentTool = {
  name: "skill",
  label: "Skill",
  description:
    "Read the instructions for one of the skills listed in your system prompt, " +
    "or list them again. Read a skill before doing the work it covers: it tells " +
    "you the exact commands and what their output means.",
  parameters: Type.Object({
    action: Type.Optional(
      Type.Union([Type.Literal("read"), Type.Literal("list")], {
        description: 'Defaults to "read" when a name is given.',
      }),
    ),
    name: Type.Optional(Type.String({ description: "Which skill to read." })),
  }),
  execute: async (_id, args) => {
    const params = (args ?? {}) as Record<string, unknown>;
    const name = typeof params.name === "string" ? params.name.trim() : "";
    const action = params.action === "list" || !name ? "list" : "read";

    if (action === "list") {
      const skills = listSkills().map((s) => ({
        name: s.name,
        description: s.description,
        available: s.available,
        ...(s.available ? {} : { needs: s.missing }),
      }));
      return {
        content: [{ type: "text", text: JSON.stringify({ skills }, null, 2) }],
        details: { count: skills.length },
      };
    }

    const skill = listSkills().find((s) => s.name === name);
    if (!skill) {
      const known = listSkills().map((s) => s.name).join(", ") || "none";
      return {
        content: [{ type: "text", text: `No skill called "${name}". Known: ${known}` }],
        details: { status: "not_found" },
      };
    }
    if (!skill.available) {
      // Reading it anyway would let the model follow instructions for a command
      // that is not there, which ends in command-not-found and a wasted turn.
      return {
        content: [{
          type: "text",
          text: `The "${skill.name}" skill needs ${skill.missing.join(", ")}, which `
            + `${skill.missing.length === 1 ? "is" : "are"} not installed on this machine. `
            + `Tell the operator that rather than attempting it.`,
        }],
        details: { status: "unavailable", missing: skill.missing },
      };
    }
    const body = readSkillBody(name);
    if (!body) {
      return {
        content: [{ type: "text", text: `Could not read the "${name}" skill.` }],
        details: { status: "unreadable" },
      };
    }
    return {
      content: [{ type: "text", text: body }],
      details: { status: "ok", name, source: skill.source },
    };
  },
};

export const SKILL_TOOLS: AgentTool[] = [skillTool];
export const SKILL_DIRS = () => ({ bundled: BUNDLED, user: USER() });
