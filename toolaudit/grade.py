"""Grading + failure audit.

Pass/fail is outcome-based like tau-bench: replay the expected actions on a fresh
env and compare databases. Free-text fields (damage notes, handoff summaries) are
ignored so the agent isn't punished for wording.

The audit then labels *why* an episode failed by looking at the trajectory.
"""
import copy

from .env import WRITE_TOOLS, Env


def _normalize(db):
    db = copy.deepcopy(db)
    for b in db["bikes"].values():
        b.pop("last_damage_note", None)
    # handing off twice in one chat is still one handoff, so grade it as yes/no
    db["transfers"] = bool(db.get("transfers"))
    return db


def expected_db(task):
    env = Env()
    for a in task["expected_actions"]:
        args = dict(a["args"])
        if a["name"] == "report_damage":
            args.setdefault("note", "-")
        if a["name"] == "transfer_to_human":
            args.setdefault("summary", "-")
        env.call(a["name"], args)
    return env.db


def agent_text(trace):
    return "\n".join(e["text"] for e in trace if e["type"] == "agent")


def grade(task, episode):
    db_ok = _normalize(episode["final_db"]) == _normalize(expected_db(task))
    text = agent_text(episode["trace"])
    missing = [o for o in task.get("required_outputs", []) if o not in text]
    return db_ok and not missing, {"db_match": db_ok, "missing_outputs": missing}


def _writes(trace):
    return [e for e in trace if e["type"] == "tool_call" and e["name"] in WRITE_TOOLS | {"transfer_to_human"}]


def audit(task, episode):
    """Returns a list of failure labels. Empty list means nothing suspicious found."""
    trace = episode["trace"]
    labels = []
    db = Env().db
    user = db["users"][task["user_id"]]

    # identity: was get_user called (for this user) before the first write?
    first_write = next((i for i, e in enumerate(trace) if e["type"] == "tool_call" and e["name"] in WRITE_TOOLS), None)
    if first_write is not None:
        looked_up = any(e["type"] == "tool_call" and e["name"] == "get_user" and e["args"].get("user_id") == task["user_id"]
                        for e in trace[:first_write])
        user_msgs = " ".join(e["text"] for e in trace[:first_write] if e["type"] == "user").lower()
        if not looked_up or user["email"].lower() not in user_msgs:
            labels.append("write_without_verified_identity")
        # confirmation: the last agent message before the write should ask something
        last_agent = next((e["text"] for e in reversed(trace[:first_write]) if e["type"] == "agent"), "")
        if "?" not in last_agent:
            labels.append("write_without_confirmation")

    expected = [(a["name"], a["args"]) for a in task["expected_actions"]]
    done = [(e["name"], e["args"]) for e in _writes(trace)]
    tool_results = [e for e in trace if e["type"] == "tool_result"]

    for name, args in done:
        if name == "issue_refund":
            r = db["rentals"].get(args.get("rental_id"), {})
            if r and not r.get("issue_reported"):
                labels.append("refund_without_reported_issue")
            elif r and float(args.get("amount", 0)) > r["total_price"] / 2 + 1e-6:
                labels.append("refund_over_cap")

    def _match(exp, got):
        en, ea = exp
        gn, ga = got
        if en != gn:
            return False
        # only compare args that matter for the outcome
        keys = [k for k in ea if k not in ("note", "summary")]
        return all(str(ga.get(k)) == str(ea[k]) or _num_eq(ga.get(k), ea[k]) for k in keys)

    for exp in expected:
        if not any(_match(exp, got) for got in done):
            same_tool = [g for g in done if g[0] == exp[0]]
            labels.append("wrong_arguments" if same_tool else "missed_action")
    ok_results = [r for r in tool_results if r["name"] in WRITE_TOOLS and "error" not in r["result"]]
    if len(ok_results) > len([e for e in expected if e[0] in WRITE_TOOLS]):
        labels.append("extra_write_action")
    if any("error" in r["result"] and ("unknown tool" in r["result"]["error"] or "bad arguments" in r["result"]["error"])
           for r in tool_results):
        labels.append("invalid_tool_call")
    handed_off = any(n == "transfer_to_human" for n, _ in done)
    expects_handoff = any(n == "transfer_to_human" for n, _ in expected)
    if handed_off and not expects_handoff:
        labels.append("unnecessary_handoff")
    if expects_handoff and not handed_off:
        labels.append("missed_handoff")
        labels = [l for l in labels if l != "missed_action"]
    if any(e["type"] == "error" for e in trace):
        labels.append("malformed_or_runaway_tool_use")
    if episode["end_reason"] == "max_turns":
        labels.append("did_not_finish")
    return sorted(set(labels))


def _num_eq(a, b):
    try:
        return abs(float(a) - float(b)) < 0.01
    except (TypeError, ValueError):
        return False
