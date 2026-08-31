/**
 * Model wiring for Ops Room.
 *
 * Two providers, chosen at run time:
 *
 *   local     llama-server on this machine, free and private, owned by GSO-1's
 *             llm.py. Ops Room only connects to it; starting or stopping it
 *             from here would fight the existing manager.
 *   anthropic Claude, over the Anthropic Messages API. Stronger, and not free.
 *
 * The tools, the sandbox in policy.ts and the build/verify/promote sequence are
 * identical either way: only the model changes. That is deliberate. The tools
 * were written against a small local model and the guarantee that matters, that
 * the agent cannot touch the supervisor that rolls back its mistakes, comes
 * from policy.ts rather than from which model is answering.
 */

import { createModels, createProvider, type Model } from "@earendil-works/pi-ai";
import { openAICompletionsApi } from "@earendil-works/pi-ai/api/openai-completions.lazy";
import { anthropicProvider } from "@earendil-works/pi-ai/providers/anthropic";
import { createFileCredentialStore } from "./credentials.ts";
import { opsSetting } from "./settings.ts";

export type ProviderId = "local" | "anthropic";

/** Which brain answers. `local` keeps the previous behaviour exactly.
 *
 *  Read from settings.json when the environment does not say, so that choosing
 *  Claude in the app also applies to `./ops` typed in a terminal. */
export const PROVIDER: ProviderId =
  opsSetting("provider", "OPSROOM_PROVIDER") === "anthropic" ? "anthropic" : "local";

export const LLAMA_BASE_URL = process.env.OPSROOM_LLAMA_URL ?? "http://127.0.0.1:8080/v1";
export const MODEL_ID = process.env.OPSROOM_MODEL ?? "glm-4.7-flash";

/** Must not exceed the ctx llama-server was started with. */
const CONTEXT_WINDOW = Number(process.env.OPSROOM_CTX ?? 65536);
const MAX_TOKENS = Number(process.env.OPSROOM_MAX_TOKENS ?? 4096);

// Sonnet rather than Opus by default: the Ops Room's turns are tool calls over
// a local machine's state, where the ceiling is rarely reasoning depth, and the
// user is paying for every one of them.
export const ANTHROPIC_MODEL =
  opsSetting("model", "OPSROOM_ANTHROPIC_MODEL") ?? "claude-sonnet-5";

const localModel: Model<"openai-completions"> = {
  id: MODEL_ID,
  name: `${MODEL_ID} (local llama.cpp)`,
  api: "openai-completions",
  provider: "llamacpp",
  baseUrl: LLAMA_BASE_URL,
  reasoning: false,
  input: ["text"],
  cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
  contextWindow: CONTEXT_WINDOW,
  maxTokens: MAX_TOKENS,
};

const llamacpp = createProvider({
  id: "llamacpp",
  name: "llama.cpp (local)",
  baseUrl: LLAMA_BASE_URL,
  // llama-server needs no credential, but pi-ai requires either an apiKey or an
  // auth header and throws "No API key for provider" otherwise. Supply a
  // placeholder; llama-server ignores it.
  auth: {
    apiKey: {
      name: "llama.cpp (local, keyless)",
      resolve: async () => ({ auth: { apiKey: "local" } }),
    },
  },
  models: [localModel],
  api: openAICompletionsApi(),
});

/** A Models collection backed by the on-disk credential store.
 *
 *  Separate from buildModels so the login flow can use the same store without
 *  needing a model, and so a token refreshed during one run is visible to the
 *  next: Ops Room is a fresh process per question. */
export function createStoredModels() {
  const models = createModels({ credentials: createFileCredentialStore() });
  models.setProvider(llamacpp);
  models.setProvider(anthropicProvider());
  return models;
}

export function buildModels(provider: ProviderId = PROVIDER) {
  const models = createStoredModels();
  const [providerId, modelId] = provider === "anthropic"
    ? ["anthropic", ANTHROPIC_MODEL]
    : ["llamacpp", MODEL_ID];
  const model = models.getModel(providerId, modelId);
  if (!model) {
    throw new Error(
      `model not found: ${providerId}/${modelId}` +
      (provider === "anthropic"
        ? `\nSet OPSROOM_ANTHROPIC_MODEL to a model this version of pi-ai knows.`
        : ""),
    );
  }
  return { models, model, providerId, modelId };
}

