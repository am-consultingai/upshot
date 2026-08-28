# Auto-detection: how the app knows a meeting started

The tray app's job is to notice a meeting beginning, record it, notice it ending, and tell
you — without you touching anything. This document defines that mechanism.

Manual Start/Stop (M1) remains the baseline and the fallback. Detection is layered on top,
which is what lets it be **tuned conservatively**: see §1.

---

## 1. The costs are asymmetric — design for that

| Error | Cost |
|---|---|
| **False positive** — records something that wasn't a meeting | A private conversation, a voice note, or a therapy session lands on disk and possibly in an LLM prompt. **Severe, and a trust-killer.** |
| **False negative** — misses a real meeting | You click Start. **One click.** |

Because manual start exists, a miss is cheap and a false positive is not. Every threshold
below is set accordingly, and every automatic recording is announced immediately with a
one-click *"not a meeting"* escape.

---

## 2. What Windows actually gives us

| # | Signal | API | Tells us | Cost |
|---|---|---|---|---|
| 1 | **Mic in use, and by whom** | `HKCU\...\CapabilityAccessManager\ConsentStore\microphone` — `LastUsedTimeStop == 0` means *in use right now* | The capture side: which exe holds the microphone | ~0, event-driven via `RegNotifyChangeKeyValue` |
| 2 | **Who is playing audio** | `IAudioSessionManager2` via **pycaw** — active render sessions + their PIDs | The render side: is a conferencing app producing sound? | cheap, poll or COM callback |
| 3 | **Window titles** | `EnumWindows` + `GetWindowText` (pywin32) | "Zoom Meeting", "… | Microsoft Teams", "Meet – …" — also the meeting's *name* | cheap poll |
| 4 | **Speech on each track** | Silero VAD (onnxruntime, ~2 MB) over our own capture buffers | Is a *conversation* happening, on which side | ~1% CPU, only while streams are open |
| 5 | **Camera in use** | same ConsentStore path, `webcam` subkey | Corroboration | ~0 |
| 6 | **Calendar** | *(post-V1, not built)* | Corroboration + naming | n/a |

**The division of labour that matters:** the registry is the reliable source for the
*capture* side (who holds the mic), and pycaw is the reliable source for the *render* side
(who is making sound). Each API is used where it's strong. Note that signal 1 is an
undocumented artifact — it backs the Windows privacy indicator, not a public contract — so
it is treated as a strong heuristic with pycaw as the backstop, never as gospel.

---

## 3. Four tiers, from idle to committed

### Tier 0 — Idle (cost: nothing)
A single blocking wait on `RegNotifyChangeKeyValue` over the ConsentStore subtree. No
polling loop, no open audio streams, no measurable CPU or battery draw. The tray icon is grey.

