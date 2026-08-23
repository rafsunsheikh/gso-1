import type { AgentTool } from '@earendil-works/pi-agent-core';
import { Type } from 'typebox';
import { execSync } from 'node:child_process';

export const diskFreeTool: AgentTool = {
  name: 'disk_free',
  label: 'Disk free',
  description: 'Show free disk space on the host.',
  parameters: Type.Object({}),
  execute: async () => {
    const out = execSync('df -h /').toString();
    return { content: [{ type: 'text', text: out }] };
  },
};
