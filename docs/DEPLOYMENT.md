# Deployment

Relay is a normal ASGI app. It runs anywhere that can run Python; the hosted demo happens to be on Vercel.

## The hosted demo

**<https://relay-volunteer-agent.vercel.app>** — coordinator token in the README.

Vercel's Python runtime loads the top-level `app` from [`app.py`](../app.py) and routes every request to it. That file is the only deployment-specific code in the repository, and it exists because a serverless host changes three things about the environment:

| Constraint | What Relay does about it | Where it's stated |
|---|---|---|
| Filesystem is read-only apart from `/tmp` | `RELAY_DB=/tmp/relay/relay.db` | banner + `app.py` |
| No persistent disk between cold starts | `RELAY_AUTOSEED=1` re-seeds the demo organisation | banner |
| Process frozen between requests, so a worker thread never fires a deadline | `RELAY_WORKER_ENABLED=0`; `scheduler.tick()` runs on the way into the dashboard and each gap | banner |

Every page of the hosted instance carries a banner saying so. **State is per-instance and resets when the instance recycles** — that is a property of the host, not of Relay, and a local run has a persistent database and a real background worker.

`scheduler.tick()` is the only path that advances a workflow in both cases. The hosted demo calls it from a different place, not in a different way.

### Environment set on the deployment

| Variable | Value | Why |
|---|---|---|
| `RELAY_MODEL_PROVIDER` | `offline` | The deployment has no AWS credentials; forcing `offline` means it can never half-fall-back |
| `RELAY_DB` | `/tmp/relay/relay.db` | Only writable path |
| `RELAY_AUTOSEED`, `RELAY_EPHEMERAL` | `1` | Seed on cold start, and say so |
| `RELAY_WORKER_ENABLED` | `0` | No long-lived process |
| `RELAY_PUBLIC_BASE_URL` | the public domain | Otherwise every volunteer link in every message points at localhost |
| `RELAY_TOKEN_SECRET`, `RELAY_INTAKE_TOKEN` | random | Not in the repository |
| `RELAY_COORDINATOR_TOKEN` | published | Deliberately shared so judges can sign in |

`RELAY_EMAIL_TRANSPORT` stays at its default of `fake`, and the allowlist stays at `relay.test`. **The hosted demo cannot send email**, by two independent checks.

## Reproducing it

```bash
npm i -g vercel
vercel link --project relay
vercel env add RELAY_TOKEN_SECRET production     # paste a random value
vercel env add RELAY_COORDINATOR_TOKEN production
vercel env add RELAY_DB production               # /tmp/relay/relay.db
vercel env add RELAY_WORKER_ENABLED production   # 0
vercel env add RELAY_AUTOSEED production         # 1
vercel env add RELAY_EPHEMERAL production        # 1
vercel env add RELAY_MODEL_PROVIDER production   # offline
vercel deploy --prod
```

[`vercel.json`](../vercel.json) excludes tests, the evaluation and docs from the function bundle and allows 30s per invocation.

**Turn Vercel's Deployment Protection off for this project.** It is on by default and gates every URL
except a project domain behind a Vercel login, which would stop a reviewer dead. Settings &rarr;
Deployment Protection &rarr; Vercel Authentication &rarr; Disabled. Relay has its own coordinator token;
the platform's SSO wall only blocks the people who are supposed to look at it.

Attach the public hostname as a **project domain**, not as a deployment alias. An alias points at one
specific deployment and does not move when you redeploy; a project domain follows production
automatically.

## A host with a disk, if you want the real thing

Nothing about Relay needs serverless. On any host with a persistent volume — Fly.io, Render, a VM, AgentCore Runtime — drop all four serverless variables and run:

```bash
pip install -e .
RELAY_DB=/data/relay.db python -m relay serve --host 0.0.0.0 --port 8000
```

You then get the background worker on a thread, a database that survives restarts, and the restart-resumption behaviour that `test_state_survives_a_restart_without_resending` covers. To send real email, set `RELAY_EMAIL_TRANSPORT=smtp`, the SMTP variables, and **widen `RELAY_EMAIL_ALLOWLIST_DOMAINS` deliberately** — it is the last thing standing between a misconfiguration and a real volunteer's inbox.

## Before pointing this at anyone real

- Change `RELAY_TOKEN_SECRET` and both access tokens; the UI shows a red banner while defaults are in use.
- The coordinator sign-in is a single shared token with no user accounts, roles or CSRF protection. Put it behind real auth first.
- There is no rate limiting on the HTTP surface.
- Agree data retention and deletion with the organisation before importing a real roster. See [DISCLOSURES.md](DISCLOSURES.md).
