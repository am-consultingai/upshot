"""Crash reports, with consent, and nothing from a meeting in them (D87).

The official build carries a Sentry DSN (``app.version``); a build from source has none
and sends nothing. Nothing is sent either until the user has said yes, once, in setup
(``diagnostics.crash_reports``), and the answer can be changed in Settings at any time.

Every report is **built**, not copied: an allowlist of fields, a stack of module and
function names and line numbers (never local variables, never source lines), and an
exception message passed through ``scrub``. Logs are never attached: their lines carry
the meeting slug, and the slug holds the title.

- ``scrub``: what is removed from any text that leaves, and how frames are made.
- ``client``: the envelope sent to Sentry, and an outbox for when there is no network.
- ``reporter``: consent, the event, one report per crash per day, the last report kept.
- ``native``: crashes Python never sees (a native library, the memory running out),
  reported at the next start.
- ``api``: ``/api/diagnostics``.

No SDK: Sentry's own would have to be switched off piece by piece to send this little,
and a dependency upgrade could switch something back on. This is short enough to read.
"""
