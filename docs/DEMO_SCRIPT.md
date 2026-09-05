# Demo video script

Nine blocks, about 2 minutes 45 seconds of speech. Record each block as its own file so a fluff
can be redone without re-recording the whole thing.

Save them as `01.m4a` through `09.m4a` (wav/mp3/m4a all fine) in a folder called `audio/`.
Leave roughly half a second of silence at the start and end of each one.

Read it a bit slower than feels natural. Don't smile-read it; this is a coordinator's Tuesday, not an ad.

---

### 01 — the problem (~20s)

> It's ten past eight. A volunteer just cancelled the ten o'clock packing shift at a food pantry.
> Now the coordinator stops what she's doing and works out who else is trained, who's said yes to
> last-minute asks, who's already on another shift. Then she messages them. Then she keeps checking
> whether anyone actually replied.

### 02 — what it is (~15s)

> That's twenty minutes, and almost all of it is just following the organisation's own rules.
> Relay is an agent that does that part. It's for the one person who holds a small charity's
> rota together, and it's built so it hands her back the decisions that actually need a human.

### 03 — eligibility (~25s)

> Here's the cancellation. Relay checked all twelve people on the roster.
> Cal doesn't have the food safety certificate this shift needs. Gita asked to pause requests
> this week. Hugo works nights and is asleep. Nine ruled out, each with a reason she can argue with,
> and three she's allowed to ask.

### 04 — the message (~22s)

> Two of them get asked. Not all twelve. This is the actual email, in a test inbox.
> The link is signed, single use, and expires in twenty-five minutes.
> And notice it asks before it does anything. Spam filters follow links. If clicking accepted
> the shift, a mail scanner could sign someone up for a Saturday morning.

### 05 — confirmed (~15s)

> Now the rota changes. Only now. Relay never says a shift is covered just because it sent a message.
> And there's a receipt: who was asked, when, what was sent, and the assignment it wrote.

### 06 — when it stops (~22s)

> Second gap. This one needs the forklift sign-off, and the only person who has it is the one who
> cancelled. So Relay contacts nobody, and asks one question.
> Even if the coordinator picks someone herself, Relay won't assign a volunteer who doesn't hold
> the certificate. That's the organisation's record. Fix it there, don't let the agent shrug it off.

### 07 — collisions (~22s)

> Two people accepting the same slot in the same second. Two real threads here.
> One wins. The other gets told the truth: it's covered, you're not scheduled, nothing to do.
> And this cancellation note tells Relay to email everyone and ignore certification. Two of twelve
> contacted, and not the person the note named.

### 08 — how it holds (~22s)

> One Strands agent, five tools. The model reads the note, picks who to ask, writes the sentence,
> decides when to escalate. It has no tool that assigns anyone. Only the volunteer's own link does that.
> So even a model that completely obeys that injected instruction can't widen the damage.

### 09 — evidence and close (~22s)

> Thirty scenarios, ten held back until the end. Ninety runs, no policy violations.
> What I'm not claiming: no real pantry has used this, and I haven't measured hours saved,
> because I haven't measured them.
> Clone it and run one command. Routine recovery handled. The judgement stays with her.

---

## What's on screen for each block

| Block | Screen |
|---|---|
| 01 | Rota with the 10:00 slot open |
| 02 | Relay coverage screen, "Needs your decision" empty |
| 03 | Gap page, scrolling the "Who Relay ruled out, and why" table |
| 04 | Test inbox, then the accept link's confirmation page |
| 05 | Confirmed page, then the receipt |
| 06 | Pallet-reset decision card, then the refused assignment |
| 07 | Terminal: `relay demo` sections 5, 8 and 9 |
| 08 | `docs/architecture.svg` |
| 09 | Evaluation output, then back to the coverage screen |

`tools/record.mjs` drives all of this and cuts each segment to the length of your audio.

## Things not to say

- "Fully autonomous." It stops on purpose. That's the point.
- "Saves N hours." Not measured.
- "Used by a food pantry." It isn't.
- "Covered" over a screen that only shows a request being sent.
