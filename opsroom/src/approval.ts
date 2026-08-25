/**
 * Human approval gate.
 *
 * Reuses the flow GSO-1 already has for Claude Code: POST a request, then poll
 * until the operator taps Allow or Deny in Telegram. Same endpoints and same
 * env overrides as thecmanager/claude_perm_mcp.py, so there is one approval
 * mechanism on this machine, not two.
 *
 *   MANAGER_APPROVAL_AUTO   "allow" | "deny"  bypass Telegram (tests only)
 *   MANAGER_APPROVAL_URL    default http://127.0.0.1:8420
 *   MANAGER_APPROVAL_CHAT   Telegram chat id
 */

const BASE = process.env.MANAGER_APPROVAL_URL ?? "http://127.0.0.1:8420";
const CHAT = process.env.MANAGER_APPROVAL_CHAT ?? "";
const TIMEOUT_MS = Number(process.env.OPSROOM_APPROVAL_TIMEOUT_MS ?? 600_000);
const POLL_MS = 1200;

export interface Verdict {
  allowed: boolean;
  reason: string;
}

export class DeniedError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "DeniedError";
  }
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export async function requestApproval(
  toolName: string,
  toolInput: unknown,
): Promise<Verdict> {
  const auto = process.env.MANAGER_APPROVAL_AUTO;
  if (auto === "allow") return { allowed: true, reason: "auto-allowed (test mode)" };
  if (auto === "deny") return { allowed: false, reason: "auto-denied (test mode)" };

  if (!CHAT) {
    // create_approval() passes chat_id straight to the Telegram notifier and
    // swallows send failures, so an empty chat id looks identical to a human
    // ignoring the prompt. Refuse up front with a message that says what to fix.
    return {
      allowed: false,
      reason:
        "MANAGER_APPROVAL_CHAT is not set, so the approval prompt cannot be delivered. " +
        "Set it in <repo>/.env to the same chat id as MANAGER_TELEGRAM_ALLOWED.",
    };
  }

  let id: string;
  try {
    // GSO-1 sends the Telegram message synchronously inside this request and
    // its own send timeout is 15s, so measured latency is ~8s. A 10s budget
    // here fails intermittently; give it room.
    const res = await fetch(`${BASE}/api/claude/permission/request`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: CHAT, tool_name: toolName, tool_input: toolInput }),
      signal: AbortSignal.timeout(Number(process.env.OPSROOM_APPROVAL_POST_TIMEOUT_MS ?? 45_000)),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    id = String(((await res.json()) as { id: unknown }).id);
  } catch (err) {
    // Fail closed: if the human cannot be reached, do not run the command.
    return {
      allowed: false,
      reason: `could not create the approval request: ${(err as Error).message}`,
    };
  }

  const deadline = Date.now() + TIMEOUT_MS;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${BASE}/api/claude/permission/poll/${id}`, {
        signal: AbortSignal.timeout(10_000),
      });
      if (res.ok) {
        const decision = ((await res.json()) as { decision?: string }).decision;
        if (decision === "allow") return { allowed: true, reason: "approved by operator" };
        if (decision === "deny") return { allowed: false, reason: "denied by operator" };
      }
    } catch {
      /* transient, keep polling until the deadline */
    }
    await sleep(POLL_MS);
  }
  return { allowed: false, reason: "approval timed out" };
}

/** Throws DeniedError unless the operator approves. */
export async function requireApproval(toolName: string, toolInput: unknown): Promise<void> {
  const v = await requestApproval(toolName, toolInput);
  if (!v.allowed) throw new DeniedError(`${toolName} not run, ${v.reason}`);
}