### Tier 1 — Wake (a process acquired the microphone)
The registry event fires. Within ~200 ms:
- Resolve which exe (`NonPackaged` subkeys are the exe path with `\` → `#`; packaged apps appear under their package family name).
- Check the ignore list (§7). If listed, go back to Tier 0.
- **Open both WASAPI streams and start the pre-roll ring buffer (§4).** Nothing is written to disk yet.
- Start collecting evidence: render sessions, window titles, camera.

Tray goes amber. **No notification yet** — this is where false positives get filtered, and
notifying here would make the app cry wolf.

### Tier 2 — Confirm (is this a conversation?)
Over the next 10–20 seconds, score the evidence (§5) and run VAD on both tracks. One of:
- **Score ≥ threshold, sustained ≥10 s** → commit.
- **Score decays / mic released** → discard the pre-roll, return to Tier 0, log a *near miss* (§8).
- **90 s elapsed with no verdict** → give up, return to Tier 0, log a near miss.

### Tier 3 — Committed (recording)
Meeting folder created, pre-roll flushed to `chunk_0001.wav`, chunks roll from there.
Tray goes red. **Toast fires now** (§6). The evidence vector is written into `meta.json`,
so the UI can always answer *"why did this record?"*

---

## 4. The pre-roll buffer — the detail that makes it usable

Confirmation takes 10–20 seconds. Without a pre-roll, every auto-recording would be missing
its first 20 seconds — which is exactly when someone says *"so what we wanted to discuss
is…"*. That's the difference between a summary that's right and one that's subtly wrong.

So from **Tier 1**, both streams write into a rolling in-memory buffer. At 16 kHz mono
int16 a 60-second pre-roll is **1.9 MB per track** — free. On commit, it's flushed to disk
as the first chunk; on discard, it's dropped and never touches storage.

Net effect: **the recording begins before the trigger fires.**

---

## 5. The evidence model

Nested booleans get unmaintainable fast. Weighted evidence with one threshold is tunable,
explainable, and easy to show in the UI.

| Evidence | Weight |
|---|---|
| Mic held by a **known conferencing app** (Zoom, Teams, Webex, Slack, Discord, a browser) | **+3** |
| Mic held by an **unknown** app | +1 |
| Sustained speech on the **loopback** track (someone else talking) | **+2** |
| Sustained speech on the **mic** track (you talking) | **+2** |
| **Window title** matches a meeting pattern | +2 |
| Active **render session** owned by the mic-holding process | +1 |
| **Camera** in use | +1 |
| **Calendar** event covering now, with a meeting link *(post-V1; weight reserved, never contributed in V1)* | +3 |
| Process on the **ignore list** | **−5** |

**Commit at ≥ 5, sustained for 10 seconds.**

*Sustained speech* = ≥3 seconds of voiced frames inside a rolling 10-second window, per track.

### Worked examples

| Situation | Evidence | Score | Result |
|---|---|---|---|
| Zoom call, you talking | zoom +3, loopback +2, mic +2, title +2 | **9** | ✅ record |
| **All-hands, you never unmute** | zoom +3, loopback +2, title +2 | **7** | ✅ record |
| YouTube video | loopback +2 | 2 | ❌ ignore |
| Dictation into Word | unknown app +1, mic speech +2 | 3 | ❌ ignore |
| Music while working | loopback +2 | 2 | ❌ ignore |
| WhatsApp voice message | unknown +1, mic +2 | 3 | ❌ ignore |
| Discord voice channel | discord +3, loopback +2, mic +2 | 7 | ✅ record *(ignore-list it if unwanted)* |
| Zoom open, nobody talking yet | zoom +3, title +2 | 5 | ⏸ at threshold — the 10 s sustain requirement holds it until speech appears |

The listen-only all-hands is why the rule isn't simply "speech on both tracks": that would
miss every meeting where you never unmute. The score reaches the threshold through *app
identity + remote speech* instead.

---

## 6. Notifications — and the feedback loop hiding in them

Using `windows-toasts` (WinRT `ToastNotificationManager`, supports buttons and click
callbacks; the installer registers an AppUserModelID so toasts carry the app's name and
persist in Action Center).

| Moment | Toast | Buttons |
|---|---|---|
| **Recording started** | *"Recording — Zoom Meeting"* | **[Stop] [Not a meeting]** |
| **Recording ended** | *"Meeting ended — 47 min. Transcribing…"* | [Open] |
| **Summary ready** | *"Summary ready — Weekly Sync"* | [Open] [Email] |
| **Something failed** | *"Transcription failed — audio is safe"* | [Retry] [Open] |
| **Near miss** *(optional, off by default)* | *"Didn't record Teams at 14:03"* | [It was a meeting] |

Two of those buttons are the entire learning mechanism:

- **"Not a meeting"** → stops and deletes the recording, and offers to add that process to the ignore list. False positives get fixed once, permanently.
- **"It was a meeting"** on a near miss → adds the process to the known-conferencing list, so it scores +3 next time. **This is how false negatives become visible at all** — otherwise a missed meeting produces no artifact and no signal, and you never find out the detector is failing.

The start toast is also the consent surface. It appears within a second of commit, so
nothing is ever recorded silently.

---

## 7. Ending, and the ignore list

**End signals**, in priority order:
1. **Mic released** (`LastUsedTimeStop != 0`) → start the 60-second grace. Re-acquired inside it → same meeting continues (survives network blips, reconnects, a Zoom crash-and-rejoin).
2. **Dual-track silence** beyond ~5 minutes while the mic is still held → end and trim the trailing silence. Catches the very common "left Zoom open after the call".
3. **Process exit** → immediate end.
4. **Max duration cap** (configurable, default 4 h) → end and start a new meeting if activity continues.

**Ignore list** ships pre-populated with the usual mic-squatters — voice assistants,
Windows Voice Access, headset/《virtual mic》 utilities, streaming tools — and grows from
the *"not a meeting"* button.

**A privacy note worth stating plainly:** Zoom and Teams mute in *software*. When you mute,
the app keeps the microphone open, so our `me` track keeps capturing your room. That is
correct behaviour for a recorder and surprising to users — which is why the tray icon is
red the whole time and Pause actually stops writing to disk, rather than relying on the
meeting app's mute.

---

## 8. Observability — because a detector you can't debug is a detector you can't tune

- **Every commit stores its evidence vector** in `meta.json`, rendered in the UI as *"Recorded because: Zoom held the microphone, both sides were speaking, window titled 'Zoom Meeting'."*
- **Near misses are logged** — any wake that peaked above a lower watermark (say 3) but never committed, with its evidence and the process name, kept for 7 days. This is the only way false negatives become visible.
- **A detector log** in the UI showing the last 50 wakes and their outcomes. During the first weeks this is the tuning instrument; afterwards it's how you answer "why didn't it record my 1:1?"

---

## 9. Tray states

| Icon | State |
|---|---|
| ⚪ grey | Idle — waiting on the registry event |
| 🟡 amber | Wake — evaluating, pre-roll buffering, nothing on disk |
| 🔴 red | **Recording** — never not visible while capturing |
| 🔵 blue | Processing — transcribing or summarizing |
| ⚠️ badge | Something needs attention |

Menu: Start/Stop, Pause, Open dashboard, Don't detect for 1 hour (a mute button for a
private call — the thing people will actually want), Quit.

