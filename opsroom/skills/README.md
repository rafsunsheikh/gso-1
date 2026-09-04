# Skills

A skill teaches the Ops Room agent to do something without anybody writing a
tool for it. It is a folder with a `SKILL.md` in it:

```
---
name: github
description: What this is for, and when to reach for it. One or two sentences.
requires: [gh]
---

# Instructions, written for the model

The exact commands, and what their output means.
```

## The three fields

- **name** — how the agent refers to it. Defaults to the folder name.
- **description** — goes in the system prompt, so keep it to a line or two and
  say *when* to use it. This is all the agent sees before deciding to read the
  rest.
- **requires** — the binaries the instructions call for. A skill whose binaries
  are missing is never offered as usable: the agent is told it exists and what
  it would need, so it can say "I could do that if you installed `gh`" rather
  than confidently running a command that is not there.

## Where they live

- `opsroom/skills/` — ships with GSO-1 and travels into every release.
- `<data>/skills/` — your own. Not touched by updates. A skill here replaces a
  bundled one of the same name, so you can override what ships without editing
  the repository. On a source checkout that is `data/skills/`.

## Writing a good one

Write for a model that will act on it, not for a person browsing docs.

- Give exact commands, in fenced blocks, that can be run as they are.
- Say what the output *means*: that `Exited (137)` is usually the OOM killer is
  the sort of thing worth a line, because the model cannot infer it.
- Include a section on what **not** to do. The agent has a shell, and the
  difference between reading state and changing somebody's repository is worth
  spelling out every time.
- Keep it short. The body is loaded into a context window that a local model
  has very little of.

The index costs a couple of hundred tokens for a handful of skills; the bodies
are only read when the agent decides it needs one.
