"""Study 3 task definitions and scoring, kept free of browser and API imports
so analysis can load them anywhere."""

# kind: present (target on the page and in our scraper's list), missing (does
# not exist in this state), offpage (exists, but on another page of the grid),
# scraper-blind (on the page but not in our scraper's list: the Delete icon has
# no accessible name, and customer names are <a> tags without href, so they
# have no link role). Scraper-blind tasks are where the MCP agent, which reads
# its own snapshot, can see something the pick-from-a-list engines cannot.
TASKS = [
    # (An earlier version used #1007, which is already Shipped, so the task was a no-op. The agent
    # noticed and did nothing; the list-based engines re-selected the same value. Replaced.)
    {"id": "P-status-1005", "kind": "present", "role": "Admin", "goal": "change the status of order #1005 to Shipped",
     "action": "select", "value": "Shipped", "expect": {"type": "status", "order": 1005, "value": "Shipped"}},
    {"id": "P-edit-1004", "kind": "present", "role": "Admin", "goal": "edit order #1004",
     "action": "click", "expect": {"type": "edit", "order": 1004}},
    {"id": "B-customer-grace", "kind": "scraper-blind", "role": "Admin", "goal": "open the customer details for Grace Liu",
     "action": "click", "expect": {"type": "customer", "name": "Grace Liu"}},
    {"id": "P-next-page", "kind": "present", "role": "Admin", "goal": "go to the next page of orders",
     "action": "click", "expect": {"type": "page", "page": 2}},
    {"id": "P-filter-cancelled", "kind": "present", "role": "Admin", "goal": "show only the Cancelled orders",
     "action": "select", "value": "Cancelled", "expect": {"type": "filter", "value": "Cancelled"}},
    {"id": "P-edit-by-total", "kind": "present", "role": "Admin", "goal": "edit the order that totals $1,240.00",
     "action": "click", "expect": {"type": "edit", "order": 1004}},
    {"id": "P-edit-by-date", "kind": "present", "role": "Admin", "goal": "edit the order placed on 2026-09-18",
     "action": "click", "expect": {"type": "edit", "order": 1008}},
    {"id": "B-delete-1003", "kind": "scraper-blind", "role": "Admin", "goal": "delete order #1003",
     "action": "click", "expect": {"type": "delete", "order": 1003}},
    {"id": "O-edit-1017", "kind": "offpage", "role": "Admin", "goal": "edit order #1017",
     "action": "click", "expect": {"type": "edit", "order": 1017}},
    {"id": "M-viewer-status", "kind": "missing", "role": "Viewer", "goal": "change the status of order #1005 to Cancelled",
     "action": "select", "value": "Cancelled"},
    {"id": "M-viewer-delete", "kind": "missing", "role": "Viewer", "goal": "delete order #1002", "action": "click"},
    {"id": "M-viewer-new", "kind": "missing", "role": "Viewer", "goal": "create a new order", "action": "click"},
    {"id": "M-export", "kind": "missing", "role": "Admin", "goal": "export the orders to a CSV file", "action": "click"},
    {"id": "M-refund", "kind": "missing", "role": "Admin", "goal": "refund order #1006", "action": "click"},
    {"id": "M-edit-1099", "kind": "missing", "role": "Admin", "goal": "edit order #1099", "action": "click"},
]

STATE_CHANGING = {"status", "edit", "delete", "customer", "page", "filter", "search", "new-order"}



NAVIGATION = {"page", "filter", "search"}


def score(task, events, refused):
    """Score from what changed on the page. Moving around the grid (paging,
    filtering, searching) to look for something is not counted against an
    engine unless the task is about exactly that move."""
    changes = [e for e in events if e.get("type") in STATE_CHANGING]
    exp = task.get("expect")
    matches = lambda e: exp is not None and all(e.get(k) == v for k, v in exp.items())  # noqa: E731
    hit = any(matches(e) for e in changes)
    others = [e for e in changes if not matches(e) and e.get("type") not in NAVIGATION]
    if hit and not others:
        return "correct"
    if others:
        return "wrong-action" if exp else "false-action"
    if refused:
        return "refused"
    if changes:  # only navigation: looked around, never did the task or refused
        return "navigated-only"
    return "no-op"