---

## 10. Packages

All verified available on PyPI for Python 3.13 / Windows:

| Need | Package |
|---|---|
| Registry watch, `EnumWindows`, FILETIME | **pywin32** 312 |
| Audio session enumeration | **pycaw** 20251023 (+ **comtypes** 1.4.16) |
| Process names | **psutil** 7.2.2 |
| Toasts with buttons and callbacks | **windows-toasts** 1.3.1 |
| VAD | **onnxruntime** + Silero (already present via faster-whisper) |

---

## 11. Test plan

Detection is empirical — it cannot be reasoned into correctness.

1. **A labelled corpus of wakes.** For two weeks, run in *shadow mode*: full detection, evidence logging, and pre-roll, but **no committing and nothing written to disk**. Every wake gets logged with its score and what actually happened. Tune the threshold against real data before it can ever record wrongly.
2. **Scripted positives:** Zoom, Teams, Meet in Chrome and in Edge, Slack huddle, a phone-bridge call on speaker, a listen-only webinar, a meeting you join 10 minutes late, a reconnect mid-call.
3. **Scripted negatives:** YouTube, Spotify, a WhatsApp voice note, dictation, a solo Zoom room with no one else, a Teams call that rings and is declined.
4. **Endurance:** a 3-hour meeting, back-to-back meetings 2 minutes apart, sleep mid-meeting, unplugging a USB headset mid-meeting (device change → stream reopen without losing the meeting).

Item 1 is the important one. Shadow mode costs one flag and turns a guessy heuristic into
a tuned one, before it can ever embarrass you.
