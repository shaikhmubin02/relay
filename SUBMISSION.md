# Submission

Deadline is 14 September 2026, 5:00 p.m. PDT. Aim to be done on the 13th.

| | |
|---|---|
| Repo | https://github.com/shaikhmubin02/relay (public, MIT shown in About) |
| Live demo | https://relay-volunteer-agent.vercel.app (token `judge-70250020`) |
| Video | `video/relay-demo.mp4`, 2m43s. Upload to YouTube as **public** |
| AWS Builder ID | Mubin (shaikhmubin572@gmail.com) |
| Track | Good Neighbour Agents |

---

## Devpost description

### Relay: when a volunteer cancels, the coverage gap closes itself

Ten past eight. Someone can't make the ten o'clock packing shift at a food pantry. The coordinator now has to work out who else is trained, who's agreed to last-minute asks, who's already on another shift, and who asked not to be bothered this week. Then message them. Then keep checking whether anyone replied. Twenty minutes, and nearly all of it is applying the organisation's own rules.

The bit that isn't: sometimes the only person with the forklift sign-off is the one who cancelled. That's a real decision and it belongs to a human.

**Who it's for.** The one person who holds a small charity's rota together. A food pantry, a library, a school.

**What Relay does.** It takes a cancellation through to confirmed cover. Checks the roster against the organisation's rules, asks a bounded set of eligible volunteers who've opted in, handles silence and declines and two people saying yes at once, updates the rota only when somebody actually accepts, and hands over one clear decision when it can't finish. Everything it did is on a receipt you can check line by line.

**How it works.** The model interprets and drafts; code decides what's allowed and performs every side effect. One Strands agent with five narrow tools reads the free-text cancellation note, orders candidates using roster notes a rules engine can't parse, writes the sentence a volunteer actually reads, and judges when to escalate. The candidate list it passes is a preference order, not permission: anyone ineligible gets dropped and logged. And it has no tool that assigns anybody. A volunteer is scheduled only by clicking their own signed, single-use, expiring link, with eligibility rechecked at that moment.

**Why the failure handling is the product.** Duplicate webhooks dedupe before any outreach. Two simultaneous acceptances resolve to exactly one assignment, and the person who loses is told the truth instead of "you already responded". A send the mail server can't confirm gets escalated, never blindly resent. Deadlines are rows in a database rather than sleeping coroutines, so a restart resumes and resends nothing. Two invariants sit in SQLite indexes rather than in application code, because application code is what races.

**Evidence.** 69 tests. 30 synthetic evaluation scenarios, 10 of them held back until the workflow was stable, run three times each: 90/90 passing, zero policy violations. Prompt injection is tested twice, once with the model declining and once by calling the tool directly with all twelve volunteer ids as though the planner had fully complied.

**What I'm not claiming.** No real organisation has used this. I'm not quoting a time saving because I didn't measure one. The published numbers came from a deterministic offline planner that ships alongside the Bedrock path so anyone can run the whole thing with no AWS account. Limitations are in the README and in `docs/DISCLOSURES.md`.

**Try it.** Live demo above, or `pip install -e . && python -m relay demo` locally. No account, nothing sent.

**Built with:** Strands Agents SDK 1.54, Amazon Bedrock (Claude), Python, FastAPI, SQLite.

---

## Left to do

- [ ] Upload `video/relay-demo.mp4` to YouTube, set it **public**, paste the link into Devpost.
- [ ] Verify the Bedrock path: `python -m relay check-model --list`, then `RELAY_MODEL_PROVIDER=bedrock python -m relay check-model --live`. If it works, say so in the description and rerun `python eval/run_eval.py --provider bedrock --repeats 1`. If it doesn't, leave the note in the README as it is.
- [ ] Request the $50 AWS credits before 11 Sep, noon PT. Don't let delivery depend on it.
- [ ] Publish the posts in `docs/builder-posts/` on builder.aws. "Agents for Humans" has to be in the title. 0.2 points each, 0.6 max.
- [ ] Open the submission logged out: repo loads, video plays, demo link works.
- [ ] Save the confirmation, the final commit hash and every link.

## Done

- [x] Public repo, MIT detected by GitHub
- [x] README, architecture diagram, setup instructions, judge guide
- [x] Live demo with real secrets, Vercel deployment protection off
- [x] AWS Builder ID
- [x] Demo video, 2m43s

## Last checks

- `python -m pytest -q` and `python eval/run_eval.py` clean on a fresh clone
- `python -m relay demo` runs from a fresh clone
- No credential in the repo, the video, a screenshot or git history
- The architecture diagram matches what's actually built
- Nothing in the description that isn't backed by something in the repo
- No invented testimonial, partner logo or hours-saved number anywhere

## Afterwards

Keep the build frozen and the demo up through judging. Watch for errors but don't change the substance of what was submitted; ask the organiser first if something material needs fixing. Once it's over, rotate the demo credentials, shut down anything paid, and if a real pilot comes out of it, sort out data handling with the partner separately.
