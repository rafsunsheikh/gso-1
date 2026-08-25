/**
 * Uptime info tool, reports how long the machine has been up.
 */

import os from "node:os";

import type { AgentTool } from "@earendil-works/pi-agent-core";
import { Type } from "typebox";

/**
 * Get system uptime in seconds.
 *
 * The first version of this fetched `http://127.0.0.1:8420/api/system/uptime`,
 * a route that does not exist: and its catch returned 0, so the tool reported
 * "0s" on a machine up for a day, while passing verification. Node exposes
 * uptime directly; no HTTP, nothing to swallow, nothing to hallucinate.
 */
function getUptimeSeconds(): number {
  return os.uptime();
}

/**
 * Format uptime into human-readable string.
 */
function formatUptime(seconds: number): string {
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = Math.floor(seconds % 60);

  const parts: string[] = [];
  if (days > 0) parts.push(`${days}d`);
  if (hours > 0) parts.push(`${hours}h`);
  if (minutes > 0) parts.push(`${minutes}m`);
  parts.push(`${secs}s`);

  return parts.join(" ");
}

export const uptimeInfoTool: AgentTool = {
  name: "uptime_info",
  label: "Uptime info",
  description:
    "Report how long this machine has been up, in seconds and human-readable format.",
  parameters: Type.Object({}),
  execute: async () => {
    const seconds = getUptimeSeconds();
    return {
      content: [
        {
          type: "text",
          text: JSON.stringify(
            {
              uptime_seconds: seconds,
              uptime_human: formatUptime(seconds),
              uptime_days: Math.floor(seconds / 86400),
            },
            null,
            2,
          ),
        },
      ],
      details: { uptime_seconds: seconds },
    };
  },
};