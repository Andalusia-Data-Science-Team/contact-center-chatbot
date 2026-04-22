"""
Dynamics 365 CRM connector — pulls doctor reference data (walk-in/cash price,
specialty, etc.) from the CRM SQL/TDS endpoint using Azure AD auth.

Auth strategy (MSAL, public client):
  1. Silent — read a cached refresh token from disk (works after first login).
  2. Username+password — headless fallback when CRM_PASSWORD is set and MFA
     is disabled on the account.
  3. Interactive — opens a browser so the user can complete MFA. Only useful
     on a dev machine; on the deployed server the token cache must already
     exist (run scripts/check_crm_prices.py locally, then copy the cache).

Connection: pyodbc with the access token via SQL_COPT_SS_ACCESS_TOKEN.
Cache: in-memory dict with a TTL (default 24h) — CRM data changes slowly
and we never want to block the booking flow on CRM latency.
"""
import atexit
import os
import struct
import threading
import time
from typing import Optional

import pyodbc

from config.settings import (
    CRM_CLIENT_ID,
    CRM_DOCTOR_TABLE,
    CRM_FEE_TABLE,
    CRM_PASSWORD,
    CRM_PRICE_CACHE_TTL_SECONDS,
    CRM_SERVER,
    CRM_TENANT,
    CRM_USERNAME,
    DB_DRIVER,
)

# Where MSAL persists the refresh token between runs. Override with
# MSAL_TOKEN_CACHE_PATH if you want a different location (e.g. shared volume).
_MSAL_CACHE_PATH = os.environ.get(
    "MSAL_TOKEN_CACHE_PATH",
    os.path.join(os.path.expanduser("~"), ".andalusia_crm_cache.bin"),
)
# Enable the interactive (browser) flow on first run. Disable on the server.
_ALLOW_INTERACTIVE = os.environ.get("CRM_ALLOW_INTERACTIVE", "1") != "0"

SQL_COPT_SS_ACCESS_TOKEN = 1256

_token_cache: dict = {"token": None, "expires_at": 0.0}
_token_lock = threading.Lock()

_doctor_cache: dict = {"doctors": [], "loaded_at": 0.0, "failed": False}
_doctor_lock = threading.Lock()


def _crm_host() -> str:
    # "org2f45e702.crm4.dynamics.com,5558" → "org2f45e702.crm4.dynamics.com"
    return (CRM_SERVER or "").split(",")[0].strip()


def _is_configured() -> bool:
    # Server is the only hard requirement — password is optional when a cached
    # refresh token or interactive login is used.
    return bool(CRM_SERVER and _crm_host())


def _load_msal_cache(msal_mod):
    cache = msal_mod.SerializableTokenCache()
    try:
        if os.path.exists(_MSAL_CACHE_PATH):
            with open(_MSAL_CACHE_PATH, "r", encoding="utf-8") as f:
                cache.deserialize(f.read())
    except Exception as e:
        print(f"[CRM] token cache unreadable ({e}); starting fresh")

    def _persist():
        if cache.has_state_changed:
            try:
                os.makedirs(os.path.dirname(_MSAL_CACHE_PATH) or ".", exist_ok=True)
                with open(_MSAL_CACHE_PATH, "w", encoding="utf-8") as f:
                    f.write(cache.serialize())
            except Exception as e:
                print(f"[CRM] could not persist token cache: {e}")

    atexit.register(_persist)
    return cache, _persist


def _get_token() -> Optional[str]:
    """Acquire a bearer token for the CRM TDS endpoint. Caches in memory until near expiry."""
    if not _is_configured():
        return None

    now = time.time()
    with _token_lock:
        if _token_cache["token"] and _token_cache["expires_at"] > now + 60:
            return _token_cache["token"]

        import msal

        authority = f"https://login.microsoftonline.com/{CRM_TENANT}"
        scope = [f"https://{_crm_host()}/.default"]

        cache, persist = _load_msal_cache(msal)
        app = msal.PublicClientApplication(
            CRM_CLIENT_ID, authority=authority, token_cache=cache,
        )

        result = None

        # 1. Silent — refresh token from disk cache
        accounts = app.get_accounts(username=CRM_USERNAME or None)
        if accounts:
            result = app.acquire_token_silent(scope, account=accounts[0])

        # 2. Username + password — only if password was provided
        if (not result or "access_token" not in result) and CRM_USERNAME and CRM_PASSWORD:
            result = app.acquire_token_by_username_password(
                username=CRM_USERNAME, password=CRM_PASSWORD, scopes=scope,
            )

        # 3. Interactive — opens a browser (useful for MFA-enabled accounts)
        if (not result or "access_token" not in result) and _ALLOW_INTERACTIVE:
            print("[CRM] opening browser for interactive login…")
            result = app.acquire_token_interactive(
                scopes=scope, login_hint=CRM_USERNAME or None,
            )

        if not result or "access_token" not in result:
            raise RuntimeError(
                f"CRM auth failed: {result.get('error_description') if result else 'no result'}"
            )

        persist()
        _token_cache["token"] = result["access_token"]
        _token_cache["expires_at"] = now + int(result.get("expires_in", 3599))
        return _token_cache["token"]


