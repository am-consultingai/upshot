"""Is a downloaded installer Authenticode-signed by the pinned certificate? (D87)

The manifest already pins the installer's SHA-256 under an Ed25519 signature; this is the
second, independent check: Windows confirms the file's signature is intact, and the
certificate that made it has the thumbprint the manifest names.

The certificate is self-signed for now (D87), so Windows cannot chain it to a trusted
root. ``CERT_E_UNTRUSTEDROOT`` and ``CERT_E_CHAINING`` are therefore accepted, because
the thumbprint is what is trusted; a bad digest, no signature, or any other failure is
not. Off Windows there is nothing to check and ``check`` says so.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

#: WinVerifyTrust results that still mean "the signature over this file is intact".
CERT_E_UNTRUSTEDROOT = 0x800B0109
CERT_E_CHAINING = 0x800B010A
ACCEPTED = {0, CERT_E_UNTRUSTEDROOT, CERT_E_CHAINING}


@dataclass(frozen=True)
class SignerCheck:
    ok: bool
    reason: str
    thumbprint: str | None = None


def check(path: Path, expected_thumbprint: str) -> SignerCheck:
    if sys.platform != "win32":
        return SignerCheck(False, "not Windows: no Authenticode check")
    try:
        status = _win_verify_trust(path)
        if status not in ACCEPTED:
            return SignerCheck(False, f"WinVerifyTrust refused the signature (0x{status:08X})")
        thumbprint = _signer_thumbprint(path)
    except OSError as exc:
        return SignerCheck(False, f"the signature could not be read: {exc}")
    if thumbprint != expected_thumbprint.upper():
        return SignerCheck(False, "signed by another certificate", thumbprint)
    return SignerCheck(True, "signed by the pinned certificate", thumbprint)


# ---------------------------------------------------------------- Windows only

if sys.platform == "win32":

    def _win_verify_trust(path: Path) -> int:  # pragma: no cover - Windows API
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        class WINTRUST_FILE_INFO(ctypes.Structure):
            _fields_ = [
                ("cbStruct", wintypes.DWORD),
                ("pcwszFilePath", wintypes.LPCWSTR),
                ("hFile", wintypes.HANDLE),
                ("pgKnownSubject", ctypes.POINTER(GUID)),
            ]

        class WINTRUST_DATA(ctypes.Structure):
            _fields_ = [
                ("cbStruct", wintypes.DWORD),
                ("pPolicyCallbackData", ctypes.c_void_p),
                ("pSIPClientData", ctypes.c_void_p),
                ("dwUIChoice", wintypes.DWORD),
                ("fdwRevocationChecks", wintypes.DWORD),
                ("dwUnionChoice", wintypes.DWORD),
                ("pFile", ctypes.POINTER(WINTRUST_FILE_INFO)),
                ("dwStateAction", wintypes.DWORD),
                ("hWVTStateData", wintypes.HANDLE),
                ("pwszURLReference", wintypes.LPWSTR),
                ("dwProvFlags", wintypes.DWORD),
                ("dwUIContext", wintypes.DWORD),
                ("pSignatureSettings", ctypes.c_void_p),
            ]

        # WINTRUST_ACTION_GENERIC_VERIFY_V2
        action = GUID(
            0x00AAC56B,
            0xCD44,
            0x11D0,
            (ctypes.c_ubyte * 8)(0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE),
        )
        file_info = WINTRUST_FILE_INFO(ctypes.sizeof(WINTRUST_FILE_INFO), str(path), None, None)
        data = WINTRUST_DATA()
        data.cbStruct = ctypes.sizeof(WINTRUST_DATA)
        data.dwUIChoice = 2  # WTD_UI_NONE
        data.fdwRevocationChecks = 0  # WTD_REVOKE_NONE
        data.dwUnionChoice = 1  # WTD_CHOICE_FILE
        data.pFile = ctypes.pointer(file_info)
        data.dwStateAction = 1  # WTD_STATEACTION_VERIFY
        # No revocation lookups and no network: the thumbprint is what is trusted.
        data.dwProvFlags = 0x10 | 0x1000  # WTD_REVOCATION_CHECK_NONE | WTD_CACHE_ONLY_URL_RETRIEVAL

        wintrust = ctypes.WinDLL("wintrust", use_last_error=True)
        wintrust.WinVerifyTrust.restype = ctypes.c_long
        wintrust.WinVerifyTrust.argtypes = [wintypes.HWND, ctypes.POINTER(GUID), ctypes.c_void_p]
        try:
            status = wintrust.WinVerifyTrust(None, ctypes.byref(action), ctypes.byref(data))
        finally:
            data.dwStateAction = 2  # WTD_STATEACTION_CLOSE
            wintrust.WinVerifyTrust(None, ctypes.byref(action), ctypes.byref(data))
        return int(status) & 0xFFFFFFFF

    def _signer_thumbprint(path: Path) -> str:  # pragma: no cover - Windows API
        import ctypes
        from ctypes import wintypes

        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        CERT_QUERY_OBJECT_FILE = 1
        CERT_QUERY_CONTENT_FLAG_PKCS7_SIGNED_EMBED = 1 << 10
        CERT_QUERY_FORMAT_FLAG_BINARY = 1 << 1
        CMSG_SIGNER_CERT_INFO_PARAM = 7
        ENCODING = 0x00000001 | 0x00010000  # X509_ASN_ENCODING | PKCS_7_ASN_ENCODING
        CERT_SHA1_HASH_PROP_ID = 3

        crypt32.CryptQueryObject.restype = wintypes.BOOL
        crypt32.CryptQueryObject.argtypes = [
            wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ]  # fmt: skip
        crypt32.CryptMsgGetParam.restype = wintypes.BOOL
        crypt32.CryptMsgGetParam.argtypes = [
            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
            ctypes.POINTER(wintypes.DWORD),
        ]  # fmt: skip
        crypt32.CertGetSubjectCertificateFromStore.restype = ctypes.c_void_p
        crypt32.CertGetSubjectCertificateFromStore.argtypes = [
            ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
        ]  # fmt: skip
        crypt32.CertGetCertificateContextProperty.restype = wintypes.BOOL
        crypt32.CertGetCertificateContextProperty.argtypes = [
            ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD),
        ]  # fmt: skip
        crypt32.CertFreeCertificateContext.argtypes = [ctypes.c_void_p]
        crypt32.CertCloseStore.argtypes = [ctypes.c_void_p, wintypes.DWORD]
        crypt32.CryptMsgClose.argtypes = [ctypes.c_void_p]

        encoding, content, fmt = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
        store, msg = ctypes.c_void_p(), ctypes.c_void_p()
        if not crypt32.CryptQueryObject(
            CERT_QUERY_OBJECT_FILE, ctypes.c_wchar_p(str(path)),
            CERT_QUERY_CONTENT_FLAG_PKCS7_SIGNED_EMBED, CERT_QUERY_FORMAT_FLAG_BINARY, 0,
            ctypes.byref(encoding), ctypes.byref(content), ctypes.byref(fmt),
            ctypes.byref(store), ctypes.byref(msg), None,
        ):  # fmt: skip
            raise ctypes.WinError(ctypes.get_last_error())
        context = None
        try:
            size = wintypes.DWORD(0)
            if not crypt32.CryptMsgGetParam(
                msg, CMSG_SIGNER_CERT_INFO_PARAM, 0, None, ctypes.byref(size)
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            info = ctypes.create_string_buffer(size.value)
            if not crypt32.CryptMsgGetParam(
                msg, CMSG_SIGNER_CERT_INFO_PARAM, 0, info, ctypes.byref(size)
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            context = crypt32.CertGetSubjectCertificateFromStore(store, ENCODING, info)
            if not context:
                raise ctypes.WinError(ctypes.get_last_error())
            digest = (ctypes.c_ubyte * 20)()
            length = wintypes.DWORD(20)
            if not crypt32.CertGetCertificateContextProperty(
                context, CERT_SHA1_HASH_PROP_ID, digest, ctypes.byref(length)
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            return bytes(digest[: length.value]).hex().upper()
        finally:
            if context:
                crypt32.CertFreeCertificateContext(context)
            crypt32.CertCloseStore(store, 0)
            crypt32.CryptMsgClose(msg)

else:

    def _win_verify_trust(path: Path) -> int:
        raise OSError("Authenticode is checked on Windows only")

    def _signer_thumbprint(path: Path) -> str:
        raise OSError("Authenticode is checked on Windows only")
