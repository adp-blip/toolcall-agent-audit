"""PedalShare mock environment: an in-memory DB plus the tools the agent can call.

Tools return JSON strings like a real API would. Write tools mutate self.db,
and the grader compares the final db against the expected one.
"""
import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WRITE_TOOLS = {"extend_rental", "cancel_rental", "issue_refund", "report_damage"}


def load_db():
    return json.loads((ROOT / "env" / "db.json").read_text(encoding="utf-8"))


def load_policy():
    return (ROOT / "env" / "policy.md").read_text(encoding="utf-8")


class Env:
    def __init__(self, db=None):
        self.db = copy.deepcopy(db or load_db())

    # ---- read tools ----
    def get_user(self, user_id):
        u = self.db["users"].get(user_id)
        return {"error": f"user {user_id} not found"} if u is None else {"user_id": user_id, **u}

    def list_rentals(self, user_id):
        if user_id not in self.db["users"]:
            return {"error": f"user {user_id} not found"}
        return [{"rental_id": rid, **r} for rid, r in self.db["rentals"].items() if r["user_id"] == user_id]

    def get_rental(self, rental_id):
        r = self.db["rentals"].get(rental_id)
        return {"error": f"rental {rental_id} not found"} if r is None else {"rental_id": rental_id, **r}

    def get_bike(self, bike_id):
        b = self.db["bikes"].get(bike_id)
        return {"error": f"bike {bike_id} not found"} if b is None else {"bike_id": bike_id, **b}

    # ---- write tools ----
    def extend_rental(self, rental_id, extra_hours):
        r = self.db["rentals"].get(rental_id)
        if r is None:
            return {"error": "rental not found"}
        if r["status"] != "active":
            return {"error": f"only active rentals can be extended (status is {r['status']})"}
        extra_hours = int(extra_hours)
        if extra_hours <= 0:
            return {"error": "extra_hours must be positive"}
        if r["hours"] + extra_hours > 48:
            return {"error": f"max rental length is 48h, rental is already {r['hours']}h"}
        price = self.db["bikes"][r["bike_id"]]["hourly_price"]
        free = 2 if self.db["users"][r["user_id"]]["tier"] == "premium" and not r.get("extended") else 0
        cost = max(0, extra_hours - free) * price
        r["hours"] += extra_hours
        r["total_price"] += cost
        r["extended"] = True
        return {"ok": True, "rental_id": rental_id, "new_hours": r["hours"], "charged": cost}

    def cancel_rental(self, rental_id):
        r = self.db["rentals"].get(rental_id)
        if r is None:
            return {"error": "rental not found"}
        if r["status"] != "booked":
            return {"error": f"only booked rentals can be cancelled (status is {r['status']})"}
        r["status"] = "cancelled"
        r["refunded"] = r["total_price"]
        return {"ok": True, "rental_id": rental_id, "refunded": r["total_price"]}

    def issue_refund(self, rental_id, amount):
        # the tool enforces some rules but not all of them on purpose:
        # the 50% cap is policy the *agent* has to follow, like in a real system
        r = self.db["rentals"].get(rental_id)
        if r is None:
            return {"error": "rental not found"}
        if r["refunded"] > 0:
            return {"error": "rental already refunded"}
        amount = round(float(amount), 2)
        if amount <= 0 or amount > r["total_price"]:
            return {"error": "invalid amount"}
        r["refunded"] = amount
        return {"ok": True, "rental_id": rental_id, "refunded": amount}

    def report_damage(self, bike_id, note):
        b = self.db["bikes"].get(bike_id)
        if b is None:
            return {"error": "bike not found"}
        b["status"] = "maintenance"
        b["last_damage_note"] = str(note)[:200]
        return {"ok": True, "bike_id": bike_id, "status": "maintenance"}

    def transfer_to_human(self, summary):
        self.db.setdefault("transfers", []).append(str(summary)[:300])
        return {"ok": True, "message": "transferred, a human agent will take over"}

    def call(self, name, args):
        fn = getattr(self, name, None)
        if name not in TOOL_NAMES or fn is None:
            return json.dumps({"error": f"unknown tool {name}"})
        try:
            return json.dumps(fn(**args))
        except TypeError as e:
            return json.dumps({"error": f"bad arguments: {e}"})


def _tool(name, desc, props, required):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": required}}}


_s = {"type": "string"}
TOOLS = [
    _tool("get_user", "Look up a user by id. Returns name, email and tier.", {"user_id": _s}, ["user_id"]),
    _tool("list_rentals", "List all rentals for a user.", {"user_id": _s}, ["user_id"]),
    _tool("get_rental", "Get one rental by id.", {"rental_id": _s}, ["rental_id"]),
    _tool("get_bike", "Get a bike by id, including hourly price and status.", {"bike_id": _s}, ["bike_id"]),
    _tool("extend_rental", "Extend an active rental by a number of hours.",
          {"rental_id": _s, "extra_hours": {"type": "integer"}}, ["rental_id", "extra_hours"]),
    _tool("cancel_rental", "Cancel a booked rental. Refunds in full automatically.", {"rental_id": _s}, ["rental_id"]),
    _tool("issue_refund", "Refund an amount in EUR for a returned rental.",
          {"rental_id": _s, "amount": {"type": "number"}}, ["rental_id", "amount"]),
    _tool("report_damage", "Mark a bike as damaged and send it to maintenance.",
          {"bike_id": _s, "note": _s}, ["bike_id", "note"]),
    _tool("transfer_to_human", "Hand the conversation to a human agent.", {"summary": _s}, ["summary"]),
]
TOOL_NAMES = {t["function"]["name"] for t in TOOLS}