def _get_connection() -> pyodbc.Connection:
    token = _get_token()
    if not token:
        raise RuntimeError("CRM not configured")

    token_bytes = token.encode("utf-16-le")
    token_struct = struct.pack(f"=i{len(token_bytes)}s", len(token_bytes), token_bytes)

    # Dynamics TDS endpoint requires TLS and a Database= value equal to the
    # org name (first DNS label of the server, e.g. "org2f45e702"). Without
    # these the driver opens the socket but the server drops it on first
    # execute, surfacing as "Communication link failure" on SELECT 1.
    org = _crm_host().split(".")[0]
    conn_str = (
        f"DRIVER={{{DB_DRIVER}}};"
        f"SERVER={CRM_SERVER};"
        f"DATABASE={org};"
        f"Encrypt=yes;"
        f"TrustServerCertificate=no;"
    )
    conn = pyodbc.connect(
        conn_str,
        attrs_before={SQL_COPT_SS_ACCESS_TOKEN: token_struct},
        timeout=60,
    )
    # Give the Dynamics TDS endpoint time to plan+execute — default 0 (no
    # timeout) plays poorly with its habit of idle-closing; 300s is a
    # reasonable upper bound.
    conn.timeout = 300
    return conn


# Walk-in fees live on cr301_table1 (one row per doctor, joined on cr301_doctorkey).
# Try the richest projection first; fall back to the minimum if the tenant's
# view is missing any of the denormalized lookup-name columns.
_QUERY_VARIANTS = [
    # Full: everything useful for debugging + display
    """
    SELECT
        D.[servhub_doctornameen]          AS DoctorEn,
        D.[cr301_doctornamear]            AS DoctorAr,
        D.[cr301_specialtyname]           AS Specialty,
        D.[cr301_subspecialtyname]        AS SubSpecialty,
        D.[cr301_businessunitname]        AS BusinessUnit,
        F.[cr301_walkinconsultationfees]  AS WalkInPrice
    FROM {doctor_table} D
    LEFT JOIN {fee_table} F
        ON D.[cr301_doctorkey] = F.[cr301_doctorkey]
    WHERE D.[cr301_opdflag] = 'OPD'
      AND D.[servhub_doctornameen] IS NOT NULL
    """,
    # Minimal: just what we actually need to show a price
    """
    SELECT
        D.[servhub_doctornameen]          AS DoctorEn,
        D.[cr301_doctornamear]            AS DoctorAr,
        F.[cr301_walkinconsultationfees]  AS WalkInPrice
    FROM {doctor_table} D
    LEFT JOIN {fee_table} F
        ON D.[cr301_doctorkey] = F.[cr301_doctorkey]
    WHERE D.[servhub_doctornameen] IS NOT NULL
    """,
]


def _run_query_with_retry(query: str, max_attempts: int = 3) -> list[dict]:
    """Open a fresh connection and execute the query, retrying on transient errors."""
    last_err = None
    for attempt in range(1, max_attempts + 1):
        try:
            with _get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query)
                cols = [c[0] for c in cursor.description]
                return [dict(zip(cols, r)) for r in cursor.fetchall()]
        except pyodbc.Error as e:
            last_err = e
            msg = str(e).lower()
            # Retry on connection drops / timeouts; fail fast on schema errors.
            transient = any(k in msg for k in (
                "communication link failure", "tcp provider", "timeout expired",
                "connection is closed", "connection reset",
            ))
            if not transient or attempt == max_attempts:
                raise
            backoff = 2 ** (attempt - 1)
            print(f"[CRM] query attempt {attempt} failed ({e}); retrying in {backoff}s")
            time.sleep(backoff)
            # Force a fresh token — stale ones look like comm failures on Dynamics
            with _token_lock:
                _token_cache["token"] = None
                _token_cache["expires_at"] = 0.0
    if last_err:
        raise last_err
    return []


def fetch_all_doctor_prices(force_refresh: bool = False) -> list[dict]:
    """
    Return list of dicts: {DoctorEn, DoctorAr, Specialty, WalkInPrice, BusinessUnit}.
    Cached for CRM_PRICE_CACHE_TTL_SECONDS. Never raises — returns [] on failure
    so the booking flow can still function without price data.
    """
    now = time.time()

    with _doctor_lock:
        cached = _doctor_cache["doctors"]
        age = now - _doctor_cache["loaded_at"]
        if not force_refresh and cached and age < CRM_PRICE_CACHE_TTL_SECONDS:
            return cached
        # If the last attempt failed, back off for 5 minutes before retrying
        if _doctor_cache["failed"] and age < 300:
            return cached

    if not _is_configured():
        return []

    rows: list[dict] = []
    try:
        for i, template in enumerate(_QUERY_VARIANTS, 1):
            try:
                rows = _run_query_with_retry(
                    template.format(doctor_table=CRM_DOCTOR_TABLE, fee_table=CRM_FEE_TABLE)
                )
                break
            except pyodbc.Error as e:
                print(f"[CRM] query variant {i} failed: {e}")
                if i == len(_QUERY_VARIANTS):
                    raise
    except Exception as e:
        print(f"[CRM] fetch_all_doctor_prices failed: {e}")
        with _doctor_lock:
            _doctor_cache["failed"] = True
            _doctor_cache["loaded_at"] = now
        return _doctor_cache["doctors"] or []

    with _doctor_lock:
        _doctor_cache["doctors"] = rows
        _doctor_cache["loaded_at"] = now
        _doctor_cache["failed"] = False
    return rows
