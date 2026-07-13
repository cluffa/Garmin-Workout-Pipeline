"""Shared Garmin client wrapper with safe_call for generic API access."""

from __future__ import annotations

import logging
from typing import Any

from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

from garmin_pipeline.sync import GarminSync

logger = logging.getLogger(__name__)


class GarminAPIError(Exception):
    """Custom exception for Garmin API errors."""

    def __init__(self, message: str, original_error: Exception | None = None) -> None:
        self.message = message
        self.original_error = original_error
        super().__init__(self.message)


class GarminClient:
    """Wrapper around GarminSync with safe_call for accessing any Garmin API method.

    Usage:
        client = GarminClient()
        client.login()
        activities = client.safe_call("get_activities_by_date", "2026-07-01", "2026-07-13", "")
    """

    def __init__(self, sync: GarminSync | None = None) -> None:
        self._sync = sync
        self._logged_in = False

    @property
    def garmin(self) -> Any:
        """The underlying garminconnect.Garmin instance."""
        if self._sync is None:
            self._sync = GarminSync()
        return self._sync.client

    def login(self) -> None:
        """Authenticate (cached tokens make this fast after first time)."""
        if self._sync is None:
            self._sync = GarminSync()
        if not self._logged_in:
            self._sync.login()
            self._logged_in = True

    def safe_call(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        """Safely call any Garmin client method with error handling.

        Args:
            method_name: Name of the Garmin client method to call.
            *args: Positional arguments for the method.
            **kwargs: Keyword arguments for the method.

        Returns:
            Method result.

        Raises:
            GarminAPIError: On any API or network error.
        """
        self.login()
        try:
            method = getattr(self.garmin, method_name)
            return method(*args, **kwargs)
        except AttributeError as e:
            raise GarminAPIError(
                f"Method '{method_name}' not found on Garmin client", original_error=e
            ) from e
        except GarminConnectAuthenticationError as e:
            raise GarminAPIError(
                "Authentication failed. Check GARMIN_EMAIL and GARMIN_PASSWORD.", original_error=e
            ) from e
        except GarminConnectTooManyRequestsError as e:
            raise GarminAPIError(
                "Rate limit exceeded. Please wait a few minutes.", original_error=e
            ) from e
        except GarminConnectConnectionError as e:
            raise GarminAPIError(f"Garmin API error: {e}", original_error=e) from e
        except Exception as e:
            raise GarminAPIError(f"Unexpected error: {e}", original_error=e) from e


# Module-level shared client (lazy-init + cached login)
_client: GarminClient | None = None


def get_client() -> GarminClient:
    """Get or create the shared GarminClient singleton."""
    global _client
    if _client is None:
        _client = GarminClient()
    return _client
