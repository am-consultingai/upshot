# Manual checks

Everything in this repository is proved by an automated gate except the items below, which
need a human's eyes or a real meeting. **None of them gates a phase** — they are recorded
so a release can say what was actually looked at.

| Check | How | Gates |
|---|---|---|
| A toast is visible, carries the app name, and persists in Action Center | Start a recording from the tray on Windows and watch the notification | nothing — `test_toast_buttons_present` covers the content, `test_notifications_fire_once` the debounce |
| The tray icon colours read correctly against both Windows themes | Look at the icon in light and dark taskbars | nothing — `test_icon_state_table` covers the state → colour mapping |
| The summary HTML renders correctly in a real mail client | Send one meeting to yourself with `delivery.mode = auto_send` | nothing — `test_email_has_no_style_block` and the goldens cover the markup |
| Detection quality on real meetings | `detection.mode = "shadow"` for a week, then read `/detector` | **flipping `detection.mode` to `on`** (DETECTION.md §11) |
| The scripted positives and negatives in `DETECTION.md` §11 | Zoom, Teams, Meet, a webinar; YouTube, Spotify, dictation | the same |

## Recorded runs

| Date | Who | What was checked | Result |
|---|---|---|---|
| — | — | Nothing yet: this build has not run on a Windows desktop. | — |
