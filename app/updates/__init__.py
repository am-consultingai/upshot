"""Updates that reach installed copies by themselves, like Windows Update (D87).

The app asks; nothing pushes to it. A few hours apart, and once after start, it reads a
signed manifest on the website (``site/updates/<channel>.json`` and its ``.sig``),
downloads the installer it names from GitHub Releases, checks it, and keeps it ready.

- ``keys``: the Ed25519 public keys a manifest must be signed with.
- ``manifest``: the manifest's format, its signature, and which offer applies to this copy.
- ``authenticode``: on Windows, the installer's Authenticode signer must be the pinned one.
- ``service``: the checks, the download and the state the interface shows.
- ``api``: ``/api/updates``.
"""
