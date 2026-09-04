/**
 * The plan tool: a checklist the agent keeps as it works.
 *
 * A multi-step job used to be a blank panel and a spinner. "Summarise every
 * dirty repo and push the ones that pass" is a minute of silence, and silence
 * is indistinguishable from a hang: the only honest thing the UI could say was
 * "thinking...". The agent knew what it intended to do and had no way to say so.
 *
 * The tool does nothing except record the plan. That is the point. It has no
 * side effects, cannot fail in a way that matters, and exists so the steps
 * become visible, both to the person watching and to the model, which plans
 * better when it has written the plan down.
 *
 * The plan reaches GSO-1 as a single marked line on stdout. The alternative was
 * a second channel, and stderr is already merged into stdout by the bridge, so
 * a line with a prefix nothing else emits is the smallest thing that works.
 */

import type { AgentTool } from "@earendil-works/pi-agent-core";
import { Type } from "typebox";

/** Recognised by thecmanager/opsroom.py, which turns it into an SSE event. */
export const PLAN_MARKER = "@@GSO1_PLAN ";

const STATUSES = ["pending", "in_progress", "completed"] as const;
type Status = (typeof STATUSES)[number];

export type PlanStep = { step: string; status: Status };

/**
 * Validate a plan, or explain what is wrong with it.
 *
 * Returned rather than thrown so a malformed plan costs the agent a correction
 * and not the run: the plan is commentary on the work, and failing the turn
 * because the commentary was misshapen would be the tail wagging the dog.
 */
export function readPlan(raw: unknown): { steps: PlanStep[] } | { error: string } {
  if (!Array.isArray(raw) || raw.length === 0) {
    return { error: "plan must be a non-empty array of {step, status}" };
  }
  const steps: PlanStep[] = [];
  for (let i = 0; i < raw.length; i++) {
    const entry = raw[i] as Record<string, unknown>;
    if (!entry || typeof entry !== "object") {
      return { error: `plan[${i}] must be an object` };
    }
    const step = typeof entry.step === "string" ? entry.step.trim() : "";
    const status = typeof entry.status === "string" ? entry.status.trim() : "";
    if (!step) return { error: `plan[${i}].step is required` };
    if (!STATUSES.includes(status as Status)) {
      return { error: `plan[${i}].status must be one of ${STATUSES.join(", ")}` };
    }
    steps.push({ step: step.slice(0, 160), status: status as Status });
  }
  if (steps.length > 20) return { error: "a plan of more than 20 steps is not a plan" };
  // One thing at a time. Without this the model marks everything in_progress
  // at once and the panel stops meaning anything.
  const running = steps.filter((s) => s.status === "in_progress").length;
  if (running > 1) return { error: "at most one step may be in_progress" };
  return { steps };
}

export const updatePlanTool: AgentTool = {
  name: "update_plan",
  label: "Update plan",
  description:
    "Record or revise your plan for a multi-step task, so the operator can see " +
    "what you intend to do and where you are. Call it once when you have a plan " +
    "of three or more steps, and again each time a step finishes or the plan " +
    "changes. Exactly one step may be in_progress. Do not use it for work you " +
    "will finish in a single tool call.",
  parameters: Type.Object({
    plan: Type.Array(
      Type.Object({
        step: Type.String({ description: "Short description of the step." }),
        status: Type.Union(STATUSES.map((s) => Type.Literal(s)), {
          description: 'One of "pending", "in_progress", "completed".',
        }),
      }),
      { description: "The full plan, every step, in order. Not just the changes." },
    ),
    note: Type.Optional(Type.String({ description: "Optional one line on what changed." })),
  }),
  execute: async (_id, args) => {
    const params = (args ?? {}) as Record<string, unknown>;
    const parsed = readPlan(params.plan);
    if ("error" in parsed) {
      return {
        content: [{ type: "text", text: `plan rejected: ${parsed.error}` }],
        details: { status: "rejected", reason: parsed.error },
      };
    }
    const note = typeof params.note === "string" ? params.note.trim().slice(0, 200) : "";
    const payload = { plan: parsed.steps, ...(note ? { note } : {}) };

    // On its own line: the assistant's prose is streamed without newlines, so
    // an unterminated marker would be spliced into whatever it was saying.
    process.stdout.write(`\n${PLAN_MARKER}${JSON.stringify(payload)}\n`);

    const done = parsed.steps.filter((s) => s.status === "completed").length;
    return {
      content: [{
        type: "text",
        text: `Plan recorded: ${done} of ${parsed.steps.length} steps complete.`,
      }],
      details: { status: "updated", ...payload },
    };
  },
};

export const PLAN_TOOLS: AgentTool[] = [updatePlanTool];
