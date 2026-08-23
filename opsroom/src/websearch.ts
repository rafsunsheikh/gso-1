/**
 * web_search — Tavily Search API.
 *
 * Design decisions come from the survey of codex / gemini-cli / qwen-code /
 * opencode / openclaw (2026-08-22):
 *
 * - Provider-hosted search (codex, gemini-cli) is unavailable to us: llama.cpp
 *   has no grounding, so the harness must call a search API itself.
 * - DuckDuckGo scraping was rejected. openclaw's reference implementation ships
 *   an isBotChallenge() checking for recaptcha and "are you a human"; that
 *   function exists because it happens, and M6 runs unattended.
 * - Brave was the first choice but its allowance is a $5/month credit behind a
 *   billing account. Tavily gives 1,000 credits/month with no card, and returns
 *   pre-digested content, which suits a small local context.
 * - Results are wrapped as untrusted (gemini-cli's wrapUntrusted). This matters
 *   more here than there: Ops Room can write to its own source and, at M4,
 *   rebuild and relaunch itself, so a snippet is attacker-controlled text.
 * - Caps are far tighter than the other agents use (opencode defaults to 10,000
 *   context chars) because the local model may be running at ctx 8192.
 *
 * API shape verified against docs.tavily.com on 2026-08-22:
 *   POST https://api.tavily.com/search
 *   Authorization: Bearer tvly-...
 *   -> { results: [{ title, url, content, score }], answer?, usage }
 */

import type { AgentTool } from "@earendil-works/pi-agent-core";
import { Type } from "typebox";

const ENDPOINT = (process.env.OPSROOM_TAVILY_URL ?? "https://api.tavily.com") + "/search";

const DEFAULT_COUNT = Number(process.env.OPSROOM_SEARCH_COUNT ?? 5);
const MAX_COUNT = 10; // Tavily allows 20; we cap lower for context budget.
/** Per-result snippet cap. 5 x ~200 chars keeps the payload near 400 tokens. */
const SNIPPET_CHARS = Number(process.env.OPSROOM_SEARCH_SNIPPET_CHARS ?? 200);
const ANSWER_CHARS = Number(process.env.OPSROOM_SEARCH_ANSWER_CHARS ?? 600);
const TIMEOUT_MS = Number(process.env.OPSROOM_SEARCH_TIMEOUT_MS ?? 25_000);

/**
 * Mark model-visible text as attacker-controlled.
 * Escaping the closing tag matters — without it the wrapper is trivially
 * escaped by content that simply includes the closing tag.
 */
export function wrapUntrusted(text: string): string {
  const escaped = text.replaceAll("</untrusted_context>", "&lt;/untrusted_context&gt;");
  return `<untrusted_context>\n${escaped}\n</untrusted_context>`;
}

function clean(s: string): string {
  return s
    .replace(/<[^>]+>/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function truncate(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n).trimEnd()}…` : s;
}

interface TavilyResult {
  title?: string;
  url?: string;
  content?: string;
  score?: number;
}

export const webSearchTool: AgentTool = {
  name: "web_search",
  label: "Web search",
  description:
    "Search the web for current information beyond the model's knowledge cutoff. " +
    "Returns a short synthesized answer plus title, URL and snippet per result — " +
    `not full page text. Returns ${DEFAULT_COUNT} results by default. Always cite the URL.`,
  parameters: Type.Object({
    query: Type.String({ description: "The search query" }),
    count: Type.Optional(
      Type.Number({ description: `Number of results (default ${DEFAULT_COUNT}, max ${MAX_COUNT})` }),
    ),
    topic: Type.Optional(
      Type.String({ description: "'general' (default) or 'news' for recent events" }),
    ),
    time_range: Type.Optional(
      Type.String({ description: "Limit result age: day, week, month, or year" }),
    ),
  }),
  execute: async (_id, params) => {
    const { query, count, topic, time_range } = (params ?? {}) as {
      query?: string;
      count?: number;
      topic?: string;
      time_range?: string;
    };

    if (!query?.trim()) throw new Error("query is required");

    const key = process.env.TAVILY_API_KEY;
    if (!key) {
      throw new Error(
        "web_search needs a Tavily API key. Set TAVILY_API_KEY in the environment. " +
          "Free tier is 1,000 credits/month with no card: https://www.tavily.com/",
      );
    }

    const wanted = Math.min(count ?? DEFAULT_COUNT, MAX_COUNT);

    const res = await fetch(ENDPOINT, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${key}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        query: query.trim(),
        max_results: wanted,
        search_depth: "basic",
        include_answer: true,
        ...(topic ? { topic } : {}),
        ...(time_range ? { time_range } : {}),
      }),
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });

    if (res.status === 401 || res.status === 403) {
      throw new Error("web_search: Tavily rejected the API key (401/403). Check TAVILY_API_KEY.");
    }
    if (res.status === 429) {
      throw new Error(
        "web_search: Tavily quota or rate limit reached (429). The free tier is 1,000 credits/month.",
      );
    }
    if (!res.ok) {
      throw new Error(`web_search: Tavily returned ${res.status} ${res.statusText}`);
    }

    const body = (await res.json()) as {
      results?: TavilyResult[];
      answer?: string;
      usage?: { credits?: number };
    };

    // Enforce the cap locally too: a provider that ignores max_results must not
    // be able to blow the context budget.
    const results = (body.results ?? []).slice(0, wanted);

    if (results.length === 0 && !body.answer) {
      return {
        content: [{ type: "text", text: `No results for: ${query}` }],
        details: { query, count: 0 },
      };
    }

    const parts: string[] = [`Search results for: ${query}`];
    if (body.answer) parts.push(`\nSummary: ${truncate(clean(body.answer), ANSWER_CHARS)}`);

    parts.push(
      ...results.map((r, i) => {
        const title = clean(r.title ?? "(untitled)");
        const snippet = truncate(clean(r.content ?? ""), SNIPPET_CHARS);
        return `\n${i + 1}. ${title}\n   ${r.url ?? ""}\n   ${snippet}`;
      }),
    );

    return {
      content: [{ type: "text", text: wrapUntrusted(parts.join("\n")) }],
      details: { query, count: results.length, credits: body.usage?.credits },
    };
  },
};

export const SEARCH_ENABLED = Boolean(process.env.TAVILY_API_KEY);
export const M3_TOOLS: AgentTool[] = SEARCH_ENABLED ? [webSearchTool] : [];
