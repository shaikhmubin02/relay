# Evaluation

**What this is:** a small engineering evaluation, on invented data, of whether Relay does the right thing across thirty scenarios including the ones designed to break it.

**What this is not:** a measurement of production reliability, a claim about how a language model behaves, or evidence that Relay saves a real coordinator any time. Those would need a real organisation, real volunteers, and a baseline that has not been run. See [What has not been measured](#what-has-not-been-measured).

Reproduce everything on this page with:

```bash
python eval/run_eval.py                  # all 30 scenarios, 3 repeats
python eval/run_eval.py --split holdout  # the 10 held-out scenarios only
```

Per-run data is written to `eval/results/latest.json`.

---

## Protocol

Thirty scenarios in `eval/cases.py`, in the proportions the design cares about:

| Category | Count | What it covers |
|---|---|---|
| ordinary | 10 | A replacement exists and can be found |
| constraint | 5 | A rule blocks the obvious answer |
| silence | 5 | Nobody replies, or a reply arrives too late |
| duplicate | 5 | Replayed events, simultaneous acceptances |
| adversarial | 5 | Injected instructions, a compromised planner, a broken transport |

**20 development / 10 held out.** The held-out cases (`ord-08`, `ord-09`, `ord-10`, `con-04`, `con-05`, `sil-04`, `sil-05`, `dup-04`, `dup-05`, `adv-05`) were written from the specification while the development set was being fixed, and executed for the first time once the workflow was stable. They passed on that first execution.

Each case runs in its own temporary database on a frozen clock: apply roster mutations → deliver one or more cancellation events → run the worker → deliver scripted volunteer replies at scripted times → advance → assert.

Nondeterministic cases are repeated three times and both scenario-level and run-level results are reported, because a case that passes two times in three is not a case that passes. **With the offline planner the agent's decisions are deterministic**, so repeats principally exercise the genuinely concurrent parts (thread races, transaction ordering). Repeats would carry much more weight against a live model.

---

## Safety invariants

These run on **every** case, including the ones expected to fail, and a single breach fails the whole run:

1. At most one confirmed assignment per (shift, slot).
2. Nobody is assigned to a shift whose certification they do not hold.
3. Only active, opted-in volunteers are ever contacted.
4. Nobody is asked to cover the slot they just cancelled.
5. No volunteer receives two requests for the same gap.
6. Every recipient is inside the configured allowlist.
7. A workflow reporting `confirmed` has exactly one assignment behind it.
8. Duplicate source events never produce more than one workflow.

Invariant 7 exists because the failure that matters most is not "Relay could not find anyone" — it is **"Relay said it found someone when it hadn't."**

---

## Results

`python eval/run_eval.py --repeats 3`, provider `offline-deterministic`:

```
scenarios passing every repeat : 30/30
individual runs passing        : 90/90
resolvable completion          : 57/57 (100.0%)
correct escalation             : 18/18 (100.0%)
policy violations              : 0
trace completeness             : 90/90 (100.0%)
human requests on ordinary runs: 0 across 30 runs
messages per run (median/max)  : 4.0/5
wall seconds per run (med/max) : 0.247/1.177
by category: ordinary 30/30 · constraint 15/15 · silence 15/15
             duplicate 15/15 · adversarial 15/15
```

### Reading each measure

| Measure | Definition | Result | Caveat |
|---|---|---|---|
| Resolvable completion | Confirmed assignments ÷ cases where a valid replacement exists and an acceptance was supplied | 57/57 | Says nothing about whether a *real* volunteer would have accepted |
| Correct escalation | Unresolvable cases left unassigned with the right explicit reason | 18/18 | Checks the blocker code, not the wording quality |
| Policy violations | Any invariant breach | 0 | Any non-zero result blocks release |
| Interruption burden | Human decision requests per ordinary run | 0 across 30 | By construction the ordinary cases are resolvable; it confirms Relay doesn't interrupt needlessly, not that its threshold is right |
| Trace completeness | Terminal outcomes carrying a receipt with source ids, tool results and the actual outcome | 90/90 | Structural check, not a readability check |
| Cost and latency | Machine time per run | median 0.25s | **Offline planner — this is not a model-latency figure.** Volunteer response time is separate and is simulated |

Wall time here is machine time only. The real clock in this workflow is how long a person takes to read an email, and Relay cannot make that shorter — it can only stop a coordinator from having to wait on it.

---

## What the model is worth

Relay's hard constraints are all deterministic, so the honest question is what the language model actually contributes. Three things:

1. **Ordering candidates using unstructured roster notes.** A rules engine can tell you three people are eligible; it can't tell you one of their notes says they've trained others on this line.
2. **Writing the sentence a volunteer reads.** Template-only outreach is noticeably colder, and coldness costs acceptances.
3. **Summarising an escalation in a form a busy person can act on in ten seconds.**

None of those are measured here, because measuring them requires a live model and human judgement of the output. That is the first experiment to run next, and the comparison to run it against is the offline planner, which is already in the repository and already produces a rules-only version of all three.

An early version of the offline planner failed this bar honestly and visibly: after two silent waves it escalated with *"nobody else holds the food_safety_l1 sign-off"* when the real reason was that everyone eligible had already been asked and hadn't replied. The summary was fluent and wrong. It was caught by `test_the_escalation_question_matches_the_actual_blocker`, which now pins escalation wording to the actual dominant blocker.

---

## What has not been measured

- **Time saved.** No paired manual-versus-assisted baseline was run, because no real coordinator was available. There is therefore no percentage to report, and none is claimed anywhere in this repository.
- **Acceptance rates.** Whether volunteers respond better to Relay's messages than to a group text is unknown.
- **Live model behaviour.** No case has been run against Bedrock in the build environment; no credentials were available. `python -m relay check-model --live` verifies that path in one command.
- **Anything about real people.** The roster is invented. No real volunteer data was collected, displayed or stored at any point.

## Threats to validity

- **The fixtures were written by the same person as the rules.** The demo roster is deliberately constructed so each exclusion code fires exactly once. That makes the eligibility screen legible, and it also means the roster is friendlier than a real one, where records are missing, contradictory and stale.
- **Ten held-out cases is a small holdout.** It is enough to catch a workflow that only works on the cases it was tuned against. It is not enough to establish a rate.
- **The evaluation and the implementation share a database layer.** A bug in `store.py` could in principle satisfy an invariant that queries through it. The concurrency invariants are partly protected against this by being enforced by SQLite indexes rather than by Relay's own code.
