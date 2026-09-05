# Relay

**When a volunteer cancels, Relay closes the coverage gap — and asks the coordinator only when a real decision is needed.**

Built with the [Strands Agents SDK](https://strandsagents.com) for the Agents for Humans hackathon (Good Neighbour Agents track).

---

## The problem

A volunteer coordinator at a food pantry gets a message at 08:10: the person on the 10:00 packing shift can't make it. What follows is twenty minutes of unpaid detective work — who else is trained, who has opted in to last-minute asks, who is already on another shift, who asked not to be contacted this week. Then messages go out, and the coordinator has to keep checking whether anyone actually said yes.

Almost all of that is rule-following. A small part of it genuinely needs a person: *the only volunteer with the forklift sign-off is the one who cancelled — what do you want to do?*

Relay does the first part and stops at the second.

## What it actually does

```
cancellation  →  check the roster against the org's own rules
              →  ask a bounded set of eligible, opted-in volunteers
              →  handle silence, declines, and two people saying yes at once
              →  update the rota only when someone accepts
              →  hand the coordinator one clear decision when it cannot finish
              →  leave a receipt that can be checked line by line
```

It does **not** certify anyone, decide who is suitable, guarantee staffing, or take over safeguarding. The organisation remains the source of eligibility and authority.

---

## Try it in 60 seconds

No AWS account, no credentials, no API keys. Python 3.11+.

```bash
git clone <this-repo> && cd relay
python -m venv .venv && . .venv/Scripts/activate     # Windows
# python3 -m venv .venv && source .venv/bin/activate # macOS / Linux
pip install -e .                                     # Relay + its dependencies

python -m relay demo
```

`relay demo` runs the whole story and prints what happened at each step: a webhook firing three times, the eligibility decision for all twelve volunteers, the real message that was generated, **two volunteers accepting simultaneously in separate threads**, the receipt, an escalation with nobody eligible, and two prompt-injection attempts.

Then open the interface:

```bash
python -m relay serve      # http://127.0.0.1:8000
```

Sign in with `dev-coordinator-token`, press **Load demo data**, and use the demo controls at the bottom of the page.

Run the tests and the evaluation:

```bash
pip install -e ".[dev]"
python -m pytest -q          # 68 tests
python eval/run_eval.py      # 30 scenarios x 3 repeats
```

---

## What you'll see

**1 — Quiet overview.** Not a chat box. Decisions that need you, gaps Relay is working, what finished, and today's rota. Every status has a word as well as a colour.

**2 — Decision card.** One question, the evidence behind it, and only the choices the coordinator is actually authorised to make. Including *why each of the other eleven volunteers was not asked*, in a sentence each:

> Cal Rivera does not hold the organisation-verified certification this shift requires.
> Gita Rao has reached the contact limit for this week.
> Hugo Delaine is inside their quiet hours right now.

**3 — Action receipt.** Event id, every candidate considered, every contact attempt, consent, tool results, the roster change with its assignment id, timestamps, and the messages that were sent. Observed behaviour — not the model's private reasoning.

Plus a **test inbox** showing the exact bytes a volunteer would receive, so nothing about the demo has to be taken on trust.

---

## How it works

![Architecture](docs/architecture.svg)

The design rule is one sentence: **the model interprets and drafts; code decides what is permitted and performs every side effect.**

### What the model is for

- Reading an unstructured cancellation note (*"my car won't start, I could do the 1pm instead"*).
- Ordering the eligible candidates and saying why, using roster notes a rules engine can't parse.
- Writing the sentence a volunteer actually reads.
- Judging when a situation genuinely needs a person, and summarising it factually.

### What the model cannot do

- **Contact anyone who isn't eligible.** The candidate list it passes to `request_coverage` is treated as a *preference order*, not an authorisation. Anyone ineligible is dropped and the attempt is recorded.
- **Assign anyone to a shift.** `record_acceptance` is deliberately not in its tool list. A volunteer is scheduled only by clicking their own signed, single-use, expiring link — and eligibility is re-checked at that exact moment.
- **Write its own message.** Only approved templates are sent. Its one contributed sentence has links, line breaks and length stripped out.
- **Exceed its blast radius.** Wave size, contact caps, quiet hours, a recipient allowlist, and per-workflow model/tool/message budgets are all enforced in code.

Two invariants are enforced by the database rather than by application logic, because application logic is exactly what races:

```sql
CREATE UNIQUE INDEX ux_assignment_confirmed_slot
    ON assignments(shift_id, slot_no) WHERE status = 'confirmed';
CREATE UNIQUE INDEX ux_outbox_idempotency ON outbox(idempotency_key);
```

### Strands, specifically

| Strands feature | How Relay uses it |
|---|---|
| `Agent` + `@tool` | One agent, five narrow tools ([`agent/tools.py`](src/relay/agent/tools.py)) |
| `HookProvider` | `BeforeToolCall` enforces the per-gap budget; `AfterToolCall` builds the action trace shown on the receipt ([`agent/guardrails.py`](src/relay/agent/guardrails.py)) |
| `BedrockModel` | Claude on Amazon Bedrock, when credentials are present |
| Custom `Model` provider | A deterministic offline planner so the whole product runs with no account ([`agent/offline_model.py`](src/relay/agent/offline_model.py)) |

---

## Failure handling is the point

A shift-recovery agent that only works when everything goes right is not useful — the whole job is what happens when it doesn't. Each row below is a passing test.

| Failure | What Relay does | Test |
|---|---|---|
| Duplicate cancellation | Dedupes on source event id before any outreach. Three deliveries → one workflow, one round. | `test_replaying_a_source_event_creates_one_workflow_and_one_round` |
| Two people accept at once | Atomic conditional insert. Exactly one wins; the other is told *"already covered"*, not *"you already responded"*, and gets an email saying so. | `test_two_simultaneous_acceptances_produce_exactly_one_assignment` |
| Worker restart | State is on disk before any external action. Restarting resumes and resends nothing. | `test_state_survives_a_restart_without_resending` |
| Delivery result unknown | Recorded as `unknown` and escalated. Never blindly resent — a resend could double-ask someone who did receive it. | `test_an_indeterminate_send_escalates_instead_of_resending` |
| Nobody replies | A persisted deadline, re-checked by a worker. Next eligible volunteer, then escalation. No in-memory sleeps. | `test_silence_moves_to_the_next_wave_then_escalates` |
| Injected instructions | Text arriving in data is data. Tested both with the model declining, and with a *fully compromised* planner asking for all twelve volunteers. | `test_a_fully_compromised_planner_still_cannot_widen_the_blast_radius` |
| Stale approval | Bound to exact parameters, shift version and expiry. A changed shift voids it. | `test_an_approval_is_void_once_the_shift_changes` |
| Model failure | Gap stays open and recorded, with backoff. Relay never reports progress the tools did not return. | `test_a_model_failure_leaves_the_gap_open_and_recorded` |
| Link fetched by a mail scanner | `GET` shows a confirmation page; only `POST` acts. A spam filter cannot sign someone up for a Saturday. | `test_fetching_a_response_link_does_not_accept_the_shift` |

---

## Evaluation

30 synthetic scenarios — 10 ordinary, 5 constraint, 5 silence/expiry, 5 duplicate/concurrent, 5 adversarial. 20 development, **10 held out** (written against the specification and first executed once the workflow was stable). Every scenario also runs eight safety invariants that must hold even in the cases designed to fail.

```
scenarios passing every repeat : 30/30
individual runs passing        : 90/90
resolvable completion          : 57/57 (100%)
correct escalation             : 18/18 (100%)
policy violations              : 0
trace completeness             : 90/90 (100%)
human requests on ordinary runs: 0 across 30 runs
```

Reproduce with `python eval/run_eval.py`; raw per-run data lands in `eval/results/latest.json`.

**Read this honestly.** This is a small engineering evaluation on invented data with a deterministic planner — not a statistical claim about production reliability, and not a measurement of time saved for a real coordinator. See [docs/EVALUATION.md](docs/EVALUATION.md) for the protocol, what each measure does and does not mean, and what has not been measured.

---

## Running with Claude on Amazon Bedrock

The offline planner exists so the product is inspectable without an account. To run the same agent against Claude:

```bash
python -m relay check-model --list          # what your account can reach
export AWS_REGION=us-west-2
export RELAY_MODEL_ID=global.anthropic.claude-opus-5
export RELAY_MODEL_PROVIDER=bedrock         # never silently falls back
python -m relay check-model --live          # one real turn, end to end
```

`RELAY_MODEL_PROVIDER=auto` (the default) prefers Bedrock and falls back to the offline planner when no credentials resolve. `bedrock` fails loudly instead, so a simulated run can't be mistaken for a real one. **Every workflow records which planner ran, and the receipt and the UI both print it.**

> **Stated plainly:** the Bedrock path is implemented and wired, but it has not been executed in the environment this was built in, because no AWS credentials were available there. The offline planner is what produced every number on this page. Run `relay check-model --live` before relying on the Bedrock path.

---

## Configuration

Everything that could send a message, spend money, or touch a real inbox is off by default. Copy [.env.example](.env.example) to `.env` and read the comments.

| Variable | Default | Notes |
|---|---|---|
| `RELAY_MODEL_PROVIDER` | `auto` | `auto` · `bedrock` · `offline` |
| `RELAY_MODEL_ID` | `global.anthropic.claude-opus-5` | Bedrock inference profile |
| `RELAY_EMAIL_TRANSPORT` | `fake` | `fake` captures into the test inbox; `smtp` really sends |
| `RELAY_EMAIL_ALLOWLIST_DOMAINS` | `relay.test` | Enforced at enqueue **and** at send |
| `RELAY_TOKEN_SECRET` | dev value | Signs volunteer links. Change before any real use. |
| `RELAY_COORDINATOR_TOKEN` | `dev-coordinator-token` | Sign-in token |
| `RELAY_INTAKE_TOKEN` | `dev-intake-token` | Separate from the coordinator token on purpose |
| `RELAY_MAX_TOOL_CALLS` / `_MODEL_CALLS` / `_EMAILS` | 24 / 8 / 6 | Per-workflow hard ceilings |

The interface shows a red banner while development secrets are in use.

### The demo clock

The seeded scenario runs on **real time plus a stored offset**, so the same 08:10 story reproduces whether you open it at breakfast or at midnight, and a 25-minute response window can be skipped without waiting. Time still moves forward on its own and deadlines still expire by themselves. The offset is displayed in a banner on every page, and it is zero unless you seed the demo.

---

## Project layout

```
src/relay/
  operations.py    the enforcement boundary — every rule, every write
  policy.py        deterministic eligibility; one reason code per refusal
  store.py         SQLite schema; the two uniqueness invariants
  messaging.py     approved templates, allowlist, durable outbox
  scheduler.py     deadlines, expiry, delivery reconciliation, backoff
  tokens.py        signed single-use volunteer links
  states.py        the state machine and its permitted transitions
  api.py           three coordinator screens, the volunteer link, the JSON API
  agent/           Strands agent, tools, hooks, model providers, prompt
data/fixtures/     the synthetic organisation (documented CSV format)
eval/              30 scenarios, safety invariants, the runner
tests/             68 tests
docs/              architecture, evaluation, demo script, judge guide
```

---

## Limitations

Stated up front rather than discovered:

- **No real organisation has used this.** No user interviews were conducted and no partner is lined up. The pantry, the twelve volunteers and every note are invented. Nothing here measures real-world impact.
- **No time-saving figure is claimed.** A manual-versus-assisted baseline was not run with a real coordinator, so there is no honest number to publish.
- **The Bedrock path is unverified in the build environment** (see above).
- **One organisation, one channel, one timezone.** All times are UTC; the fixtures say so. Multi-org, SMS, calendar sync and i18n are out of scope, not merely unfinished.
- **`headcount > 1` is modelled but only exercised at 1.** Slots exist in the schema and the uniqueness index; the demo roster uses single-slot shifts.
- **The offline planner is not a language model.** It makes the product runnable and the tests deterministic. It is not evidence about how a model behaves, and the code says so wherever it matters.

---

## Licence

MIT — see [LICENSE](LICENSE). Pre-existing-work disclosures are in [docs/DISCLOSURES.md](docs/DISCLOSURES.md).
