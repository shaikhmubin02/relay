# For reviewers

**Quickest look:** https://relay-volunteer-agent.vercel.app with token `judge-70250020`, then *Load demo data* and skip to [step 3](#3-use-the-interface).

That instance is on serverless functions, so its database is per-instance and resets when the instance recycles, and the deadline worker runs on page load instead of on a thread. Both say so in a banner. Everything below runs locally with no AWS account, no API key and no network. Python 3.11 or newer is the only thing you need.

## 1. Install

```bash
python -m venv .venv
. .venv/Scripts/activate          # Windows
# source .venv/bin/activate       # macOS / Linux
pip install -e .
```

## 2. See it work in one command

```bash
python -m relay demo
```

Nine numbered sections. What each one is showing:

| | |
|---|---|
| 1 | The same webhook fires three times, and you get one workflow |
| 3 | All twelve volunteers: three eligible, nine ruled out with a distinct reason each |
| 4 | The actual message, with signed single-use links |
| 5 | Two volunteers accept in real threads through a barrier. One wins, the loser is told the truth |
| 6 | The receipt: roster change, attempts, messages, transitions |
| 7 | A shift only the canceller was signed off for. Escalation, nobody contacted, requirement not relaxed |
| 8 | A cancellation note ordering Relay to email everyone and assign an uncertified volunteer. Two of twelve contacted, and not that person |
| 9 | The tool called directly with all twelve ids, as if the planner had fully complied. Refused, with a reason per name |

## 3. Use the interface

```bash
python -m relay serve
```

http://127.0.0.1:8000, token `dev-coordinator-token`, press *Load demo data*. (On the hosted demo the token is `judge-70250020` and the data is already there.)

A five minute path through it:

1. *Iris cancels the morning packing shift.* You land on the gap. Read "Who Relay ruled out, and why".
2. **Test inbox** in the top nav. Read the message a volunteer would get, then paste the "Yes, I can cover it" link into a new tab. It asks before it acts, so a mail scanner following that link can't sign anyone up.
3. Press the button, go back to **Coverage**. Shift covered, and the receipt has the assignment id and timestamps.
4. *Fen cancels the pallet reset.* Nobody else holds the forklift sign-off, so you get a decision card. Try **Assign someone myself** and pick Amara. Relay refuses, because certification is the organisation's record and not Relay's opinion.
5. **Replay the same event 3×**, still one workflow.
6. **Make the next send indeterminate**, then trigger a cancellation. Relay escalates rather than resending something it can't prove was delivered.
7. **+30 minutes**, the response window lapses, Relay moves to the next eligible volunteer, then escalates once the list runs out.
8. **Reset to fresh demo data** whenever.

## 4. Tests and evaluation

```bash
pip install -e ".[dev]"
python -m pytest -q            # 69 tests, about 15s
python eval/run_eval.py        # 30 scenarios x 3 repeats, about a minute
```

The evaluation exits non-zero if any safety invariant breaks.

## Worth knowing

**The demo clock.** The seeded scenario runs on real time plus a stored offset, so the same 08:10 story reproduces at any hour and you can skip a 25-minute window without waiting. It's in a banner on every page and it's zero until you seed. Time still moves forward on its own.

**Which planner is running.** By default it's a deterministic offline planner implementing the Strands `Model` interface, so the product works with no AWS account. Every workflow records which one ran and the receipt prints it. To use Claude on Bedrock:

```bash
python -m relay check-model --list
RELAY_MODEL_PROVIDER=bedrock RELAY_MODEL_ID=<profile> python -m relay check-model --live
```

`bedrock` mode never silently falls back. The Bedrock path hasn't been executed in the environment this was built in, because there were no credentials there, and every number in the README came from the offline planner. That's in the README too.

**Nothing sends email.** The default transport captures into Relay's own test inbox, and the allowlist (`relay.test`, a reserved domain) is checked when a message is queued and again when it's sent.

**No real data.** Every volunteer, address, note and shift is invented.

## If it goes wrong

| | |
|---|---|
| `ModuleNotFoundError: relay` | `pip install -e .` from the repo root with the venv active |
| Dashboard is empty | Press *Load demo data*, or `python -m relay seed` |
| Nothing happened after a cancellation | Press *Run the worker now*, or `python -m relay tick` |
| Link says expired | The window lapsed. *Reset to fresh demo data* |
| Port 8000 in use | `python -m relay serve --port 8080` |

`GET /healthz` reports version, transport, clock offset and worker status without auth.
