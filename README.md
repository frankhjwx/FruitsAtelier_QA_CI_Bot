# FruitsAtelier Discord Bot

Papaya connects Discord directly to the OpenAI Responses API. Its server handles
questions, evidence retrieval and job tracking. GitHub-hosted runners handle
source changes, tests, screenshots and experimental Windows packages.

This is an independent repository. Its default `main` branch contains the Bot
and workflows. The editor repository is read at `dev`; development tasks publish
only isolated `codex/discord-*` branches there. Bot changes do not modify the
editor's `dev` or `main` branches.

## User commands

- `@Papaya <question>` or `/ask`: answer from public documentation, source and
  recently updated issues. The bot translates search keywords when needed and
  cites commit-pinned evidence. The 10–30 second target is a response target,
  not a guarantee of bug reproduction. A lookup has a 26-second deadline.
- `/template`: concise usage, bug and feature request templates.
- `/develop`: attempt a small change on a new `codex/discord-<interaction-id>`
  branch based on `dev`. The request is supplied to a public GitHub workflow;
  do not include secrets, personal files or private information.
- `/revise <task_id> <feedback>`: continue the same branch. Only the original
  requester in the original channel can continue a task. Each attempt has its
  own task ID and budget reservation.
- `/task <task_id>`: retrieve status and the workflow link, including after a
  Discord delivery failure.
- `/screenshot`: show the published documentation overview image.

Guild access is limited to the two configured FruitsAtelier servers. Owner DMs
are supported for questions. Message content is read only for direct bot
mentions and DMs; privileged Message Content intent is not needed. Slash
commands are registered per guild. Mention replies do not automatically start
development: use `/develop` or `/revise` explicitly.

## Evidence and screenshots

The in-memory index refreshes every 15 minutes from `dev` and the 100 most
recently updated public issues/PRs. A stale successful snapshot remains available
when refresh fails; answers receive its synchronization timestamp. Repository
evidence can describe unreleased behavior. Issue reports are not proof of
reproduction, and an absent search result does not establish that a feature or
bug is absent.

Successful development runs provide packages and checksums as GitHub Actions
artifacts with seven-day retention. Users must sign into GitHub to download
them. Windows packages use `0.0.0-alpha.<run-number>` and are for separate test
installations, not the stable updater feed. macOS regressions run, but this
workflow does not distribute macOS packages.

When the existing native screenshot check succeeds, up to two PNGs are sent to
Discord directly from memory. These show the demo map, not a reproduction of
the user's report. Specific reproduction scenes and user-uploaded image
understanding are not implemented. Package ZIPs are downloaded by users from
GitHub; the Bot server does not download or extract them.

## Budget and execution boundaries

QA uses `gpt-6-luna`; development uses `gpt-6.1-sol`. Standard processing is used.
The current price assumptions are $0.10/$0.50 and $2/$10 per million input/output
tokens respectively. Conservative reservations include a cache-write margin.
See the [official model pages](https://developers.openai.com/api/docs/models).

The durable UTC-calendar-month ledger limits automatic QA to $10 and development
reservations to $80, leaving $10 of a $100 target outside automated allocation.
QA reserves $0.02 before a request and settles successful calls using reported
usage. Failures retain the reservation. Development reserves $0.50 per attempt;
unused development reservation is not refunded. The worker also checks each
model call against the same per-attempt ceiling using a conservative UTF-8 byte
bound. Some requests will exceed this small-change budget and stop without a
package. Five daily development attempts per user and five pending jobs globally
bound the queue. These application limits are not a replacement for account
billing controls and do not include manually dispatched workflows or other apps.

The development worker can search/read repository text and replace exact text
in up to eight C#, Markdown or RESX files under source, tests, docs and macOS.
It cannot edit workflow/build configuration, dependencies or its own controller.
It has no shell tool and never executes generated code. Separate Windows and
macOS jobs execute the changed source without OpenAI/Discord secrets and with
read-only repository permissions. No changes are merged automatically.

## Deployment

Requires Python 3.13+, a Discord bot with View Channel, Send Messages, Embed
Links and Attach Files permissions, and the `applications.commands` scope.
Install `requirements.txt` in a dedicated virtual environment. The server stores
only SQLite state and credentials; it never runs editor tests/builds or creates
an `artifacts/` directory.

The `discord-develop.yml` workflow must exist on this Bot repository's default
`main` branch before GitHub will accept dispatch. Add the CI service-account key
as repository secret `FA_OPENAI_CI_KEY`. Add `FA_SOURCE_TOKEN`, a separate GitHub
fine-grained token restricted to `frankhjwx/FruitsAtelier` with **Contents: Read
and write**, to publish experimental branches. It is passed only to the trusted
editing worker; test/build jobs do not receive it. No Discord token is needed
in GitHub Actions.

On the server, install this directory at `/opt/fruitsatelier-discord`, create
`/var/lib/fruitsatelier-discord` owned by `fruitsatelier-qabot`, and install the
unit from `deploy/fruitsatelier-discord.service`. Place the Discord token in
`/etc/fruitsatelier-discord/discord-token`, root-owned and group-readable only by
`fruitsatelier-qabot`. Run the interactive credential helper as root:

```sh
python3 /opt/fruitsatelier-discord/deploy/configure.py
```

It prompts without echo for the OpenAI QA service-account key and a GitHub
fine-grained token restricted to `frankhjwx/FruitsAtelier_QA_CI_Bot`, with **Actions: Read
and write**, **Contents: Read**, and the automatically required metadata access.
The server token does not need source-write permission. Then:

```sh
systemctl enable --now fruitsatelier-discord
systemctl status fruitsatelier-discord --no-pager
```

Do not commit credentials or paste them into Discord. The OpenAI keys only need
Responses API write permission. Use separate QA and CI service accounts in the
same OpenAI project for independent revocation and usage tracking.

## Verification

From this directory, with the dependencies installed:

```sh
python -m unittest discover -s tests -v
```

Before opening to users, verify a real `/ask` with source links and measure its
latency; submit one bounded `/develop`, check both platform jobs, download and
run the test package, confirm screenshot delivery if capture was available,
and use `/revise` to verify branch continuation. A missing key or a mocked API
test cannot establish live model access, latency or successful code generation.
