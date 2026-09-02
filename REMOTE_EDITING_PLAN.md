# Editing and a terminal from the phone, Plan

> **Status: not started. Deliberately deferred on 2026-09-02.**
> Everything below is research and a decision that has not been taken yet.
> Nothing in the codebase implements any of it.

---

## 1. The ask

> *"Is there any way I can see the VS Code view including the VS Code terminal
> for any open repo from the mobile?"*

The phone can already see which repos are open in VS Code and bring one to the
front on the Mac. What it cannot do is show the editor itself, or give you a
shell in a repo.

Two different wants are tangled together in that question, and they have
different answers:

* **the editor** — read and change a file that is open on the Mac
* **the terminal** — run something in a repo and watch the output

Route A gives both. Route B gives only the second.

---

## 2. What is true on this machine

Verified 2026-09-02, so a later reader does not have to re-check:

```
VS Code                1.127.0  (4fe60c8b1cdac1c4c174f2fb180d0d758272d713)
code CLI               /Applications/Visual Studio Code.app/Contents/Resources/app/bin/code
                       not on PATH; thecmanager/vscode.py already finds it there
code tunnel            supported, with start / stop / status / rename / service
tunnel status          {"tunnel": null, "service_installed": false}
~/.vscode-cli          does not exist yet, created at first login
GSO-1                  no PTY, no WebSocket, no terminal of any kind
```

GSO-1 has never opened a shell for anybody. Everything it runs today is a
command it chose to implement: a start command, a git operation, llama-server,
the agent sidecar. That is worth keeping in mind, because both routes below
change it.

---

## 3. Route A, VS Code Remote Tunnels

### How it works

A Mac behind a router cannot accept incoming connections. A tunnel gets around
that by dialling out: `code tunnel` opens an outbound connection to a Microsoft
relay and holds it open. Opening `vscode.dev/tunnel/<name>` on the phone talks
to Microsoft, which passes traffic back down the connection the Mac already
made. No port forwarding, no firewall change.

What arrives on the phone is real VS Code in the browser, driving the Mac: the
files, the extensions, and the integrated terminal, which is a shell on the Mac
running as you. Both ends sign in to the same GitHub or Microsoft account, and
the machine registers under a name.

### What GSO-1 would build

The same shape as the llama-server manager it already has, and for the same
reason: the capability exists, the app's job is to make it visible and
stoppable.

* `tunnel status` surfaced on the Local LLM-style screen and on the phone
* start and stop, with the device-login code shown in the UI during sign-in
* a per-repo "Open in VS Code" link that deep-links `vscode.dev` at that folder
* a visible reminder while a tunnel is running, because the failure mode is
  forgetting

Deliberately **not** `code tunnel service install`. See below.

### What it costs

* Traffic routes through Microsoft's relay. Encrypted end to end, so they
  should not be reading the files, but they operate the path and know which
  machine is connected, when, and from where. GSO-1's pitch is that nothing
  leaves your machine; this is a deliberate exception to it.
* Requires a GitHub or Microsoft sign-in.
* Full VS Code on a 390px screen is workable but cramped.

---

## 4. Route B, a terminal inside GSO-1

A PTY per repo over a WebSocket, with xterm.js in the mobile page. Entirely
local, no third party, and a much smaller build than it sounds: the pieces are
a `pty` process per session, a socket, and a terminal widget.

It gives a shell in any repo from the phone. It does not give the editor, so it
only answers half the question.

The reason it is not obviously the safer choice despite being local: it turns
the phone token into a shell. Today somebody with that token can drive the
operations GSO-1 implements. With a PTY they can do anything the user can do.

---

## 5. Security

**The headline for Route A: a tunnel turns the laptop into a server that anyone
with the GitHub password can get a shell on.**

1. **The GitHub account becomes a way in.** Today a stolen GitHub password means
   bad pushes. With a tunnel running it means a terminal on the Mac, and
   everything readable from it: SSH keys, `.env`, `~/run_manager.sh` and the
   Telegram token inside it. **2FA on that account is a precondition, not a
   nice-to-have.**
2. **A third party is in the path.** See above.
3. **It outlives your attention.** It runs until stopped, reconnects for three
   hours after a drop by default, and `tunnel service` can start it at boot.
   Switched on for an evening, still running a month later. `--no-sleep` exists
   precisely to keep the machine awake serving it.
4. **The blast radius is the machine, not a repo.** There is no per-repo tunnel;
   the terminal reaches the whole filesystem.

### Honest comparison with what already runs

GSO-1 is already exposed, and in one respect more carelessly:

| | Reach | Auth | Grants |
|---|---|---|---|
| GSO-1 today | the local network (`MANAGER_HOST=0.0.0.0`) | 12-character token | the operations it implements |
| GSO-1 + PTY (route B) | the local network | the same token | a shell |
| VS Code tunnel (route A) | the internet | GitHub account, ideally with 2FA | a shell |

Route B is the only row that widens what the existing token grants without
also improving how it is protected. If B is ever built, a longer token, or a
second factor for terminal sessions specifically, should be part of it rather
than an afterthought.

---

## 6. Recommendation, as it stood

**Route A, and not B first.** The question asked for the editor and the
terminal; A is the only one that delivers both, and it delivers them by being
actual VS Code rather than a reimplementation of it. B gives half the answer
while widening what a leaked phone token is worth.

They also compose: with a tunnel running, the VS Code terminal *is* the
terminal, so B stops being necessary.

If the trade does not appeal, the fallback is that most of what a phone
terminal gets used for, *is it dirty, commit, push, pull, restart it, read the
log*, GSO-1 already does without exposing a shell to anyone. What is left is
open-ended, and for open-ended work a tunnel switched on deliberately is a
reasonable answer.

---

## 7. Open questions, to settle before starting

* Is 2FA on the GitHub account in place? Route A should not ship without it.
* On-demand only, or is a boot service ever acceptable? The plan above assumes
  on-demand, because the realistic failure is leaving it running.
* Should the phone be able to *start* a tunnel, or only see and stop one?
  Starting requires an interactive device-code sign-in, which is awkward on a
  phone and lands on the Mac. Stopping from the phone is unambiguously useful.
* Does a running tunnel deserve a louder signal than a status row? A tunnel is
  the most consequential thing GSO-1 could switch on.
* If Route B is ever revisited: what protects it, given the phone token as it
  stands is twelve characters?

---

## 8. Progress log

* **2026-09-02** — Researched, costed and deferred. Nothing implemented. The
  machine facts in section 2 were verified on this date; re-check them before
  building, particularly whether `code tunnel` still behaves as described.
