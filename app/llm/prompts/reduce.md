version: 1

You are given the per-window extractions from one meeting, in order, plus the meeting's
metadata. Merge them into the final notes.

- De-duplicate: the same decision seen in two windows is one decision.
- Order topics as the conversation moved, not by importance.
- `tldr` is 2–6 lines a reader can act on without opening the rest.
- `follow_up_email` is what ME would send afterwards: subject plus a short markdown body.
