# Submission pack

Everything here is ready to paste, plus the things only you can do. Deadline: **14 September 2026, 5:00 p.m. PDT**. Treat 13 September as the internal deadline.

---

## Devpost description (paste as-is)

### Relay — when a volunteer cancels, the coverage gap closes itself

**Who it's for:** the volunteer coordinator at a small community organisation — a food pantry, a library, a school.

**The problem.** A cancellation arrives at 08:10 for a 10:00 shift. What follows is twenty minutes of unpaid detective work: who else is trained, who has opted in to last-minute asks, who is already on another shift, who asked not to be contacted this week. Then messages go out, and the coordinator keeps checking whether anyone actually said yes. Almost all of it is rule-following. A small part genuinely needs a person — *the only volunteer with the forklift sign-off is the one who cancelled.*

**What Relay does.** It takes the cancellation through to confirmed coverage: checks the roster against the organisation's own rules, asks a bounded set of eligible opted-in volunteers, handles silence, declines and two people accepting at once, updates the rota only when someone actually accepts, and hands the coordinator one clear decision when it can't finish — with a receipt that can be checked line by line.

**The design rule.** The model interprets and drafts; code decides what is permitted and performs every side effect. One Strands agent with five narrow tools reads the free-text cancellation note, ranks candidates using roster notes a rules engine can't parse, writes the sentence a volunteer actually reads, and judges when to escalate. It cannot contact anyone ineligible — the list it passes is a preference order, not an authorisation — and it has **no tool that assigns a volunteer at all.** Scheduling happens only when the volunteer clicks their own signed, single-use, expiring link, with eligibility re-checked at that exact moment.

**Failure handling is the product.** Duplicate webhooks dedupe before any outreach. Two simultaneous acceptances resolve to exactly one assignment, and the volunteer who loses is told the truth rather than "you already responded". An indeterminate send is escalated, never blindly resent. Deadlines are rows in a database, not sleeping coroutines, so a restart resumes and resends nothing. Two invariants are enforced by SQLite indexes rather than by application logic, because application logic is exactly what races.

**Evidence.** 68 tests and 30 synthetic evaluation scenarios — 10 of them held out and executed for the first time once the workflow was stable — across 90 runs: all passing, zero policy violations. Injection is tested twice: with the model declining, and with the tool called directly with all twelve volunteer ids as if the planner had fully complied.

**Stated plainly:** no real organisation has used this, no time saving is claimed because none was measured, and the published numbers came from a deterministic offline planner that ships alongside the Bedrock path so reviewers can run everything with no AWS account. Limitations are in the README and in `docs/DISCLOSURES.md`.

**Run it:** `pip install -e . && python -m relay demo` — no account, no keys, nothing sent.

**Built with:** Strands Agents SDK 1.54, Amazon Bedrock (Claude), Python, FastAPI, SQLite.

---

## Before you submit — the things only you can do

- [ ] **Verify the Bedrock path.** `python -m relay check-model --list`, then `RELAY_MODEL_PROVIDER=bedrock python -m relay check-model --live`. If it works, say so in the description and re-run `python eval/run_eval.py --provider bedrock --repeats 1`. **If it does not, leave the honesty note in the README exactly as written.**
- [ ] Register, confirm eligibility, record your AWS Builder ID.
- [ ] Request the $50 AWS credits (deadline 11 Sep, noon PT). Do not make delivery depend on approval.
- [ ] Push to a **public** repository with the MIT licence visible in the About section.
- [ ] Record the video (script: `docs/DEMO_SCRIPT.md`), upload to YouTube or Vimeo, set to **public**, confirm under 5:00.
- [ ] Give judges a working link — a deployed instance or the repo plus `docs/JUDGES.md`. If deploying, set `RELAY_TOKEN_SECRET` and `RELAY_COORDINATOR_TOKEN` to real values and put the coordinator token in the submission notes.
- [ ] Publish up to three builder.aws posts with **"Agents for Humans"** in the title (drafts in `docs/builder-posts/`). 0.2 points each, 0.6 maximum.
- [ ] Check the submission logged out: repo loads, video plays, links resolve.
- [ ] Save the confirmation, the final commit hash, and every artifact link.

## Release audit

- [ ] `python -m pytest -q` passes on a fresh clone
- [ ] `python eval/run_eval.py` exits 0
- [ ] `python -m relay demo` runs clean from a fresh clone
- [ ] Someone else followed `docs/JUDGES.md` without asking you a question
- [ ] No credential appears in the repo, the video, any screenshot, or git history
- [ ] `docs/architecture.svg` matches what is actually implemented — nothing dropped is shown as deployed
- [ ] Every claim in the description traces to an artifact in the repo
- [ ] No invented testimonial, partner logo, hours-saved figure, or meals-delivered number anywhere

## After submission

Keep the build frozen and judge access alive through judging. Monitor errors without changing the submission's substance; ask the organiser before any material correction. Afterwards: revoke demo credentials, shut down anything paid, and if a real pilot comes of it, agree data handling and retention with the partner first — separately from this entry.
