# Switchr: an agent account per session

Decided with Michał, 2026-11-12. Phase 2 of the Powr Pack (the 2026-11-04 spec
called it "Swapr"; the name is now **Switchr**).

## Why

One Claude subscription does not last Michał a week (weekly limit), so part of
the work has to run on a second account. Switching the whole machine's login —
what the `rcosteira.account-switch` plugin did — moves *every* running session at
once and breaks what belongs to the account: artifacts land on the other account,
and so on. He wants to move **chosen sessions only** and leave the rest alone.

## What he gets

- **The account belongs to one agent** (one terminal running Claude Code or
  Codex), not to a space or a tab.
- **He chooses, always by hand.** Switchr never moves a session on its own,
  never reacts to a limit running out. (Asked explicitly; answer: "ja sam
  wybieram konto".)
- **New agent:** starts on the main account unless he picks another when
  starting it.
- **Moving a running session:** he picks the other account; the agent quits and
  comes back in the same terminal, on the new account, in the **same
  conversation**, with the same instructions, memory, skills, plugins and
  settings. A few seconds of restart is accepted.
- **Seeing it:** in the **Agents** list, an agent on a non-main account carries a
  short badge with the account's name at the end of its row. The main account
  shows nothing (it is the default, only exceptions are marked).

  ```
  ● Claude   powr · switchr
  ● Claude   tr · funnels       bonus
  ◐ Codex    atlas                 tr
  ```
- **Choosing helped by numbers:** wherever he picks an account, each account
  shows how much of its limit is left. Information only; it triggers nothing.
- **Switchr's page in the Powr panel:** the accounts per agent kind (Claude,
  Codex), each with its remaining limit; add an account (by logging in), rename,
  remove. The switch at the top turns the module on and off like Browsr's and
  Spacr's.
- **Agents covered:** Claude Code and Codex.

## Non-goals

- No automatic moving, no limit-triggered prompts, no "move all".
- No switching the whole machine. The main login in `~/.claude` / `~/.codex` is
  never rewritten by Switchr.
- No accounts per space or per project.
- Remote machines (`machine` spaces): out of scope; local agents only.
- Other agent kinds (Pi, Gemini, …): out of scope.

## Anti-goals (would be a failure even if it "works")

- A session on the second account that has lost his CLAUDE.md, memory, skills,
  plugins, hooks, MCP servers or settings — it must behave like him.
- A switch that loses the conversation, or silently starts a fresh one.
- A switch that kills an agent in the middle of a turn.
- Two places holding the same account's login. OAuth refresh tokens rotate; two
  copies of one account means one of them gets logged out at random.
- The badge lying: it must come from what the running process actually uses, not
  from a stored note about it.

## Approaches considered

- **A. A separate home per extra account (chosen).** Each extra account gets its
  own config folder (`CLAUDE_CONFIG_DIR` / `CODEX_HOME`) holding its own login;
  everything else in it points at the main folder. The agent is started with
  that variable set. Works the same for Claude and Codex; each home refreshes its
  own token. Hard part: Claude's `.claude.json` mixes account identity with user
  settings (user-scope MCP servers, per-project state) and cannot simply be shared.
- **B. A long-lived token per process** (`claude setup-token` →
  `CLAUDE_CODE_OAUTH_TOKEN`). Simpler for Claude, but Codex has no equivalent,
  and the token has narrower scopes (usage reading, claude.ai-linked features
  such as artifacts unverified). Rejected.
- **C. Swapping the login file just before starting an agent.** Both CLIs re-read
  their credentials on every request, so all sessions follow the swap — the
  exact problem being solved. Rejected.

## How it is built (decided)

### Account homes

- **Main account** = the existing `~/.claude` and `~/.codex`, untouched, no
  environment variable set. It has a display name (default "main", renamable).
