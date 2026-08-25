# Security Policy

## What GSO-1 is, in security terms

GSO-1 is a **localhost control plane for your own machine**. By design it can:

- start and stop arbitrary processes in your projects,
- run `git pull` and detected setup commands (`install.sh`, `npm install`, `pip install`),
- read files anywhere under your configured project roots,
- optionally drive the `claude` CLI with a permission prompt in front of it.

That is the intended feature set, not a vulnerability. It also means **anyone
who can reach the GSO-1 HTTP port can run code as you.** Treat the port the way
you would treat an unlocked terminal.

## The defaults that protect you

| Control | Default | Where |
|---|---|---|
| Bind address | `127.0.0.1`, loopback only, unreachable from the network | `MANAGER_HOST` |
| Remote access | Refused outright unless a token is set | `MANAGER_MOBILE_TOKEN` |
| Non-loopback requests | Must present the shared token | `thecmanager/remoteauth.py` |
| Agent tool use | Read-only tools auto-run; writes and commands require explicit Allow/Deny | `thecmanager/claude_perm_mcp.py` |
| Project roots | Only paths under a configured root are addressable | `config.under_any_root()` |

**Do not set `MANAGER_HOST=0.0.0.0` without also setting a strong
`MANAGER_MOBILE_TOKEN`,** and prefer a tunnel (Tailscale, SSH forward) over
exposing the port directly. GSO-1 speaks plain HTTP; it has no TLS of its own.

## Supported versions

GSO-1 is pre-1.0. Only the **latest release** receives security fixes.

| Version | Supported |
|---|---|
| Latest release | ✅ |
| Anything older | ❌ |

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Report privately through GitHub:

1. Go to the [Security tab](https://github.com/rafsunsheikh/gso-1/security/advisories/new)
2. Click **Report a vulnerability**
3. Include what you did, what happened, and what you expected

### What to expect

| Stage | Target |
|---|---|
| Acknowledgement | Within 5 business days |
| Initial assessment | Within 10 business days |
| Fix or mitigation plan | Communicated once assessed |

This is a solo-maintained project, not a funded security program, timelines
are best-effort. There is no bug bounty.

### In scope

- Authentication bypass on the remote/mobile gate
- Path traversal that escapes the configured project roots
- Command injection reachable from the HTTP API
- Permission-prompt bypass in the agent bridge
- Token or credential leakage into logs, the UI, or release artifacts

### Out of scope

- The fact that localhost users can run commands, that is the product
- Anything requiring an attacker to already have your shell
- Findings that depend on you deliberately disabling the documented defaults
- Vulnerabilities in third-party dependencies with no GSO-1-specific impact
  (report those upstream)

## Disclosure

Please give us a reasonable window to ship a fix before publishing. Reporters
are credited in the release notes unless they ask not to be.
