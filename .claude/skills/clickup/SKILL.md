---
name: clickup
description: This project's task tracker is the `upshot` space in ClickUp (workspace "am consulting tools"). Use when filing, finding, triaging or updating bugs, features, chores, decisions, epics or releases for this repository, or when the user mentions ClickUp, tickets, the backlog or the roadmap.
---

# Upshot in ClickUp

The work on this repository is tracked in the **upshot** space. Not Jira, not
the `dibra` space next door — that one belongs to a different product.

| What | ID |
|---|---|
| Workspace (team) "am consulting tools" | `1100390000007610` |
| Space `upshot` | `1100390000037253` |
| List **Inbox**: untriaged intake (quick adds, form submissions) | `1100390000047755` |
| List **Backlog**: triaged, accepted work of every type | `1100390000047754` |
| List **Roadmap**: epics; Backlog tasks link to the epic they serve | `1100390000047756` |
| List **Releases**: one task per release (version, date, what shipped) | `1100390000047757` |

Statuses on every list: `to do` → `in progress` → `complete`.

The space has no folders: all four lists sit directly in it.

## Workflow

- New, unreviewed items go in **Inbox**. Triage means setting Type, Severity
  (for bugs), Effort and priority, then moving the task to **Backlog** or closing
  it as won't do.
- Bugs and features share **Backlog** so they get prioritised against each other.
- Epics live in **Roadmap**. Link each Backlog task to its epic.
- When a push to `main` ships, add or update the task in **Releases** with the
  commit SHA or tag, the date, and the tasks that shipped.

`docs/known-issues.md` and `docs/DECISIONS.md` in this repository cover the same
ground as Backlog and the Decision type. When you close a loop in one, say so in
the other rather than letting them drift apart.

## Custom fields (drop-downs; set them with the option id)

| Field | Field id | Options |
|---|---|---|
| Type | `3c552e80-7116-4b04-ac07-3e65013e4c2e` | Bug, Feature, Chore, Tech debt, Decision |
| Severity | `bf88fb34-d80e-43b0-aec9-5abed239ee6c` | Critical, Major, Minor, Cosmetic |
| Effort | `04257dc2-d552-4393-9960-86ba77f018fe` | XS, S, M, L, XL |
| Source | `ebbdaa02-74e4-4553-8665-f7dd9ef62585` | Internal, Client, Found on Windows, Docs / known-issues |

Option ids change if someone edits a field, so look them up at call time:
`GET /list/1100390000047754/field`.

## API access

There is no ClickUp MCP server here; use the REST API v2 with curl. The token is
a personal token (`pk_…`) in `~/.config/clickup/token`, and it is an owner of
this workspace. **Never print it, echo it, or write it into the repo.**
(`~/.config/clickup/shorashim/token` belongs to another workspace; don't use it
for upshot.)

```bash
T=$(cat ~/.config/clickup/token); API=https://api.clickup.com/api/v2

# List open Backlog tasks
curl -s -H "Authorization: $T" "$API/list/1100390000047754/task" \
  | python3 -c 'import json,sys; [print(t["id"], t["status"]["status"], "|", t["name"]) for t in json.load(sys.stdin)["tasks"]]'

# Create a task in Inbox
curl -s -X POST -H "Authorization: $T" -H "Content-Type: application/json" \
  "$API/list/1100390000047755/task" \
  -d '{"name":"…","markdown_description":"…","status":"to do"}'

# Change status
curl -s -X PUT -H "Authorization: $T" -H "Content-Type: application/json" \
  "$API/task/<task_id>" -d '{"status":"in progress"}'

# Set a drop-down custom field
curl -s -X POST -H "Authorization: $T" -H "Content-Type: application/json" \
  "$API/task/<task_id>/field/<field_id>" -d '{"value":"<option_id>"}'

# Move a task from Inbox to Backlog (ClickUp v3 endpoint)
curl -s -X PUT -H "Authorization: $T" \
  "https://api.clickup.com/api/v3/workspaces/1100390000007610/tasks/<task_id>/home_list/1100390000047754"
```

Creating, editing, moving or closing tasks changes shared state that other
people see. Confirm with the user before doing it unless they asked for that
exact change.
