---
name: dependencies
description: What a project depends on, whether it is pinned, and whether anything is vulnerable. Use when asked about dependencies, lockfiles, outdated packages or security advisories.
requires: [npm]
---

# Dependencies

Work from the project directory. Which ecosystem it is follows from the
manifest, so look before running anything.

## Node

```bash
npm audit --json | head -c 4000
npm outdated --json | head -c 4000
```

`npm audit` exits non-zero when it finds something, which is not a failure of
the command. Report the counts by severity and name the few worth acting on,
not every transitive advisory.

A project with no `package-lock.json` is not reproducible: two installs can
produce different trees. Worth saying when it is true.

## Python

Prefer the tool the project already uses over installing anything.

```bash
[ -f uv.lock ] && uv pip list --outdated
[ -f requirements.txt ] && grep -c '==' requirements.txt   # how many are pinned
```

Requirements without `==` float, so today's install is not last month's.

## What to report

Lead with whether the project is pinned at all, then anything of high or
critical severity, then the count of the rest. A list of ninety advisories is
not an answer.

## What not to do

- Never run `npm audit fix`, `npm update`, or edit a manifest unless asked. A
  dependency bump is a change to somebody's project.
- Do not install a tool to answer a question about dependencies.
