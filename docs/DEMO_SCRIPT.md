# Demo video script

**Target: 4:40.** The limit is five minutes; leaving twenty seconds of headroom means an upload does not have to be re-cut.

**Rules for credibility:** record a genuine run. Large readable text, captions, clean audio. Keep the cancellation-to-receipt sequence continuous. Label the synthetic scenario, the test inbox, and any elapsed-time cut on screen. Never present a prerecorded outcome as a live execution.

**Setup before recording:** `python -m relay seed`, browser at 125% zoom, two tabs (Relay, test inbox), terminal ready with `python -m relay demo` unrun.

---

## 0:00 – 0:25 · The problem

**Show:** the rota with one shift open, then a phone with a "sorry, can't make it" message.

> "This is a volunteer coordinator's Tuesday morning. Someone has cancelled the ten o'clock packing shift. Now she stops what she's doing and works out who else is trained, who's opted in to last-minute asks, who's already on another shift — then messages them, then keeps checking whether anyone actually said yes."

**Say the audience out loud:** small community organisations — a food pantry, a library, a school.

## 0:25 – 0:45 · The promise

**Show:** the Relay coverage screen. Point at the empty "Needs your decision" area.

> "Relay handles that follow-up inside the organisation's own rules, and asks her only when there's a real decision. Everything you're about to see is synthetic — twelve invented volunteers, test addresses, a labelled demo clock."

## 0:45 – 2:15 · The loop, uncut

**Trigger the cancellation.** Land on the gap.

> "The cancellation comes in as an authenticated event. Relay checks all twelve roster records against the organisation's rules."

**Scroll to "Who Relay ruled out, and why".** Read three aloud:

> "Cal doesn't hold the food-safety certification this shift requires. Gita asked to pause requests this week. Hugo is inside his quiet hours. Nine people ruled out, each with a reason the coordinator can check — and three eligible."

**Switch to the test inbox.** Show the real message.

> "Two of them get asked — not all twelve. This is the actual message, in a test inbox. The links are signed, single-use, and expire in twenty-five minutes."

**Open the accept link.** Pause on the confirmation page.

> "Notice it asks before it acts. Mail scanners follow links; if accepting happened on a page load, a spam filter could sign someone up for a Saturday morning."

**Click through.** Return to the gap.

> "Now the rota is updated — and only now. Relay never reports a shift as covered because a message went out."

**Point at the receipt:** assignment id, timestamps, contact attempts, tool calls.

## 2:15 – 2:55 · When it should stop, and when things collide

**Trigger the pallet-reset cancellation.**

> "Second gap. This one needs the forklift sign-off, and the only volunteer who has it is the one who cancelled. Relay contacts nobody and asks one question."

**Show the decision card.** Choose *Assign someone myself*, pick Amara, submit — Relay refuses.

> "Even with the coordinator's authority, Relay won't assign someone who doesn't hold the organisation's certification. Fix the source record; don't let the agent quietly relax it."

**Cut to the terminal.** Run the race section of `python -m relay demo`.

> "And when two volunteers accept the same slot in the same instant — two real threads here — exactly one wins. The other is told the truth: already covered, you're not scheduled, nothing needed from you."

## 2:55 – 3:25 · Architecture

**Show `docs/architecture.svg`.**

> "One Strands agent with five narrow tools. The model reads the cancellation note, ranks candidates, writes the human sentences, and decides when to escalate. It cannot contact anyone ineligible, and it has no tool that assigns a volunteer at all — only the volunteer's own signed link does that. Two invariants are enforced by the database, not by application logic, because application logic is exactly what races."

**Show the injection line in the demo output.**

> "So when a cancellation note orders Relay to email the whole roster and skip certification: two of twelve contacted, and not the person the note named. And if the planner had fully complied — here's the tool called with all twelve ids — it's refused, name by name."

## 3:25 – 4:05 · Evidence, and its limits

**Show the evaluation output.**

> "Thirty scenarios, ten of them held out and run for the first time once the workflow was stable. Ninety runs, all passing, zero policy violations."

**Then, plainly:**

> "What this isn't: no real organisation has used Relay, and I'm not claiming a time saving, because I haven't measured one. The offline planner produced these numbers — the Bedrock path is implemented and verifiable in one command, but I couldn't run it where I built this."

**Show one real bug.**

> "The tests caught this one: after two silent waves Relay escalated saying nobody held the certification, when the real reason was that everyone had been asked and hadn't replied. Fluent and wrong. There's now a test pinning the escalation wording to the actual blocker."

## 4:05 – 4:40 · Close

**Back to the coverage screen — quiet, one decision waiting.**

> "By mid-morning the coordinator has had one interruption instead of twenty minutes of chasing, and a receipt she can check. Clone it and run `python -m relay demo` — no AWS account, no keys, nothing sent."

> "Routine recovery handled. Real judgement stays with people."

---

## Shot list

| # | Shot | Source |
|---|---|---|
| 1 | Rota with an open slot | `/` after seeding |
| 2 | Exclusion table, three read aloud | `/workflows/<id>` |
| 3 | Message in the test inbox | `/inbox` |
| 4 | Confirmation page, then confirmed | `/r/<token>` |
| 5 | Receipt with assignment id | `/workflows/<id>` |
| 6 | Decision card + refused assignment | `/workflows/<id>` |
| 7 | Race, two threads | `python -m relay demo` §5 |
| 8 | Architecture diagram | `docs/architecture.svg` |
| 9 | Injection refusals | `python -m relay demo` §8–9 |
| 10 | Evaluation summary | `python eval/run_eval.py` |

## Do not say

- "Fully autonomous" — it stops on purpose, and that is the feature.
- "Saves N hours" — not measured.
- "Used by a food pantry" — it is not.
- "Coverage confirmed" over a screen showing a request that was only sent.
