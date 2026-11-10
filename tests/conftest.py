"""Fixtures for testing jellyfin_cast against a real Home Assistant core
(pytest-homeassistant-custom-component). The Windows shims only apply on Windows."""

from __future__ import annotations

import logging
import socket
import sys
from typing import Any

import pytest
import pytest_socket

# Import this directory's custom_components package before the hass fixture does:
# otherwise pytest-homeassistant-custom-component's testing_config/custom_components
# gets cached in sys.modules first and HA never sees the override under test.
import custom_components  # noqa: F401
from homeassistant.helpers import frame as ha_frame

if sys.platform == "win32":
    # The Windows event loop wakes itself through a loopback socket pair, which
    # pytest-socket refuses. Let only socketpair() make real sockets.
    _real_socketpair = socket.socketpair

    def _socketpair_for_event_loop(*args: Any, **kwargs: Any) -> Any:
        guarded = socket.socket
        socket.socket = pytest_socket._true_socket  # noqa: SLF001
        try:
            return _real_socketpair(*args, **kwargs)
        finally:
            socket.socket = guarded

    socket.socketpair = _socketpair_for_event_loop

    # HA spots custom integrations in report_usage() by "/custom_components/" in
    # frame paths, which Windows writes with backslashes.
    _real_report_usage = ha_frame.report_usage

    def _report_usage(what: str, **kwargs: Any) -> None:
        frame = sys._getframe(1)  # noqa: SLF001
        while frame is not None:
            if "\\custom_components\\" in frame.f_code.co_filename:
                behavior = kwargs.get(
                    "custom_integration_behavior", ha_frame.ReportBehavior.LOG
                )
                if behavior is ha_frame.ReportBehavior.ERROR:
                    raise RuntimeError(f"Custom integration code {what}")
                if behavior is ha_frame.ReportBehavior.LOG:
                    logging.getLogger(__name__).warning(
                        "Custom integration code %s (%s:%s)",
                        what,
                        frame.f_code.co_filename,
                        frame.f_lineno,
                    )
                return
            frame = frame.f_back
        _real_report_usage(what, **kwargs)

    ha_frame.report_usage = _report_usage


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: Any) -> None:
    """Load integrations from custom_components/."""
