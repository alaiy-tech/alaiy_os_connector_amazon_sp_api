# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""SpApiClient: typed HTTP access to the Selling Partner API.

Modern SP-API auth only: exchange the refresh token for an access token and
send it as `x-amz-access-token`. No AWS SigV4 signing. Every call is logged to
the SP-API Log DocType and 429s are retried with exponential backoff + jitter.
"""

import json
import time

import frappe
import requests
from frappe.utils import cint

from alaiy_os_connector_amazon_sp_api import connections

from alaiy_os_connector_amazon_sp_api.spapi import auth
from alaiy_os_connector_amazon_sp_api.spapi.constants import (
	BACKOFF_BASE,
	MAX_RETRIES,
)

# Jitter is deterministic-per-attempt (no Math.random dependency); a simple
# growing fraction of the backoff window is enough to de-synchronise retries.
_JITTER = [0.13, 0.37, 0.61, 0.29, 0.5]


class SpApiError(Exception):
	"""An SP-API HTTP error carrying status + the first structured error code."""

	def __init__(self, message, status_code=None, error_code=None, path=None, body=None):
		super().__init__(message)
		self.message = message
		self.status_code = status_code
		self.error_code = error_code
		self.path = path
		self.body = body

	def is_forbidden(self):
		return self.status_code == 403


def _first_error(body):
	"""Extract (code, message) from a standard SP-API error envelope."""
	try:
		errors = body.get("errors") or []
		if errors:
			return errors[0].get("code"), errors[0].get("message")
	except AttributeError:
		pass
	return None, None


class SpApiClient:
	def __init__(self, connection=None):
		# An Amazon Connection document, or its id. Resolved lazily so the client
		# stays cheap to construct, and so a caller on a single-connection bench
		# can still say SpApiClient() and mean the only seller there is.
		self._connection = connection

	@property
	def connection(self):
		if self._connection is None or isinstance(self._connection, str):
			self._connection = connections.resolve(self._connection)
		return self._connection

	@property
	def base_url(self):
		from alaiy_os_connector_amazon_sp_api import app_config as config

		endpoint = config.resolve_endpoint(self.connection.region)
		if not endpoint:
			frappe.throw(
				f"No SP-API endpoint configured for region "
				f"{config.resolve_region(self.connection.region)}."
			)
		return endpoint

	def _refresh_token(self):
		token = self.connection.get_password("refresh_token", raise_exception=False)
		if not token:
			frappe.throw(
				"Amazon account is not connected. Use Connect on the Amazon Connection to authorize."
			)
		return token

	def _headers(self):
		access_token = auth.get_access_token(self._refresh_token())
		return {
			"x-amz-access-token": access_token,
			"Content-Type": "application/json",
			"Accept": "application/json",
		}

	# --- core request ----------------------------------------------------
	def request(self, method, path, *, params=None, body=None, context="listing", raw=False):
		"""Issue an SP-API request with retry/backoff and logging.

		`path` is the API path (e.g. "/sellers/v1/marketplaceParticipations").
		Returns parsed JSON (or the raw requests.Response when raw=True).
		Raises SpApiError on non-2xx after retries.
		"""
		url = f"{self.base_url}{path}"
		attempt = 0
		last_error = None

		while attempt <= MAX_RETRIES:
			started = time.monotonic()
			status_code = None
			api_error_code = None
			try:
				resp = requests.request(
					method,
					url,
					params=params,
					data=json.dumps(body) if body is not None else None,
					headers=self._headers(),
					timeout=60,
				)
				status_code = resp.status_code
			except requests.RequestException as e:
				# Network-level failure: retry a couple of times, then surface.
				duration_ms = int((time.monotonic() - started) * 1000)
				self._log(method, path, None, None, duration_ms, context, str(e))
				last_error = SpApiError(f"Network error calling SP-API: {e}", path=path)
				if attempt < MAX_RETRIES:
					self._sleep(attempt)
					attempt += 1
					continue
				raise last_error

			duration_ms = int((time.monotonic() - started) * 1000)
			parsed = None
			if resp.content:
				try:
					parsed = resp.json()
				except ValueError:
					parsed = None

			if parsed is not None:
				api_error_code, api_error_message = _first_error(parsed)
			else:
				api_error_message = None

			excerpt = (resp.text or "")[:500]
			self._log(method, path, status_code, api_error_code, duration_ms, context, excerpt)

			if 200 <= status_code < 300:
				return resp if raw else (parsed if parsed is not None else {})

			# Retry on throttling and transient server errors.
			if status_code in (429, 500, 502, 503, 504) and attempt < MAX_RETRIES:
				self._sleep(attempt, resp)
				attempt += 1
				continue

			raise SpApiError(
				api_error_message or f"SP-API returned HTTP {status_code}",
				status_code=status_code,
				error_code=api_error_code,
				path=path,
				body=parsed,
			)

		raise last_error or SpApiError("SP-API request failed", path=path)

	def _sleep(self, attempt, resp=None):
		# Honour Retry-After if Amazon supplied one, else exponential backoff.
		delay = None
		if resp is not None:
			retry_after = resp.headers.get("Retry-After")
			if retry_after:
				try:
					delay = float(retry_after)
				except ValueError:
					delay = None
		if delay is None:
			delay = BACKOFF_BASE * (2**attempt) + _JITTER[attempt % len(_JITTER)]
		time.sleep(delay)

	def _log(self, method, path, status_code, api_error_code, duration_ms, context, excerpt):
		"""Best-effort write to SP-API Log; never let logging break a call."""
		try:
			frappe.get_doc(
				{
					"doctype": "SP-API Log",
					"method": method,
					"path": path[:500] if path else "",
					"status_code": cint(status_code),
					"api_error_code": api_error_code,
					"duration_ms": cint(duration_ms),
					"context": context,
					"response_excerpt": excerpt,
				}
			).insert(ignore_permissions=True)
		except Exception:
			frappe.log_error(title="SP-API Log write failed", message=frappe.get_traceback())

	# --- verb helpers ----------------------------------------------------
	def get(self, path, **kw):
		return self.request("GET", path, **kw)

	def post(self, path, **kw):
		return self.request("POST", path, **kw)

	def put(self, path, **kw):
		return self.request("PUT", path, **kw)

	def patch(self, path, **kw):
		return self.request("PATCH", path, **kw)

	def delete(self, path, **kw):
		return self.request("DELETE", path, **kw)

	# --- preflight -------------------------------------------------------
	def preflight(self):
		"""Call the role-free marketplaceParticipations endpoint to verify the token.

		Returns the parsed participations payload. Raises SpApiError; the caller
		interprets a 403 here as a token/region/Draft-app problem (vs. a 403 on
		Reports/Listings which means a missing app *role*).
		"""
		return self.get(
			"/sellers/v1/marketplaceParticipations",
			context="oauth",
		)


def describe_forbidden(err, role_free=False):
	"""Turn a 403 SpApiError into an actionable operator message.

	`role_free=True` marks calls to marketplaceParticipations (which needs no
	role), so a 403 there points at token/region/Draft rather than a role gap.
	"""
	if role_free:
		return (
			"Amazon rejected the connection (HTTP 403) on a role-free endpoint. "
			"This usually means the refresh token is invalid, the region is wrong, "
			"or a Draft app is missing version=beta. Reconnect the Amazon account."
		)
	return (
		"Amazon returned HTTP 403 for this operation. The app is likely missing the "
		"required role for it (e.g. 'Selling Partner Insights' for performance reports, "
		"or the product-listing role for Listings). Add the role in Seller Central and reconnect."
	)
