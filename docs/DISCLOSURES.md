# Disclosures

## Pre-existing work

None. Every file in this repository was written for this hackathon during the submission period. There is no prior codebase, no vendored source, and nothing carried over from another project.

Third-party dependencies are the ordinary kind, declared in `requirements.txt` and installed from PyPI:

| Package | Purpose |
|---|---|
| `strands-agents` 1.54.0 | The agent framework this project is built on |
| `fastapi`, `uvicorn`, `jinja2`, `python-multipart` | Web layer |
| `pydantic` | Pulled in by the above |
| `pytest`, `httpx` | Development only |

`boto3` and `botocore` arrive as dependencies of `strands-agents` and are used only on the Bedrock path.

No third-party CSS framework, icon set, font, or template was used. The stylesheet and the SVG architecture diagram were written for this project.

## AI assistance

This project was built with AI coding assistance (Claude). All of it was reviewed, run, and tested; the tests, the evaluation and the fixtures were designed as part of the work rather than generated as an afterthought.

## Data

Every volunteer, name, email address, note, shift and availability window in this repository is invented.

- Email addresses use `relay.test`. `.test` is reserved by [RFC 2606](https://www.rfc-editor.org/rfc/rfc2606) and is not routable, so a misconfiguration cannot reach a real inbox.
- The organisation, "Riverside Community Food Pantry (synthetic)", does not exist. The word *synthetic* is in the name so it cannot be quoted as a partner.
- No real volunteer data was collected, processed, displayed or stored at any point.
- No organisation logo, quotation, testimonial or endorsement appears anywhere in this project, because none was obtained.

## Claims not made

Stated explicitly so they cannot be read in by implication:

- **No real organisation has used Relay.** No partner is lined up and no user interviews were conducted.
- **No time saving is claimed.** No manual-versus-assisted baseline was run with a real coordinator, so there is no honest figure to report.
- **No production-readiness claim.** Thirty synthetic scenarios establish that the implemented behaviour is correct on those scenarios. Nothing more.
- **The Bedrock path is implemented but was not executed in the build environment**, because no AWS credentials were available there. Every number published in this repository was produced by the deterministic offline planner, which is not a language model. `python -m relay check-model --live` verifies the Bedrock path in one command.

## Security posture of this build

This is a hackathon demonstration, not a hardened deployment.

- Default secrets are development placeholders. The interface shows a red banner while they are in use.
- Coordinator sign-in is a single shared token. There are no user accounts, roles, or audit of who signed in.
- There is no rate limiting on the HTTP surface and no CSRF token on coordinator forms; the app is intended to be run locally or behind a reviewer-only URL.
- Volunteer links are HMAC-signed, single-use, expiry-bound and scoped to one action on one outreach row for one shift version.
- Outbound delivery is allowlisted at two layers and defaults to a transport that sends nothing.

None of these are secrets: they are what a reviewer should know before pointing this at anything real.

## Licence

MIT, see [`LICENSE`](../LICENSE). No dependency in `requirements.txt` carries a licence incompatible with redistributing this repository under MIT.
