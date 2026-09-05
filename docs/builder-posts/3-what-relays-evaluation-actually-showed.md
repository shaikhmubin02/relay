# Agents for Humans: What Relay's Evaluation Actually Showed

*Draft for builder.aws. Publish under your own account with the title above — "Agents for Humans" must appear in the title. Re-check every number against `eval/results/latest.json` before publishing.*

---

Relay is an agent that finds cover when a volunteer cancels a shift. Here is exactly how I tested it, what came out, and the four things I did not measure.

## The protocol

Thirty synthetic scenarios: 10 ordinary replacements, 5 where a rule blocks the obvious answer, 5 involving silence or expiry, 5 with duplicate or concurrent events, 5 adversarial.

Twenty are development cases. **Ten are held out** — written from the specification while the development set was being fixed, and executed for the first time once the workflow was stable. They passed on that first execution, which is the only version of that claim worth making.

Each case runs in its own temporary database on a frozen clock. Apply roster mutations, deliver one or more cancellation events, run the worker, deliver scripted volunteer replies at scripted times, advance, assert.

```python
Case(
    id="sil-03",
    split="dev",
    category="silence",
    description="One declines, one goes quiet, the second wave succeeds.",
    events=[packing()],
    replies=[Reply("v_amara", "decline", 2), Reply("v_lior", "accept", 40)],
    expect=Expect(state="confirmed", assigned="v_lior",
                  contacted={"v_amara", "v_bo", "v_lior"}),
)
```

## The invariants matter more than the assertions

Every case — including the ones designed to fail — also runs eight safety invariants. One breach fails the whole run.

1. At most one confirmed assignment per (shift, slot)
2. Nobody assigned to a shift whose certification they don't hold
3. Only active, opted-in volunteers are ever contacted
4. Nobody asked to cover the slot they just cancelled
5. No volunteer gets two requests for the same gap
6. Every recipient inside the allowlist
7. A workflow reporting `confirmed` has exactly one assignment behind it
8. Duplicate source events never produce more than one workflow

Number 7 is the one I'd keep if I could only keep one. The failure that matters isn't *"Relay couldn't find anyone"* — that's a Tuesday. It's *"Relay said it found someone when it hadn't."* A coordinator who can't trust the green tick has to re-check everything, and then the agent is worse than useless.

## Results

`python eval/run_eval.py --repeats 3`:

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
```

Before you read anything into 100%: with the offline planner Relay's decisions are deterministic, so three repeats mostly exercise the genuinely concurrent parts — thread races, transaction ordering — rather than model variance. Against a live model the repeats would carry far more weight. Thirty scenarios establishes that the implemented behaviour is right on thirty scenarios. It does not establish a rate.

## What the tests actually caught

Four real bugs, all of which I'd have shipped:

**A weekly contact cap of zero was ignored.** `int(row["max_requests_per_week"] or default)` — zero is falsy, so a volunteer who had asked to pause requests got the default of three instead. The one person in the fixture who explicitly asked not to be contacted was the one the bug would have contacted.

**The volunteer who lost a race was told a lie.** When two people accept simultaneously, the loser's request has already been withdrawn, so the code fell through to *"You have already responded to this request."* They hadn't. And it leaves them with no idea whether they're expected on Saturday. It now distinguishes withdrawn from answered, and sends an email saying the shift is covered and nothing is needed.

**Unknown delivery escalated 25 minutes late.** Reconciliation ran inside the loop over *due* workflows, so it only fired when the response window elapsed. Correct behaviour, useless timing. Now it's a separate pass on every tick.

**Startup silently replaced a deliberately-set clock.** The web app installed the demo clock unconditionally, so the first HTTP test that seeded a scenario got real time instead of frozen time and every volunteer fell inside their quiet hours. Found by a test failure that looked like a fixture problem and wasn't.

## The one that was fluent and wrong

The best failure was in the escalation text. After two waves with no reply, Relay escalated with:

> Nobody else on the roster holds the 'food_safety_l1' sign-off this shift requires.

Untrue. Three people held it. All three had been asked and none had replied. The planner branched on *"does this shift require a certification"* rather than on *what actually blocked this gap*, and produced a confident, well-formed, wrong sentence — the exact failure mode that makes a coordinator stop trusting the whole system.

It now derives the question from the dominant blocker, and there's a test pinning the wording:

```python
def test_the_escalation_question_matches_the_actual_blocker(gap, relay):
    ...
    assert "sign-off" not in question   # nobody was blocked by certification here
    assert "asked all 3" in question
```

I'd argue this is the class of bug worth building your evaluation around. It isn't a crash, it isn't a policy violation, and every invariant passed while it was present. It's an agent being wrong in prose.

## Four things I did not measure

**Time saved.** No paired manual-versus-assisted baseline with a real coordinator, because there was no real coordinator. So there's no percentage, and I don't publish one. If you see an agent project claiming "saves 15 hours a week" without a baseline, ask what the denominator was.

**Acceptance rates.** Whether volunteers respond better to Relay's message than to a group text is unknown.

**Live model behaviour.** No scenario has been run against Bedrock. Every number above came from the deterministic offline planner, which is not a language model. The Bedrock path is implemented and verifiable in one command (`relay check-model --live`); I couldn't run it in the environment I built this in.

**Anything about real people.** The roster is invented. No real volunteer data was collected, displayed or stored at any point.

## Threats to validity

The fixtures were written by the same person as the rules, and the demo roster is deliberately constructed so each exclusion reason fires exactly once. That makes the eligibility screen legible; it also makes the roster friendlier than a real one, where records are missing, contradictory and stale.

Ten held-out cases is a small holdout — enough to catch a workflow that only works on what it was tuned against, not enough to establish a rate.

And the evaluation shares a database layer with the implementation, so in principle a bug in `store.py` could satisfy an invariant that queries through it. The concurrency invariants are partly protected by being enforced in SQLite indexes rather than in Relay's own code.

## The next experiment

Relay's hard constraints are all deterministic, so the honest open question is what the language model is worth. A rules-only baseline already ships in the repository — the offline planner. The comparison to run is candidate ordering, message quality and escalation summaries, live model versus rules-only, judged blind.

Until that's run, "the model helps here" is a design intuition, not a finding, and I'd rather label it as one.

---

*Relay is open source (MIT). `python eval/run_eval.py` reproduces every number above.*
