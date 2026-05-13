# errors.py
"""Structured error type for booking-bot internals.

`BotError` is the shared shape used by reliability code (retry loops, the app
catch-all, the per-source error logger) when an exception needs to carry more
than a string. Per-call sites can keep raising native exceptions
(`requests.exceptions.ConnectionError`, `pyodbc.Error`, etc.) and wrap them in
`BotError` only at the boundary where retry / fallback / logging decisions are
made.
"""


class BotError(Exception):
    """Structured error for booking-bot internals.

    Attributes:
        code:       Short stable dotted identifier — e.g. 'llm.network',
                    'llm.upstream', 'db.query', 'crm.auth', 'app.unhandled'.
                    Stored verbatim in the `errors.error_type` column when
                    logged, so the dashboard's group-by-source/type breakdowns
                    treat related failures together.
        message:    Human-readable summary. Safe to log; NOT shown to the
                    patient (the user-facing reply still comes from the
                    existing safety_net / fallback paths).
        retryable:  Advisory flag. True if the caller can sensibly try again
                    (transient network error, stale DB connection, etc.).
                    The class itself never retries — retry-loop code reads
                    this flag to decide whether to loop.
        cause:      Optional original exception, preserved for use in
                    best-effort logging paths where the live traceback may
                    not be available. `raise BotError(...) from cause` also
                    keeps Python's standard `__cause__` chain.
        context:    Optional dict of extra fields, passed through to
                    `log_error(..., context=...)` for structured triage.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        cause: BaseException | None = None,
        context: dict | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.cause = cause
        self.context = context or {}

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"

    def __repr__(self) -> str:
        return (
            f"BotError(code={self.code!r}, message={self.message!r}, "
            f"retryable={self.retryable!r})"
        )
