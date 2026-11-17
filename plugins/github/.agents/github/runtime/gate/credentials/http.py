from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import IO, Protocol, override

from credentials.model import CredentialError

LIMIT = 4 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class Response:
    status: int
    headers: Mapping[str, str]
    body: bytes = field(repr=False)

    def json(self) -> object:
        try:
            return json.loads(self.body)
        except ValueError:
            raise CredentialError(
                "credential-probe-invalid: the server returned invalid JSON"
            ) from None


class Transport(Protocol):
    def __call__(self, url: str, headers: Mapping[str, str]) -> Response: ...


class NoRedirect(urllib.request.HTTPRedirectHandler):
    @override
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: http.client.HTTPMessage,
        newurl: str,
    ) -> None:
        return None


def proxy_configuration(environment: Mapping[str, str]) -> dict[str, str]:
    configured = {
        name.casefold().removesuffix("_proxy"): value
        for name, value in environment.items()
        if name.casefold() in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}
    }
    for name, value in environment.items():
        if name in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}:
            configured[name.removesuffix("_proxy")] = value
    if "all" in configured:
        for scheme in ("http", "https"):
            configured.setdefault(scheme, configured["all"])
    return {name: value for name, value in configured.items() if value}


def request(
    url: str,
    headers: Mapping[str, str],
    *,
    proxies: Mapping[str, str] | None = None,
) -> Response:
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler(dict(proxies or {})), NoRedirect()
    )
    try:
        with opener.open(
            urllib.request.Request(url, headers=dict(headers)), timeout=20
        ) as response:
            result = Response(
                response.status,
                {key.casefold(): value for key, value in response.headers.items()},
                response.read(LIMIT + 1),
            )
    except urllib.error.HTTPError as error:
        result = Response(
            error.code,
            {key.casefold(): value for key, value in error.headers.items()},
            b"",
        )
        error.close()
    except (OSError, urllib.error.URLError, http.client.HTTPException):
        raise CredentialError(
            "credential-network-unavailable: check connectivity "
            "to the declared service and retry"
        ) from None
    if len(result.body) > LIMIT:
        raise CredentialError(
            "credential-probe-invalid: the server response exceeded the limit"
        )
    return result


def require_success(response: Response, action: str) -> None:
    if response.status == 401:
        raise CredentialError(f"credential-invalid: {action}")
    if response.status == 429 or (
        response.status == 403
        and (
            response.headers.get("x-ratelimit-remaining") == "0"
            or response.headers.get("retry-after")
        )
    ):
        raise CredentialError(
            "credential-rate-limited: wait for the service's rate-limit reset and retry"
        )
    if (
        response.status == 403
        and "required" in response.headers.get("x-github-sso", "").casefold()
    ):
        raise CredentialError(
            "credential-sso-required: authorize this credential "
            "for the declared GitHub organization"
        )
    if response.status in {403, 404}:
        raise CredentialError(
            "credential-access-denied: ask the resource owner to grant "
            "the declared access or approve organization SSO"
        )
    if not 200 <= response.status < 300:
        raise CredentialError(
            "credential-service-unavailable: the service rejected the check; "
            "retry when it is available"
        )
