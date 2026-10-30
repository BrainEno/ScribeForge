"""Cooperative cancellation shared by the CLI, pipeline and worker adapters."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from threading import Event


class ProcessingCancelled(Exception):
    """Raised when a user explicitly cancels an in-flight processing run."""


class CancellationToken:
    """Thread-safe flag used to request cancellation without killing the CLI."""

    def __init__(self) -> None:
        self._event = Event()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise ProcessingCancelled("processing cancelled by user")


_current_token: ContextVar[CancellationToken | None] = ContextVar(
    "scribeforge_cancellation_token", default=None
)


@contextmanager
def cancellation_scope(token: CancellationToken) -> Iterator[None]:
    """Make ``token`` available to synchronous pipeline and adapter calls."""

    context_token: Token[CancellationToken | None] = _current_token.set(token)
    try:
        yield
    finally:
        _current_token.reset(context_token)


def current_cancellation_token() -> CancellationToken | None:
    """Return the active cancellation token, if this call is in a run scope."""

    return _current_token.get()


def raise_if_cancelled() -> None:
    """Raise at stage/page boundaries when the active run was cancelled."""

    token = current_cancellation_token()
    if token is not None:
        token.raise_if_cancelled()
