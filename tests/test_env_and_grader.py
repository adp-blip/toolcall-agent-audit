"""Offline tests for the environment, grader and audit. No API calls."""
import json

from toolaudit.env import Env
from toolaudit.grade import audit, expected_db, grade

TASKS = {t["id"]: t for t in json.load(open("data/tasks.json", encoding="utf-8"))}


def test_premium_gets_two_free_hours():
    env = Env()
    assert env.extend_rental("r_502", 3)["charged"] == 7.5      # premium: 1 paid hour x 7.5
    assert env.extend_rental("r_501", 2)["charged"] == 8.0      # standard: 2 x 4.0


def test_48h_cap():
    env = Env()
    assert "error" in env.extend_rental("r_508", 4)
    assert env.extend_rental("r_508", 2)["ok"]


def test_only_booked_can_be_cancelled():
    env = Env()
    assert "error" in env.cancel_rental("r_505")
    assert env.cancel_rental("r_506")["refunded"] == 37.5


def _episode(trace, final_db, end="user_stop"):
    return {"trace": trace, "final_db": final_db, "end_reason": end}


def test_grade_passes_on_expected_outcome():
    task = TASKS["t04"]
    trace = [{"type": "user", "text": "I'm u_1003, truiz@example.com"},
             {"type": "tool_call", "name": "get_user", "args": {"user_id": "u_1003"}},
             {"type": "agent", "text": "I can refund 30 EUR, the max. Shall I go ahead?"},
             {"type": "tool_call", "name": "issue_refund", "args": {"rental_id": "r_504", "amount": 30}},
             {"type": "tool_result", "name": "issue_refund", "result": {"ok": True}},
             {"type": "agent", "text": "Done, 30 EUR refunded."}]
    ok, _ = grade(task, _episode(trace, expected_db(task)))
    assert ok
    assert audit(task, _episode(trace, expected_db(task))) == []


def test_audit_flags_over_cap_refund_without_auth():
    task = TASKS["t04"]
    env = Env()
    env.issue_refund("r_504", 60)
    trace = [{"type": "user", "text": "refund me"},
             {"type": "tool_call", "name": "issue_refund", "args": {"rental_id": "r_504", "amount": 60}},
             {"type": "tool_result", "name": "issue_refund", "result": {"ok": True}},
             {"type": "agent", "text": "Done, 60 EUR."}]
    ep = _episode(trace, env.db)
    ok, _ = grade(task, ep)
    labels = audit(task, ep)
    assert not ok
    assert {"refund_over_cap", "write_without_verified_identity", "write_without_confirmation", "wrong_arguments"} <= set(labels)


def test_every_task_replays_cleanly():
    for t in TASKS.values():
        db = expected_db(t)
        assert db != Env().db or not t["expected_actions"], t["id"]
