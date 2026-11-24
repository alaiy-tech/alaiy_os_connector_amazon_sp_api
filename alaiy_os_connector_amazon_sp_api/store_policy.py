# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""What an app that owns a buyer's Amazon store lets this connector keep.

The connector is generic: with nothing registered it reads and stores whatever
Amazon returns. An app that connects a store belonging to someone else (a buyer,
not the seller who runs this site) can register a hook that narrows that to the
products it has a claim on, and nothing more.

    amazon_sp_api_store_policy = ["my_app.store_policy.amazon_policy"]

The hook takes the Amazon Connection name and returns a StorePolicy, or None for
a connection it does not own. A policy is applied at the door: before a row is
written and before any extra API call made on its behalf.
"""

from collections.abc import Callable
from dataclasses import dataclass

import frappe


@dataclass(frozen=True)
class StorePolicy:
	# False: fetch and store nothing for this connection.
	may_sync: bool
	# Per seller SKU: store this listing or order line?
	keeps_sku: Callable[[str], bool]


def for_connection(connection: str) -> StorePolicy | None:
	"""None means unrestricted. A policy means a restricted store.

	Resolve once per job run, not per row: the hook may query the database.
	"""
	hooks = frappe.get_hooks("amazon_sp_api_store_policy")
	if not hooks:
		return None
	return frappe.get_attr(hooks[-1])(connection)
