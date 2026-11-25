"""Zero-dependency HMAC-SHA256 request signing and verification (RFC 9421 principles)."""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

HEADER_KEY_ID = "X-Hub-Key-Id"
HEADER_TIMESTAMP = "X-Hub-Timestamp"
HEADER_NONCE = "X-Hub-Nonce"
HEADER_SIGNATURE = "X-Hub-Signature"

SIGNATURE_PREFIX = "v1="
DEFAULT_KEY_ID = "v1"
DEFAULT_MAX_SKEW_SECONDS = 60
DEFAULT_NONCE_TTL_SECONDS = 120
DEFAULT_MAX_NONCE_ENTRIES = 10_000


def build_canonical_string(
    method: str,
    path_with_query: str,
    timestamp: int | str,
    nonce: str,
    raw_body: bytes,
) -> str:
    """Build the canonical HMAC-SHA256 signing string.

    Format: "{METHOD}\\n{PATH_WITH_QUERY}\\n{TIMESTAMP}\\n{NONCE}\\n{sha256_hex(raw_body)}"
    """
    body_hash = hashlib.sha256(raw_body).hexdigest()
    return f"{method.upper()}\n{path_with_query}\n{timestamp}\n{nonce}\n{body_hash}"


def compute_signature(secret: str, canonical: str) -> str:
    """Compute the 'v1=<hex>' HMAC-SHA256 signature for a canonical string."""
    digest = hmac.new(
        secret.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{SIGNATURE_PREFIX}{digest}"


def sign_request(
    secret: str,
    method: str,
    path_with_query: str,
    raw_body: bytes = b"",
    *,
    key_id: str = DEFAULT_KEY_ID,
    timestamp: int | str | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    """Generate signed X-Hub-* headers for an outbound HTTP/WS request."""
    ts_str = str(int(time.time())) if timestamp is None else str(timestamp)
    nonce_str = secrets.token_urlsafe(16) if nonce is None else nonce
    canonical = build_canonical_string(method, path_with_query, ts_str, nonce_str, raw_body)
    signature = compute_signature(secret, canonical)
    return {
        HEADER_KEY_ID: key_id,
        HEADER_TIMESTAMP: ts_str,
        HEADER_NONCE: nonce_str,
        HEADER_SIGNATURE: signature,
    }


@dataclass(frozen=True)
class AuthVerificationResult:
    """Outcome of an HMAC verification check."""

    ok: bool
    reason: str = "ok"
    key_id: str | None = None


class NonceReplayCache:
    """Thread-safe bounded TTL cache rejecting replayed (key_id, nonce) pairs."""

    def __init__(
        self,
        ttl_seconds: float = DEFAULT_NONCE_TTL_SECONDS,
        max_entries: int = DEFAULT_MAX_NONCE_ENTRIES,
    ) -> None:
        self._ttl_seconds = float(ttl_seconds)
        self._max_entries = max(1, int(max_entries))
        self._lock = threading.Lock()
        self._entries: OrderedDict[tuple[str, str], float] = OrderedDict()

    def _prune_expired_locked(self, now: float) -> None:
        while self._entries:
            oldest_key, expires_at = next(iter(self._entries.items()))
            if expires_at <= now:
                self._entries.pop(oldest_key, None)
            else:
                break

    def check_and_store(self, key_id: str, nonce: str, now: float) -> bool:
        """Return True and record the nonce if unseen; return False on replay."""
        cache_key = (key_id, nonce)
        with self._lock:
            self._prune_expired_locked(now)
            existing_expiry = self._entries.get(cache_key)
            if existing_expiry is not None and existing_expiry > now:
                return False
            if cache_key in self._entries:
                self._entries.pop(cache_key, None)
            while len(self._entries) >= self._max_entries:
                self._entries.popitem(last=False)
            self._entries[cache_key] = now + self._ttl_seconds
            return True

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


def _read_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


class HubHMACVerifier:
    """HMAC-SHA256 request verifier with key rotation, skew window, and nonce replay cache."""

    def __init__(
        self,
        *,
        shared_secret: str | None = None,
        prev_secret: str | None = None,
        key_id: str | None = None,
        max_skew_seconds: int | None = None,
        nonce_ttl_seconds: float = DEFAULT_NONCE_TTL_SECONDS,
        max_nonce_entries: int = DEFAULT_MAX_NONCE_ENTRIES,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.shared_secret = (
            os.environ.get("SUNSYNC_HUB_SHARED_SECRET", "")
            if shared_secret is None
            else shared_secret
        )
        self.prev_secret = (
            os.environ.get("SUNSYNC_HUB_PREV_SECRET", "") if prev_secret is None else prev_secret
        )
        self.key_id = (
            (os.environ.get("SUNSYNC_HUB_KEY_ID") or DEFAULT_KEY_ID) if key_id is None else key_id
        )
        self.max_skew_seconds = (
            _read_int_env("SUNSYNC_HUB_MAX_SKEW_SECONDS", DEFAULT_MAX_SKEW_SECONDS)
            if max_skew_seconds is None
            else int(max_skew_seconds)
        )
        self._clock = clock or time.time
        self._nonce_cache = NonceReplayCache(
            ttl_seconds=nonce_ttl_seconds,
            max_entries=max_nonce_entries,
        )

    @property
    def is_enabled(self) -> bool:
        """Return True when SUNSYNC_HUB_SHARED_SECRET is configured (non-empty)."""
        return bool(self.shared_secret)

    def verify(
        self,
        *,
        method: str,
        path: str,
        query: str,
        headers: dict[str, str] | object,
        raw_body: bytes,
    ) -> AuthVerificationResult:
        """Verify HMAC headers for an incoming request. Never logs secrets."""
        if not self.is_enabled:
            return AuthVerificationResult(ok=True, reason="dev_unsigned_mode")

        # Case-insensitive header lookup supporting both Starlette Headers and plain dicts.
        get_header = getattr(headers, "get", None)
        if get_header is None:
            return AuthVerificationResult(ok=False, reason="missing_auth_headers")

        def _hdr(name: str) -> str:
            val = get_header(name)
            if val is None:
                val = get_header(name.lower())
            return str(val).strip() if val is not None else ""

        req_key_id = _hdr(HEADER_KEY_ID)
        req_ts_raw = _hdr(HEADER_TIMESTAMP)
        req_nonce = _hdr(HEADER_NONCE)
        req_sig = _hdr(HEADER_SIGNATURE)

        if not (req_key_id and req_ts_raw and req_nonce and req_sig):
            logger.warning("Hub auth rejected: missing_auth_headers path=%s", path)
            return AuthVerificationResult(ok=False, reason="missing_auth_headers")

        if len(req_nonce) > 256:
            logger.warning("Hub auth rejected: invalid_nonce path=%s", path)
            return AuthVerificationResult(ok=False, reason="invalid_nonce")

        try:
            req_ts = int(req_ts_raw)
        except ValueError:
            logger.warning("Hub auth rejected: invalid_timestamp path=%s", path)
            return AuthVerificationResult(ok=False, reason="invalid_timestamp")

        now = self._clock()
        if abs(now - req_ts) > self.max_skew_seconds:
            logger.warning("Hub auth rejected: timestamp_skew_exceeded path=%s", path)
            return AuthVerificationResult(ok=False, reason="timestamp_skew_exceeded")

        if not req_sig.startswith(SIGNATURE_PREFIX):
            logger.warning("Hub auth rejected: malformed_signature path=%s", path)
            return AuthVerificationResult(ok=False, reason="malformed_signature")

        path_with_query = f"{path}?{query}" if query else path
        canonical = build_canonical_string(method, path_with_query, req_ts_raw, req_nonce, raw_body)

        expected_primary = compute_signature(self.shared_secret, canonical)
        matched = hmac.compare_digest(req_sig, expected_primary)
        if not matched and self.prev_secret:
            expected_prev = compute_signature(self.prev_secret, canonical)
            matched = hmac.compare_digest(req_sig, expected_prev)

        if not matched:
            logger.warning(
                "Hub auth rejected: invalid_signature key_id=%s path=%s", req_key_id, path
            )
            return AuthVerificationResult(ok=False, reason="invalid_signature")

        if not self._nonce_cache.check_and_store(req_key_id, req_nonce, now):
            logger.warning("Hub auth rejected: replay_detected key_id=%s path=%s", req_key_id, path)
            return AuthVerificationResult(ok=False, reason="replay_detected")

        return AuthVerificationResult(ok=True, reason="verified", key_id=req_key_id)
