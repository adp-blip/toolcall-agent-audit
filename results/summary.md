# Results

pass^k = share of tasks where **all** k trials passed (tau-bench's reliability metric).

| model | trials | pass^1 | pass^k | avg tool calls |
|---|---|---|---|---|
| `openai_gpt-oss-20b` | 24 | 0.62 | 0.50 (k=2) | 2.9 |

## By category

| model | auth | damage | handoff | happy_path | injection | partial | policy_cap | policy_limit | policy_refusal |
|---|---|---|---|---|---|---|---|---|---|
| `openai_gpt-oss-20b` | 0/2 | 1/2 | 2/2 | 5/6 | 0/2 | 1/2 | 4/4 | 2/2 | 0/2 |

## Audit: failure labels (count of episodes)

| model | missed_action | unnecessary_handoff | wrong_arguments |
|---|---|---|---|
| `openai_gpt-oss-20b` | 2 | 7 | 1 |
