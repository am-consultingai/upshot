"""The public keys an update manifest must be signed with (D87).

Ed25519, raw 32 bytes, base64. The private halves never enter the repository
(``scripts/release_key.py``). Two are built in so a key can be retired without stranding
installs: ``update-2026-10`` signs releases; ``update-spare`` is kept offline, and signs
the release that replaces both should the first be lost or leak.
"""

from __future__ import annotations

#: (name, public key). A manifest signed by any of them is accepted.
PUBLIC_KEYS: tuple[tuple[str, str], ...] = (
    ("update-2026-10", "/Qa5ShbzFeN60VqXfLYYGEszjkrALU2pz3Oj4T5eqV8="),
    ("update-spare", "hxvaAViq7vZy0VXd7KFx1jorGoXI4AnMaSN/m1VLqAQ="),
)
