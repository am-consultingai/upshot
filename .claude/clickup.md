# ClickUp config: Upshot

Follows the shared ClickUp standard (user skill `clickup`). This file holds only this
project's facts.

| What | Value |
|---|---|
| Token file | `~/.config/clickup/token` |
| Expected account | `my agent email` (id `240114217`) |
| Workspace | "am consulting tools" `1100390000007610` |
| Space | `upshot` `1100390000037253` |
| List: Inbox | `1100390000047755` |
| List: Backlog | `1100390000047754` |
| List: Roadmap | `1100390000047756` |
| List: Releases | `1100390000047757` |

Layout: standard (four lists, no folders).

Do not file here: the `dibra` and `afterprompt` spaces belong to other products; don't use Jira either.

Extra header keys: none.

Project rules:
- `docs/known-issues.md` and `docs/DECISIONS.md` in this repository cover the same ground as Backlog and the `decision` type. When you close a loop in one, say so in the other rather than letting them drift apart.
