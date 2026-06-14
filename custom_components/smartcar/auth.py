from abc import ABC, abstractmethod
import logging

from aiohttp import ClientResponse, ClientSession

_LOGGER = logging.getLogger(__name__)


class AbstractAuth(ABC):
    """Abstract class to make authenticated requests."""

    def __init__(self, websession: ClientSession, host: str) -> None:
        """Initialize the auth."""
        self._websession = websession
        self._host = host

    @abstractmethod
    async def async_get_access_token(self) -> str:
        """Return a valid access token."""

    async def request(
        self,
        method: str,
        path: str,
        version: str = "3",
        host: str | None = None,
        **kwargs,  # noqa: ANN003
    ) -> ClientResponse:
        """Make a request.

        Args:
            method: HTTP method (get, post, etc.)
            path: API path (without /v{version}/ prefix)
            version: API version string, defaults to "3" (v3 API)
            host: Override the default host (e.g. for management API)
            **kwargs: Additional arguments passed to aiohttp request

        Returns:
            The client response.
        """
        access_token = await self.async_get_access_token()
        headers = dict(kwargs.pop("headers", {}))
        headers["authorization"] = f"Bearer {access_token}"

        target_host = host or self._host

        _LOGGER.debug(
            "HTTP %s request %s/v%s/%s %r headers=%r",
            method,
            target_host,
            version,
            path,
            kwargs,
            headers,
        )

        return await self._websession.request(
            method,
            f"{target_host}/v{version}/{path}",
            **kwargs,
            headers=headers,
        )
