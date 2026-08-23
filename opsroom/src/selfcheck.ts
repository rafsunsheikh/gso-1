/**
 * Sidecar self-check — the health gate for Ops Room itself.
 *
 * `supervisor verify` boots GSO-1 and probes its port, which proves the Python
 * app works. It says nothing about the Node sidecar: a syntax error or bad
 * import in opsroom/ would pass verification and only fail later, at the worst
 * possible moment — after promotion.
 *
 * This script imports every tool module and asserts the tool set is intact.
 * It touches no network and no model, so it is fast and deterministic.
 *
 * Exit 0 = sidecar loads and its tools construct.
 * Exit 1 = anything failed; the release must not be promoted.
 */

import "./env.ts";

/** Tools that must always be present. Losing one means a broken build. */
const REQUIRED = [
  "list_apps",
  "git_status",
  "git_dirty_sweep",
  "read",
  "ls",
  "grep",
  "write",
  "edit",
];

async function main(): Promise<number> {
  const names: string[] = [];

  try {
    const m1 = await import("./tools.ts");
    const m2 = await import("./fstools.ts");
    const m3 = await import("./websearch.ts");
    const m4 = await import("./buildtools.ts");

    for (const set of [m1.M1_TOOLS, m2.M2_TOOLS, m3.M3_TOOLS, m4.M4_TOOLS]) {
      for (const t of set ?? []) {
        if (!t?.name) throw new Error("a tool has no name");
        if (typeof t.execute !== "function") throw new Error(`${t.name} has no execute()`);
        names.push(t.name);
      }
    }

    // The policy module must load and expose a sandbox root, or the guards
    // silently do nothing (see the M2 note about inert operations).
    const policy = await import("./policy.ts");
    if (!policy.SANDBOX_ROOT) throw new Error("policy.SANDBOX_ROOT is empty");
    if (typeof policy.assertWritable !== "function") throw new Error("policy guard missing");

    // And it must actually refuse something. A guard that only ever allows is
    // indistinguishable from no guard at all.
    let refused = false;
    try {
      policy.assertWritable("/etc/passwd");
    } catch {
      refused = true;
    }
    if (!refused) throw new Error("policy.assertWritable did not refuse /etc/passwd");
  } catch (err) {
    console.error(`selfcheck FAIL: ${(err as Error).message}`);
    return 1;
  }

  const missing = REQUIRED.filter((r) => !names.includes(r));
  if (missing.length) {
    console.error(`selfcheck FAIL: missing tools: ${missing.join(", ")}`);
    return 1;
  }

  const dupes = names.filter((n, i) => names.indexOf(n) !== i);
  if (dupes.length) {
    console.error(`selfcheck FAIL: duplicate tool names: ${[...new Set(dupes)].join(", ")}`);
    return 1;
  }

  // Structure is now proven. Behaviour is not — a tool can load perfectly and
  // still return nonsense (see smoke.ts for the incident that motivated this).
  const { checkEndpoints, smokeTools } = await import("./smoke.ts");
  const allTools = [
    ...((await import("./tools.ts")).M1_TOOLS ?? []),
    ...((await import("./fstools.ts")).M2_TOOLS ?? []),
    ...((await import("./buildtools.ts")).M4_TOOLS ?? []),
  ];

  const failures = [...(await checkEndpoints()), ...(await smokeTools(allTools))];
  if (failures.length) {
    for (const f of failures) console.error(`selfcheck FAIL [${f.check}]: ${f.detail}`);
    return 1;
  }

  console.log(`selfcheck OK: ${names.length} tools [${names.join(", ")}]`);
  return 0;
}

main().then(
  (code) => process.exit(code),
  (err) => {
    console.error(`selfcheck FAIL: ${err?.message ?? err}`);
    process.exit(1);
  },
);
