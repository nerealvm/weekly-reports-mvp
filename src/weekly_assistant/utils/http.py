import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class JsonHttpError(RuntimeError):
    def __init__(self, method: str, url: str, status: int | None, body: str):
        self.method = method
        self.url = url
        self.status = status
        self.body = body
        super().__init__(f"{method} {url} failed with status={status}: {body[:500]}")


@dataclass(frozen=True)
class JsonHttpClient:
    timeout_seconds: int = 30

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        full_url = _with_params(url, params)
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request_headers = {"Accept": "application/json", **(headers or {})}
        if payload is not None:
            request_headers["Content-Type"] = "application/json"
        request = Request(full_url, data=body, headers=request_headers, method=method.upper())
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                response_body = response.read().decode("utf-8")
        except HTTPError as error:
            error_body = error.read().decode("utf-8", errors="replace")
            raise JsonHttpError(method, full_url, error.code, error_body) from error
        except URLError as error:
            raise JsonHttpError(method, full_url, None, str(error.reason)) from error
        if not response_body:
            return None
        return json.loads(response_body)


def _with_params(url: str, params: dict[str, Any] | None) -> str:
    clean_params = {key: value for key, value in (params or {}).items() if value is not None and value != ""}
    if not clean_params:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{urlencode(clean_params)}"
