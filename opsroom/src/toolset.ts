/**
 * The one list of tools the agent gets.
 *
 * It was assembled inline in ask.ts and again, differently, in selfcheck.ts.
 * Adding `update_plan` to the first and not the second meant verification
 * cheerfully reported sixteen tools while the agent ran with seventeen: the
 * check that exists to prove a release works had no idea one of its tools was
 * there, and could not have noticed it breaking.
 *
 * Anything that hands tools to the agent imports from here.
 */

import type { AgentTool } from "@earendil-works/pi-agent-core";

import { M1_TOOLS } from "./tools.ts";
import { M2_TOOLS } from "./fstools.ts";
import { M3_TOOLS } from "./websearch.ts";
import { M4_TOOLS } from "./buildtools.ts";
import { PLAN_TOOLS } from "./plantool.ts";
import { SKILL_TOOLS } from "./skills.ts";

export function allTools(): AgentTool[] {
  return [...M1_TOOLS, ...M2_TOOLS, ...M3_TOOLS, ...M4_TOOLS, ...PLAN_TOOLS,
          ...SKILL_TOOLS];
}

/** Tools with real side effects, which the smoke test must not simply run. */
export function smokeableTools(): AgentTool[] {
  return [...M1_TOOLS, ...M2_TOOLS, ...M4_TOOLS];
}
