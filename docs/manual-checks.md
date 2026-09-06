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
| **Whether muting in the conferencing app releases the microphone stream** | `probe-mic.py watch` during a real call — see the open question below | the 60 s release grace, and whether mic-release can be an end signal at all |

---

## Open question: does mute release the microphone?

**Status: unanswered as of 2026-09-06.** `DETECTION.md` §7 asserts that Zoom and Teams mute
in *software*, so the capture stream stays open and `LastUsedTimeStop` stays `0`. Everything
downstream depends on that being true: the mic-release end signal, the 60-second grace, and
the privacy note explaining why the tray icon stays red while you are muted. Nobody has
watched it happen.

Run `scripts/windows/probe-mic.py watch` under the launcher's venv, join a real Zoom or Meet
call, and do this in order:

| Action | Expected | If it differs |
|---|---|---|
| Join the call | `ACQUIRED  …\Zoom.exe` | the detector cannot wake for this app at all — check the host API (below) |
| Mute in the app | **nothing** | mute releases the stream: every mute would look like a meeting ending, and the 60 s grace becomes load-bearing rather than a blip guard. `detection.release_grace_s` needs to cover a realistic mute, and §7's privacy note is wrong |
| Unmute | nothing | as above |
| Turn the camera off | nothing | the camera ConsentStore is a separate key; a release here is only a corroboration signal |
| Leave the meeting | `RELEASED  …\Zoom.exe` | there is no clean end signal; stopping falls through to `dual_silence_s` (5 min) for every meeting |
| Rejoin within 60 s | `ACQUIRED` again | — |

Record the result in the table below and, if mute releases, open a `DECISIONS.md` entry
rather than quietly retuning the grace.

### What the same probe already established, without a meeting

Run on 2026-09-06 on the author's machine, so the protocol above starts from known ground:

- The signal follows the **host API**, not the device. The same physical microphone
  registers when captured through WASAPI or DirectSound and is **invisible through MME**
  (legacy `waveIn`). Loopback endpoints never register, which is correct — they are
  render-side and not the microphone capability, so the `them` track can never self-hold.
- The app's own capture goes through WASAPI (`app/audio/wasapi.py`), so **it registers as a
  holder of its own microphone** while recording.
- `voicemeeter.exe` holds the microphone **continuously** on this machine, and is not in
  `detection.ignore`.

The last two are why `detector.py` reducing all holders to `holders[0]` is a defect and not
a simplification: with a permanent holder present the release grace can never start. That is
a code issue, not an open question — it belongs in `known-issues.md`, not here.

## Recorded runs

| Date | Who | What was checked | Result |
|---|---|---|---|
| 2026-09-06 | am | `probe-mic.py devices` — which capture paths register in the ConsentStore | MME silent; DirectSound and WASAPI register; loopback never registers |
| 2026-09-06 | am | `probe-mic.py show` — standing holders with nothing else running | `voicemeeter.exe`, continuously |
| — | — | The mute/leave protocol above | **not yet run** |
