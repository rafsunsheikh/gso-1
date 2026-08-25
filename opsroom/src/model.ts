/**
 * Model wiring for Ops Room.
 *
 * The local llama-server is owned by GSO-1's llm.py, Ops Room only connects to
 * it. Never start or stop it from here; that would fight the existing manager.
 */

import { createModels, createProvider, type Model } from "@earendil-works/pi-ai";
import { openAICompletionsApi } from "@earendil-works/pi-ai/api/openai-completions.lazy";

export const LLAMA_BASE_URL = process.env.OPSROOM_LLAMA_URL ?? "http://127.0.0.1:8080/v1";
export const MODEL_ID = process.env.OPSROOM_MODEL ?? "glm-4.7-flash";

/** Must not exceed the ctx llama-server was started with (currently 65536). */
const CONTEXT_WINDOW = Number(process.env.OPSROOM_CTX ?? 65536);
const MAX_TOKENS = Number(process.env.OPSROOM_MAX_TOKENS ?? 4096);

const localModel: Model<"openai-completions"> = {
  id: MODEL_ID,
  name: "GLM-4.7-Flash (local llama.cpp)",
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

export function buildModels() {
  const models = createModels();
  models.setProvider(llamacpp);
  const model = models.getModel("llamacpp", MODEL_ID);
  if (!model) throw new Error(`model not found: llamacpp/${MODEL_ID}`);
  return { models, model };
}

/** Fail fast with a useful message when llama-server is not up. */
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

/** Throws when the served model is not the one configured. */
export async function assertModelMatches(): Promise<string> {
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