/** Every Claude model this build knows, for the picker in GSO-1. */
export function anthropicModels(): Array<{ id: string; contextWindow: number }> {
  const models = createStoredModels();
  const list = (models as any).getModels?.("anthropic") ?? [];
  return list.map((m: any) => ({ id: m.id, contextWindow: m.contextWindow }));
}

/** Describe the configured brain for logs and for the dock header. */
export function describeModel(): string {
  return PROVIDER === "anthropic"
    ? `${ANTHROPIC_MODEL} (Anthropic)`
    : `${MODEL_ID} @ ${LLAMA_BASE_URL}`;
}

/**
 * Fail fast with a useful message when the chosen brain is not usable.
 *
 * The two providers fail in completely different ways, and saying
 * "llama-server is not reachable" to somebody who chose Claude sends them to
 * the wrong screen entirely.
 */
export async function assertReady(): Promise<void> {
  if (PROVIDER === "anthropic") {
    const models = createStoredModels();
    const stored = await models.getAuth?.("anthropic").catch(() => null);
    if (!stored && !process.env.ANTHROPIC_API_KEY && !process.env.ANTHROPIC_OAUTH_TOKEN) {
      throw new Error(
        "Ops Room is set to use Claude, but no Anthropic credential is stored.\n" +
        "Connect it in GSO-1: Settings, then Ops Room, then Connect Claude.",
      );
    }
    return;
  }
  await assertServerUp();
}

export async function assertServerUp(): Promise<void> {
  const url = LLAMA_BASE_URL.replace(/\/v1\/?$/, "") + "/health";
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(5000) });
    if (!res.ok) throw new Error(`health ${res.status}`);
  } catch (err) {
    throw new Error(
      `llama-server is not reachable at ${LLAMA_BASE_URL}\n` +
        `Start it from GSO-1: POST /api/llm/start (or the Local LLM view).\n` +
        `Cause: ${(err as Error).message}`,
    );
  }
}

/**
 * Report which model llama-server is actually serving.
 *
 * llama-server ignores the `model` field in a request and serves whatever is
 * loaded, so MODEL_ID here is only a label. On 2026-08-22 the server was
 * swapped from GLM to Qwen mid-milestone and nothing noticed for hours,
 * behavioural findings recorded in that window could not be attributed to a
 * model. Surface the mismatch instead of silently talking to the wrong one.
 */
export async function servedModel(): Promise<string | null> {
  try {
    const res = await fetch(`${LLAMA_BASE_URL}/models`, { signal: AbortSignal.timeout(5000) });
    if (!res.ok) return null;
    const body = (await res.json()) as { models?: Array<{ name?: string; model?: string }>; data?: Array<{ id?: string }> };
    return (
      body.models?.[0]?.name ??
      body.models?.[0]?.model ??
      body.data?.[0]?.id ??
      null
    );
  } catch {
    return null;
  }
}

/** Throws when the served model is not the one configured.
 *
 *  Only meaningful for the local provider: Anthropic serves the model you ask
 *  for, so there is no mismatch to detect. */
export async function assertModelMatches(): Promise<string> {
  if (PROVIDER === "anthropic") return ANTHROPIC_MODEL;
  const served = await servedModel();
  if (!served) return "(server did not report a model)";
  if (served !== MODEL_ID && !served.toLowerCase().includes(MODEL_ID.toLowerCase())) {
    throw new Error(
      `model mismatch: configured OPSROOM_MODEL="${MODEL_ID}" but llama-server is serving "${served}".\n` +
        `llama-server serves whatever is loaded regardless of the requested name.\n` +
        `Either load the right model via GSO-1 /api/llm/start, or set OPSROOM_MODEL="${served}".`,
    );
  }
  return served;
}
