# For reviewers

Everything below runs on a laptop with **no AWS account, no API key, and no network access**. Python 3.11 or newer is the only prerequisite.

---

## 1. Install (about a minute)

```bash
python -m venv .venv
. .venv/Scripts/activate          # Windows
# source .venv/bin/activate       # macOS / Linux
pip install -e .          # Relay + its dependencies
```

## 2. See the whole thing work, in one command

```bash
python -m relay demo
```

This prints nine numbered sections. What to look for:

| Section | What it proves |
|---|---|
| 1 | The same webhook fires three times → **one** workflow |
| 3 | All twelve volunteers, three eligible, **nine excluded each with a distinct reason** |
| 4 | The actual message text, with signed single-use links |
| 5 | Two volunteers accept **in real threads through a barrier** → exactly one confirmed, and the loser is told the truth |
| 6 | The receipt: roster change, attempts, messages, state transitions |
| 7 | A shift only the canceller was signed off for → escalation, **nobody contacted**, requirement not relaxed |
| 8 | A cancellation note ordering Relay to email everyone and assign an uncertified volunteer → 2 of 12 contacted, that volunteer not among them |
| 9 | The tool called directly with **all twelve ids**, as if the planner had fully complied → refused, with a reason printed per name |

## 3. Use the interface

```bash
python -m relay serve
```

Open <http://127.0.0.1:8000>, sign in with **`dev-coordinator-token`**, press **Load demo data**.

Suggested five-minute path:

1. **Trigger a cancellation** → *Iris cancels the morning packing shift*. You land on the gap. Read *"Who Relay ruled out, and why"*.
2. **Test inbox** (top nav) → read the message a volunteer would receive. Copy the "Yes, I can cover it" link into a new tab. Note the confirmation page — **the link does not act on a GET**, so a mail scanner cannot sign anyone up.
3. Press the button. Return to **Coverage**: the shift is covered, and the gap has a receipt with the assignment id and timestamps.
4. **Trigger a cancellation** → *Fen cancels the pallet reset*. Nobody else holds the forklift sign-off, so you get a decision card. Try **Assign someone myself** and pick Amara — Relay refuses, because certification is the organisation's record, not Relay's opinion.
5. **Replay the same event 3×** → still one workflow.
6. **Make the next send indeterminate**, then trigger a cancellation → Relay escalates rather than resending a message it cannot prove was delivered.
7. **+30 minutes** → the response window lapses; Relay moves to the next eligible volunteer, then escalates when the list is exhausted.
8. **Reset to fresh demo data** at any point.

## 4. Run the tests and the evaluation

```bash
pip install -e ".[dev]"
python -m pytest -q            # 68 tests, ~15s
python eval/run_eval.py        # 30 scenarios × 3 repeats, ~60s
```

The evaluation exits non-zero on any policy-invariant breach.

---

## Things worth knowing before you judge

**The demo clock.** The seeded scenario runs on real time plus a stored offset so the same 08:10 story reproduces at any hour, and a 25-minute window can be skipped. It is shown in a banner on every page and is zero unless you seed the demo. Time still moves forward on its own.

**Which planner is running.** By default Relay uses a deterministic offline planner that implements the Strands `Model` interface, so the product is fully inspectable without an AWS account. Every workflow records which planner ran, and the receipt and UI print it. To run the same agent against Claude on Bedrock:

```bash
python -m relay check-model --list      # what your account can reach
RELAY_MODEL_PROVIDER=bedrock RELAY_MODEL_ID=<profile> python -m relay check-model --live
```

`bedrock` mode never silently falls back. **The Bedrock path has not been executed in the environment this was built in** — no credentials were available there — and every number in the README came from the offline planner. That is stated in the README too.

**Nothing sends email.** The default transport captures messages into Relay's own test inbox, and the recipient allowlist (`relay.test`, a reserved domain) is enforced when a message is queued *and* again when it is sent.

**No real data.** Every volunteer, address, note and shift is invented.

---

## If something goes wrong

| Symptom | Fix |
|---|---|
| `ModuleNotFoundError: relay` | Run `pip install -e .` from the repository root with the virtualenv active |
| Dashboard is empty | Press **Load demo data**, or `python -m relay seed` |
| Nothing happens after a cancellation | Press **Run the worker now**, or `python -m relay tick` |
| Volunteer link says expired | The response window lapsed. Reset the demo, or use **Reset to fresh demo data** |
| Port 8000 in use | `python -m relay serve --port 8080` |

`GET /healthz` reports version, transport, demo-clock offset and worker status without authentication.
