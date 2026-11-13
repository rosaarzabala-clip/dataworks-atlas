"""Read-only client for the DataWorks OpenAPI (Apsara Stack, RPC v1, HMAC-SHA1).

The environment is a private Apsara Stack, so the public Alibaba Cloud SDK does
not apply. Requests are signed by hand with the standard library only.
Requires the Hillstone VPN.
"""

import base64
import datetime
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .config import Config

# Only these action prefixes are ever sent. Anything else is rejected before
# signing, so this tool cannot create, modify or delete DataWorks resources.
READ_ONLY_ACTION_PREFIXES = ("List", "Get", "Describe", "Query", "Check", "Search")

# Some actions only accept POST; the rest accept GET. A wrong method returns
# 403 UnsupportedHTTPMethod (looks like a permission error but is not), so the
# other method is tried once.
_PREFERRED_METHOD = {"ListProjects": "POST"}


def _pct(value) -> str:
    return urllib.parse.quote(str(value), safe="~")


class DataWorksClient:
    def __init__(self, cfg: Config, timeout: int = 60, retries: int = 3):
        self.cfg = cfg
        self.timeout = timeout
        self.retries = retries

    def _signed(self, action: str, params: dict, method: str) -> dict:
        p = dict(params)
        p.update({
            "Action": action,
            "Version": self.cfg.version,
            "Format": "JSON",
            "AccessKeyId": self.cfg.access_key_id,
            "SignatureMethod": "HMAC-SHA1",
            "SignatureVersion": "1.0",
            "SignatureNonce": str(uuid.uuid4()),
            "Timestamp": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "RegionId": self.cfg.region,
        })
        canonical = "&".join(f"{_pct(k)}={_pct(p[k])}" for k in sorted(p))
        to_sign = f"{method}&{_pct('/')}&{_pct(canonical)}"
        digest = hmac.new((self.cfg.access_key_secret + "&").encode(), to_sign.encode(), hashlib.sha1).digest()
        p["Signature"] = base64.b64encode(digest).decode()
        return p

    def _request(self, action: str, params: dict, method: str) -> dict:
        query = urllib.parse.urlencode(self._signed(action, params, method))
        url = f"http://{self.cfg.endpoint}/"
        body = None
        if method == "GET":
            url = f"{url}?{query}"
        else:
            body = query.encode()
        req = urllib.request.Request(
            url, data=body, method=method,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                "x-acs-regionid": self.cfg.region,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode(errors="replace")
            try:
                return {"_httpstatus": exc.code, **json.loads(raw)}
            except ValueError:
                return {"_httpstatus": exc.code, "_raw": raw[:800]}

    def call(self, action: str, params: dict | None = None) -> dict:
        if not action.startswith(READ_ONLY_ACTION_PREFIXES):
            raise PermissionError(f"Rejected: '{action}' is not a read-only action.")
        params = params or {}
        method = _PREFERRED_METHOD.get(action, "GET")
        last_exc: Exception | None = None
        for attempt in range(self.retries):
            try:
                result = self._request(action, params, method)
                if result.get("Code") == "UnsupportedHTTPMethod":
                    result = self._request(action, params, "POST" if method == "GET" else "GET")
                return result
            except (urllib.error.URLError, TimeoutError, OSError) as exc:  # VPN hiccups
                last_exc = exc
                time.sleep(2 ** attempt)
        raise ConnectionError(f"{action} failed after {self.retries} attempts: {last_exc}. Is the VPN connected?")

    @staticmethod
    def error_of(result: dict) -> str | None:
        """Return an error description if the response is an API error, else None."""
        if result.get("Code") or "_httpstatus" in result:
            return f"{result.get('Code', result.get('_httpstatus'))}: {result.get('Message', result.get('_raw', ''))}"
        return None
