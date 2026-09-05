# Agents for Humans: Building Relay Around a Coordinator's Real Decisions

*Draft for builder.aws. Publish under your own account with the title above. "Agents for Humans" has to be in the title.*

---

A volunteer cancels a shift two hours before it starts. Somebody has to work out who else can cover it, ask them, and find out whether anyone said yes.

Almost all of that is rule-following. Who holds the certification this shift requires. Who has opted in to last-minute asks. Who is already on another shift at that time. Who asked not to be contacted this week. Then messages go out, and the coordinator keeps checking whether anyone actually said yes.

A small part of it is not rule-following at all. Sometimes the only volunteer with the sign-off is the one who cancelled. That is a real decision, and it belongs to a person.

Relay is an agent that does the first part and stops at the second.

## What I did not build

The obvious build is a chat assistant: *ask me who can cover Saturday and I'll tell you*. I rejected that, and I think the reason generalises.

A chat assistant hands the coordinator a list. The coordinator still has to send the messages, still has to track the replies, still has to notice that nobody answered. The work that costs twenty minutes is the follow-up, not the lookup. An agent that only does the lookup has automated the fast part.

So Relay has no chat box. Its main screen is a list of coverage gaps with their current state, and a section headed *"Needs your decision"* that is usually empty. The interaction I optimised for is the one where the coordinator doesn't interact at all.

## Honesty about validation

I should say this early, because it changes how you should read everything else: **no real organisation has used Relay.** I did not interview a coordinator. There is no pantry waiting for this.

The pantry in the demo is invented, the twelve volunteers are invented, and the word *synthetic* is in the organisation's name so it can't be quoted as a partner. Every number I publish came from synthetic scenarios.

That is a genuine weakness and I would rather name it than have someone find it. What I could do without a partner was make the *shape* of the problem defensible, and be strict about not dressing a plausible workflow up as a validated one.

## The three screens

**Quiet overview.** Covered shifts, gaps in progress, what finished, and today's rota. Every status carries a word as well as a colour: "Request sent", "No reply", "Covered", "Needs your decision". A coordinator glancing at this between other tasks should be able to tell in two seconds whether anything wants them.

**Decision card.** One question, and only the choices the coordinator is actually authorised to make. The part I care most about is the evidence table: *why each of the other eleven volunteers was not asked*, in a sentence each.

> Cal Rivera does not hold the organisation-verified certification this shift requires.
> Gita Rao has reached the contact limit for this week.
> Hugo Delaine is inside their quiet hours right now.

That table does something a confidence score can't. It lets the coordinator disagree with a *specific* fact. "Actually Cal finished that course on Tuesday" is an actionable correction to a source record. "The agent was 72% confident" is not actionable at all.

**Action receipt.** Event id, every candidate considered, every contact attempt, consent, tool results, the roster change with its assignment id, timestamps. Observed behaviour, not the model's private reasoning. I deliberately do not show a "chain of thought" panel. What the coordinator needs to audit is what Relay *did*.

## The distinction that shaped the whole state machine

**A sent message is not a filled shift.**

That sounds obvious written down. It is remarkably easy to violate in code. The tempting shortcut is to mark the gap resolved when outreach goes out, because that's when the agent's turn ends and it feels finished.

So `contacting` and `awaiting_response` are separate states from `confirmed`, and several things exist purely to keep the distinction visible:

- `request_coverage` returns a literal string in its result: *"A request has been queued. This is not coverage until someone accepts."* The model reads that in its tool result.
- The receipt after outreach says *"Nobody is scheduled yet."*
- There is a test asserting the word "confirmed" does not appear in a summary written while the workflow is still awaiting a reply.

## Where the human boundary actually sits

I gave the coordinator four possible decisions on a gap Relay cannot close: retry outreach, assign someone myself, leave it uncovered, or *the source record is wrong and I'll fix it*.

That last one matters. It is the option that admits the problem is upstream. Without it, the only way to resolve a bad certification record is to make the agent ignore certification. That is exactly the thing that must never become a one-click action.

And "assign someone myself" is not a bypass. Relay still refuses a volunteer who lacks an organisation-verified requirement, even with coordinator authority behind the request:

> Amara Osei does not hold the organisation-verified 'forklift' certification. Update the volunteer record in the source system first.

The coordinator has authority over Relay. Neither of them has authority over what the organisation has certified.

## What I'd do differently

Ranking candidates is where I still can't tell you the model earns its place. Relay uses it to order eligible volunteers using roster notes a rules engine can't parse ("has trained two others on this line"), and to write the sentence a volunteer reads. I believe both matter. I haven't measured either, and a rules-only baseline ships in the same repository, so that comparison is the obvious next experiment rather than a claim I get to make now.

---

*Relay is open source (MIT). It runs end to end with no AWS account: `pip install -e . && python -m relay demo`.*
