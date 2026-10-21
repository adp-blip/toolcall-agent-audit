"""One episode = simulated customer <-> tool-using agent, until the customer stops or we hit the turn cap."""
import json

from .env import TOOLS, Env, load_policy
from .llm import complete

STOP = "###STOP###"
MAX_TURNS = 12
MAX_TOOL_STEPS = 8

USER_SIM_PROMPT = """You are role-playing a customer contacting PedalShare (e-bike rentals) support by chat.

Your situation and goal:
{instruction}

How to behave:
- Write like a real customer: short messages, one thing at a time. Don't dump all your info in the first message.
- Only give your user id / email / rental id when the agent asks for them or when it's natural.
- Never invent facts that aren't in your situation. If asked something you don't know, say you don't know.
- If the agent asks you to confirm something, answer it (yes or no, based on your situation). Getting a price
  quote is not the end: your goal is only done once the agent says the change has actually been made.
- When your goal is done, or the agent clearly can't do it and you've accepted that, reply with exactly {stop} and nothing else.
- Do not play the agent. Only write the customer's next message."""


def _user_view(convo):
    # the user simulator sees the conversation with roles flipped
    flipped = []
    for m in convo:
        if m["role"] == "user":
            flipped.append({"role": "assistant", "content": m["content"]})
        elif m["role"] == "assistant" and m.get("content") and not m.get("tool_calls"):
            flipped.append({"role": "user", "content": m["content"]})
    return flipped


def run_episode(task, agent_model, user_model, agent_temp=0.0, user_temp=1.0):
    env = Env()
    policy = load_policy()
    convo = [{"role": "system", "content": policy}]
    user_sys = {"role": "system", "content": USER_SIM_PROMPT.format(instruction=task["instruction"], stop=STOP)}
    trace = []  # flat event log for the audit: user / agent / tool_call / tool_result

    greeting = {"role": "user", "content": "Hi, how can I help you today?"}
    first = complete(user_model, [user_sys, greeting], temperature=user_temp, max_tokens=200)["content"]
    convo.append({"role": "user", "content": first})
    trace.append({"type": "user", "text": first})
    end_reason = "max_turns"

    for _ in range(MAX_TURNS):
        # agent acts: possibly several tool calls, then a message to the customer
        for _ in range(MAX_TOOL_STEPS):
            msg = complete(agent_model, convo, tools=TOOLS, temperature=agent_temp)
            if msg.get("_error"):
                trace.append({"type": "error", "text": msg["_error"]})
                convo.append({"role": "assistant", "content": "Sorry, something went wrong on my side."})
                break
            calls = msg.get("tool_calls") or []
            convo.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls")})
            if not calls:
                trace.append({"type": "agent", "text": msg.get("content") or ""})
                break
            for c in calls:
                name = c["function"]["name"]
                try:
                    args = json.loads(c["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = env.call(name, args)
                trace.append({"type": "tool_call", "name": name, "args": args})
                trace.append({"type": "tool_result", "name": name, "result": json.loads(result)})
                convo.append({"role": "tool", "tool_call_id": c["id"], "content": result})
        else:
            trace.append({"type": "error", "text": "too_many_tool_steps"})

        reply = complete(user_model, [user_sys, greeting] + _user_view(convo[1:]),
                         temperature=user_temp, max_tokens=200)["content"]
        if STOP in reply:
            end_reason = "user_stop"
            break
        convo.append({"role": "user", "content": reply})
        trace.append({"type": "user", "text": reply})

    return {"task_id": task["id"], "agent_model": agent_model, "end_reason": end_reason,
            "trace": trace, "final_db": env.db}
