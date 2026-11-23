"""Read browser cookie exports and save store sessions as JSON."""

from __future__ import annotations

import json
import re
from http.cookiejar import Cookie, CookieJar, DefaultCookiePolicy
from typing import TYPE_CHECKING

from requests.cookies import RequestsCookieJar

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

_DOMAIN = re.compile(r"\.?[a-z0-9-]+(?:\.[a-z0-9-]+)*", re.IGNORECASE | re.ASCII)
_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")
_HTTP_ONLY_PREFIX = "#HttpOnly_"


def make_cookie(
    domain: str,
    name: str,
    value: str,
    *,
    path: str = "/",
    secure: bool = False,
    http_only: bool = False,
    expires: int | None = None,
    host_only: bool | None = None,
) -> Cookie:
    if host_only is None:
        host_only = not domain.startswith(".")
    domain = domain.removeprefix(".")
    if not host_only:
        domain = "." + domain
    if not _DOMAIN.fullmatch(domain) or not path.startswith("/"):
        raise ValueError("Invalid cookie domain or path")
    if any(_CONTROL_CHARACTER.search(field) for field in (name, value, path)):
        raise ValueError("Invalid cookie characters")
    # CookieJar uses a missing value to represent an unnamed cookie
    return Cookie(
        version=0,
        name=name or value,
        value=value if name else None,
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=not host_only,
        domain_initial_dot=not host_only,
        path=path,
        path_specified=True,
        secure=secure,
        expires=expires,
        discard=expires is None,
        comment=None,
        comment_url=None,
        rest={"HttpOnly": ""} if http_only else {},
    )


def _json_cookies(data: object) -> Iterable[Cookie]:
    if isinstance(data, dict):
        data = data.get("cookies")
    if not isinstance(data, list):
        raise ValueError("Expected a JSON array of cookies")
    for entry in data:
        if not isinstance(entry, dict):
            raise ValueError("Invalid JSON cookie")
        # CookieJar has no partition information with which to send these safely
        if entry.get("partitionKey") is not None or entry.get("firstPartyDomain"):
            continue
        domain, name, value = entry.get("domain"), entry.get("name"), entry.get("value")
        path = entry.get("path", "/")
        if not (isinstance(domain, str) and isinstance(name, str) and isinstance(value, str) and isinstance(path, str)):
            raise ValueError("Invalid cookie fields")
        host_only = entry.get("hostOnly", not domain.startswith("."))
        secure, http_only = entry.get("secure", False), entry.get("httpOnly", False)
        expiration = entry.get("expirationDate")
        session = entry.get("session", expiration is None)
        if not all(isinstance(flag, bool) for flag in (host_only, secure, http_only, session)):
            raise ValueError("Invalid cookie flags")
        expires = None
        if not session:
            if not isinstance(expiration, int | float) or isinstance(expiration, bool):
                raise ValueError("Invalid cookie expiration")
            expires = int(expiration)
        yield make_cookie(
            domain, name, value, path=path, secure=secure, http_only=http_only, expires=expires, host_only=host_only
        )


def _netscape_cookies(text: str) -> Iterable[Cookie]:
    for line in text.splitlines():
        http_only = line.startswith(_HTTP_ONLY_PREFIX)
        if http_only:
            line = line.removeprefix(_HTTP_ONLY_PREFIX)
        elif not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) < 7:
            raise ValueError("Invalid Netscape cookie")
        domain, include_subdomains, path, secure, expiration, name, value = fields[:7]
        yield make_cookie(
            domain,
            name,
            value,
            path=path,
            secure=secure == "TRUE",
            http_only=http_only,
            expires=int(expiration) if expiration not in ("", "0") else None,
            host_only=include_subdomains != "TRUE",
        )


def _cookie_jar(cookies: Iterable[Cookie]) -> RequestsCookieJar:
    policy = DefaultCookiePolicy(strict_ns_domain=DefaultCookiePolicy.DomainStrictNonDomain)
    jar = RequestsCookieJar(policy=policy)
    for cookie in cookies:
        if not cookie.is_expired():
            # Keep quoted cookie values verbatim when loading and saving sessions
            CookieJar.set_cookie(jar, cookie)
    return jar


def parse_cookies(text: str) -> RequestsCookieJar:
    """Read a JSON cookie export or a legacy Netscape export."""
    text = text.removeprefix("\ufeff")
    try:
        if text.lstrip().startswith(("[", "{")):
            return cookies_from_records(json.loads(text))
        return _cookie_jar(_netscape_cookies(text))
    except OverflowError, RecursionError:
        raise ValueError("Invalid cookie export") from None


def cookies_from_records(records: object) -> RequestsCookieJar:
    try:
        return _cookie_jar(_json_cookies(records))
    except OverflowError, RecursionError:
        raise ValueError("Invalid cookie records") from None


def load_cookie_file(path: Path, *, max_size: int | None = None) -> RequestsCookieJar:
    with path.open("rb") as source:
        data = source.read() if max_size is None else source.read(max_size + 1)
    if max_size is not None and len(data) > max_size:
        raise ValueError("Cookie export is too large")
    return parse_cookies(data.decode("utf-8"))


def select_cookies(cookies: CookieJar | Iterable[Cookie], domain: str) -> RequestsCookieJar:
    """Keep current cookies for the store and its subdomains."""
    return _cookie_jar(
        cookie
        for cookie in cookies
        if cookie.domain.removeprefix(".") == domain or cookie.domain.endswith(f".{domain}")
    )


def cookie_records(cookies: CookieJar) -> list[dict[str, str | int | bool | None]]:
    return [
        {
            "domain": cookie.domain,
            "name": "" if cookie.value is None else cookie.name,
            "value": cookie.name if cookie.value is None else cookie.value,
            "path": cookie.path,
            "secure": cookie.secure,
            "httpOnly": cookie.has_nonstandard_attr("HttpOnly"),
            "hostOnly": not cookie.domain_specified,
            "session": cookie.discard,
            "expirationDate": cookie.expires,
        }
        for cookie in cookies
    ]
