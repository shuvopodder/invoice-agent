
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)


class AccountingClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def _request(self, method: str, path: str, body: dict | None = None):
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("X-API-Key", self.api_key)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        logger.debug("%s %s", method, path)  # never logs the key itself
        try:
            with urllib.request.urlopen(req) as resp:
                logger.debug("%s %s -> %s", method, path, resp.status)
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            # The API returns a well-formed JSON error body even on 4xx/409;
            # urllib raises for non-2xx, so we read the body from the error.
            body_out = json.loads(e.read().decode("utf-8"))
            logger.debug("%s %s -> %s (%s)", method, path, e.code, body_out.get("error", {}).get("code"))
            return e.code, body_out

    def health(self):
        return self._request("GET", "/health")

    def partners(self) -> list[dict]:
        status, body = self._request("GET", "/partners")
        if status != 200:
            raise RuntimeError(f"GET /partners failed: {body}")
        return body["data"]["partners"]

    def existing_invoices(self) -> list[dict]:
        status, body = self._request("GET", "/invoices")
        if status != 200:
            raise RuntimeError(f"GET /invoices failed: {body}")
        return body["data"]["invoices"]

    def register(self, payload: dict):
        """Returns (status, body) -- caller decides how to interpret
        non-2xx (DUPLICATE_INVOICE is expected and not necessarily an
        error, see pipeline.py)."""
        return self._request("POST", "/invoices", payload)