- **Extra account** = a folder under `~/.local/share/switchr/<kind>/<name>/`
  (mode 700).
  - Claude: its own `.credentials.json` and its own `.claude.json`. Every other
    entry of `~/.claude` is a symlink to the main one — including `projects/`
    and `sessions/`, so conversations are shared and `--resume` finds them from
    either account.
  - `.claude.json` of an extra account: account identity (`oauthAccount` and
    other per-login fields) is its own; user settings (user-scope `mcpServers`,
    per-project entries, and other non-account keys) follow the main file. Which
    keys are which is verified against the installed Claude Code, not guessed.
    The sync is **one-way, main → extra**, at every start: a user-wide setting
    changed from inside an extra-account session (a new MCP server, a newly
    trusted folder) does not flow back and is reset next start. Accepted; such
    changes are made from a main-account session. *(Builder's call.)*
  - Codex: its own `auth.json`; everything else of `~/.codex` symlinked
    (`config.toml`, `AGENTS.md`, sessions, memories, plugins …), checked against
    the installed Codex for files that must not be shared (locks, sockets,
    daemon state).
  - New entries that appear in the main folder later (a new dir Claude creates)
    get linked the next time an agent starts on that account.
- **One login per account, in exactly one place.** Switchr refuses to add an
  account whose identity (email / account id) equals the main one or another
  extra account.

### Starting an agent on an account

- One launcher program in the repo (`switchr`, Python like `bridge.py`):
  `switchr run <kind> <account> [-- agent args]` refreshes the home's links and
  `.claude.json` sync, then `exec`s the agent with the variable set. For the main
  account it execs the agent plainly.
- **The new-agent menu:** for each extra account Switchr adds an entry to Herdr's
  `programs.json` (e.g. "Claude · bonus") whose `run` is that launcher. Herdr's
  own new-agent menu then offers it. Switchr only adds/removes its own entries
  and leaves the rest of the file alone.

### Which account an agent is on

- Read from the running agent process: `hc get <t> --json` gives the foreground
  pid; its environment (`/proc/<pid>/environ`) carries `CLAUDE_CONFIG_DIR` /
  `CODEX_HOME` or not. Path → account name. No stored mapping.

### Moving a running session

- Offered from the agent's row in the Agents list (right-click → account
  choice, current one ticked), and from wherever else Herdr lets a mod act on an
  agent if it is cheap.
- Only when the agent is **idle or done** — its turn is over. Not while
  working, and not while blocked (in Claude that is usually a permission prompt
  in the middle of a turn; quitting would drop the pending tool call). Otherwise
  the choice is shown disabled with the reason. *(Builder's call, 2026-11-12.)*
- Steps: find the conversation id (Claude: `~/.claude/sessions/<pid>.json` →
  `sessionId`; Codex: its own session record, to be found), quit the agent
  cleanly, wait for the shell prompt in the same terminal, then start
  `switchr run <kind> <new account> -- <original flags minus resume/continue
  flags> --resume <id>` (Codex: `codex resume <id>`). Original flags such as
  `--dangerously-skip-permissions` are kept from the process's argv.
- After the move, check the agent is up, on the new account, in the same
  conversation; if anything fails, say so in a toast and leave a shell in the
  terminal — never a silent fresh session.

### The badge in the Agents list

- Herdr's Agents list is its own mod and cannot be decorated from outside. Powr
  ships its own copy (cloned from Herdr's `agents` mod) with the badge added, and
  swaps it into the layout when Switchr is on — exactly the way Spacr replaces
  `spaces` (`layout-swap.js`). Switching Switchr off puts Herdr's list back.
- Known cost: when Herdr changes its Agents list, the copy has to be refreshed.

### Remaining limit

- Per account, read with that account's own login from the providers' usage
  addresses (Claude: `api.anthropic.com/api/oauth/usage`; Codex:
  `chatgpt.com/backend-api/wham/usage`, as the removed plugin used). These are
  not documented APIs: verify request shape and response against the live
  service on his accounts before building on them; cache, and show "unknown"
  rather than fail when they don't answer.
- **Token renewal never races an agent** *(builder's call)*: if an agent is
  running on the account, Switchr only reads the access token that agent keeps
  fresh and never renews it itself. If none is running, Switchr may renew,
  holding a per-account lock that `switchr run` also takes before starting an
  agent. The same rule covers checking an imported login.
- **Numbers are shown on Switchr's Powr page and in the move menu**, not in
  Herdr's new-agent menu (it shows fixed names; rewriting them would also rename
  tabs, and a third copied Herdr mod is not worth it). *(Builder's call.)*

### Adding an account

- From Switchr's page: name it, then a terminal opens running the agent's own
  login inside the new home (Claude: its login flow with `CLAUDE_CONFIG_DIR` set;
  Codex: `codex login` with `CODEX_HOME` set). When the login lands, Switchr
  checks the identity is new (see "one login per account") and shows the account.

## Constraints

- Linux, Herdr desktop app (`herdr-app`), the existing Powr panel and installer.
- Public, generic tool (goes to GitHub with the Powr Pack): any number of
  accounts per agent kind, named by the person; nothing in the code or docs is
  specific to one user's accounts. No import of other tools' saved logins —
  accounts are added by logging in.
- The end-to-end test run happens on accounts we own before release.
- Third-party contracts (env variable names, which files hold the login, resume
  flags, usage endpoints) are verified against the installed CLIs / live
  services before planning on them; each plan step cites where it was checked.
  At least one test drives the real CLI, not a fake.
- Installer (`install.py`) learns Switchr like the other modules; running it
  twice is safe.

## Left to the builder

- Exact on-disk layout of the account registry (names, kind, home path).
- How the Powr page draws the account rows and the add/rename/remove controls,
  following the existing Browsr and Spacr pages.
- How the badge is styled (follow Herdr's muted secondary text).
- How quitting the agent is done (a slash command, keys) — whatever is reliable
  and verified.
- Test layout, in the existing `tests/` style.
