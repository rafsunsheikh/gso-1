/**
 * Connect Ops Room to Claude.
 *
 *   node src/login.ts anthropic          run the OAuth flow
 *   node src/login.ts anthropic --status what is stored, without reading it
 *   node src/login.ts anthropic --logout forget the credential
 *
 * Every line of stdout is one JSON event, because GSO-1 drives this from a
 * button rather than a terminal: it needs the authorisation URL as data it can
 * show and open, not as prose in a log. Events:
 *
 *   {"event":"auth_url","url":...}    open this to authorise
 *   {"event":"prompt","message":...}  the flow wants something typed back
 *   {"event":"info"|"progress",...}   narration, safe to display
 *   {"event":"done","provider":...}   stored
 *   {"event":"error","message":...}   failed, with the reason
 *
 * A prompt is answered by writing one line of JSON to stdin:
 * {"answer":"..."}. That is how the paste-the-code branch of the flow is
 * satisfied when no local callback server can win the race.
 */

import "./env.ts"; // must be first: loads <repo>/.env into process.env

import { createInterface } from "node:readline";
import { createStoredModels } from "./model.ts";
import { storedProviders, CREDENTIALS_FILE } from "./credentials.ts";

function emit(event: string, extra: Record<string, unknown> = {}): void {
  process.stdout.write(JSON.stringify({ event, ...extra }) + "\n");
}

/** Read one {"answer": "..."} line from stdin. */
function askLine(message: string, secret: boolean): Promise<string> {
  emit("prompt", { message, secret });
  return new Promise((resolve) => {
    const rl = createInterface({ input: process.stdin });
    rl.once("line", (line) => {
      rl.close();
      try {
        const parsed = JSON.parse(line);
        resolve(String(parsed?.answer ?? ""));
      } catch {
        resolve(line.trim());
      }
    });
  });
}

async function main(): Promise<number> {
  const args = process.argv.slice(2);
  const provider = (args.find((a) => !a.startsWith("--")) ?? "anthropic").trim();
  const wantStatus = args.includes("--status");
  const wantModels = args.includes("--models");
  const wantLogout = args.includes("--logout");

  if (wantModels) {
    const { anthropicModels } = await import("./model.ts");
    emit("models", { provider, models: anthropicModels() });
    return 0;
  }

  if (wantStatus) {
    // Metadata only. Never resolve or print the credential itself.
    emit("status", {
      provider,
      connected: storedProviders().includes(provider),
      stored: storedProviders(),
      file: CREDENTIALS_FILE,
    });
    return 0;
  }

  const models = createStoredModels();

  if (wantLogout) {
    await models.logout(provider);
    emit("done", { provider, connected: false });
    return 0;
  }

  try {
    await models.login(provider, "oauth", {
      prompt: async (p: any) => askLine(String(p?.message ?? "Enter the value"),
                                        p?.type === "secret"),
      notify: (e: any) => {
        if (e?.type === "auth_url") emit("auth_url", { url: e.url });
        else if (e?.type === "device_code") {
          emit("device_code", { code: e.userCode, url: e.verificationUri });
        } else if (e?.type === "progress") emit("progress", { message: e.message });
        else if (e?.type === "info") {
          emit("info", { message: e.message, links: e.links ?? [] });
        }
      },
    });
  } catch (err) {
    emit("error", { message: (err as Error)?.message ?? String(err) });
    return 1;
  }

  emit("done", { provider, connected: storedProviders().includes(provider) });
  return 0;
}

main().then(
  (code) => process.exit(code),
  (err) => {
    emit("error", { message: err?.message ?? String(err) });
    process.exit(1);
  },
);
