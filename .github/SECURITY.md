# Security Policy

Read this before reporting anything. This repository is not a normal one, and the usual instinct — "I found a way to make the agent leak data, I should report it privately" — is wrong here.

## This project ships deliberate vulnerabilities

break-the-agent provisions intentionally vulnerable AI agents into a disposable cloud project so they can be attacked. Seven flags are planted for exactly that purpose. Capturing them is the intended use, not a security finding.

**Not vulnerabilities, by design:**

- Prompt injection succeeding against L1 or L2 agents. That is the demonstration.
- Flags captured at L1 or L2 through any technique. See the result matrix.
- The system prompt disclosing its contents at L1 or L2 (LLM08 is a planted category).
- `provision_account(role=admin)` succeeding at L1 or L2 (F6 exists to be captured).
- The deploy-time access code not being real authentication. FR-2 states plainly that it is a token-burning gate, not a security boundary, and it is documented as such rather than claimed to be more.
- Synthetic estate data appearing to be sensitive. All of it is fabricated (SEC-1).

## What is a real vulnerability

Anything that breaks **containment** — the property that a deployment can only harm the disposable project it was deployed into — or that breaks the lab's own plumbing, as opposed to the agents it is demonstrating on.

In scope, and genuinely worth reporting:

- **Escape from the deployment project.** Any path by which a deployed lab reaches resources outside its own project, organisation, or billing account.
- **Credential material in the lab's own infrastructure.** Provider API keys reaching agent context, a tool, or an error response (SEC-9); credential material in logs, transcripts, audit records or agent output at any level (VER-5); any long-lived service account key (SEC-5).
- **The lab acting as an open proxy.** Any tool accepting an arbitrary URL, or agent egress beyond the allowlist (SEC-4). This is the failure mode that would turn a deployed lab into someone else's infrastructure.
- **Flag values or the fixture manifest leaking into the repository**, published artefacts, or the site (schemas.md §3, VER-6).
- **CI or federation weaknesses** — anything letting a third party assume the CI identity, or crossing the pool separation in SEC-8.
- **Cost controls failing** — a path around the rate limits in NFR-3, the suite bypass token in NFR-3a, or the budget kill switch in SEC-3.
- **`terraform destroy` leaving resources behind** (NFR-6). An operator who believes they have torn the lab down and has not is exposed.
- **Anything in the site that reaches beyond the lab** — SSRF, arbitrary command execution, or similar. The agents are meant to be vulnerable; the site is not.

## A defeated L3 flag is a research finding, not a security report

If you defeat a flag at **L3** — the architectural level — that is the single most valuable result this project can produce, and it belongs in the open.

**Open a public issue.** Include the case, the model, the level, and enough transcript (redacted of flag values) to reproduce. It will be classified per SC-4 as either `absent` (the implementation missed a control the design specified) or `insufficient` (the control was correctly implemented and the architecture failed anyway), and published either way, including when it contradicts the project's central claim.

Do not report these privately. A finding that the architecture does not hold is the point of the exercise, and quietly patching it before publication would make the result matrix dishonest.

## Reporting a real vulnerability

For anything in the "real vulnerability" list above:

**Do not open a public issue.**

Use [GitHub's private vulnerability reporting](../../security/advisories/new). Include a description, steps to reproduce, the potential impact, and a suggested fix if you have one.

> Private vulnerability reporting must be enabled under **Settings → Code security** for that link to work.

## Before you deploy

The containment requirements in the README are not advisory. Deploy into a disposable project on a separate billing account with a hard budget cap, never into one sharing an organisation with anything you care about, and tear it down when finished.

## Supported versions

Only the latest commit on `main` is maintained.
