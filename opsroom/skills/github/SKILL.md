---
name: github
description: Pull requests, issues, CI runs and releases through the `gh` CLI. Use when asked about a PR, why CI failed, what issues are open, or the state of a release.
requires: [gh]
---

# GitHub, through `gh`

Run these with `bash` from inside the repository in question. `gh` is already
authenticated; never ask the operator for a token.

## Finding out what is going on

```bash
gh pr list --limit 20 --json number,title,state,isDraft,headRefName
gh pr view <n> --json title,state,statusCheckRollup,reviewDecision
gh issue list --state open --limit 20 --json number,title,labels
gh run list --limit 10 --json databaseId,name,status,conclusion,headBranch
```

## Why CI failed

The rollup tells you which check failed; the log tells you why. Read the log
rather than guessing from the check name.

```bash
gh run view <run-id> --json jobs --jq '.jobs[] | "\(.name): \(.conclusion)"'
gh run view <run-id> --log-failed | tail -60
```

The failing step's own output is usually the last thirty lines. Quote the error
itself, not the fact that something failed.

## Releases

```bash
gh release list --limit 5
gh release view <tag> --json isDraft,assets,publishedAt
```

A release with `isDraft: true` is built but not downloadable by anyone.

## What not to do

- Do not create, close, merge or comment on anything unless you were asked to
  in this conversation. Reading is free; writing is somebody's repository.
- Do not `gh auth` anything. If a command reports an auth problem, report it.
- For work across many repositories, `gh api` with `--paginate` beats a loop.
