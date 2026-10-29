# FruitsAtelier Discord Bot

Papaya connects Discord directly to the OpenAI Responses API. Its server handles
questions, evidence retrieval and job tracking. Its private GitHub workflow
handles source changes and private sample-backed regression. The public FruitsAtelier
repository's GitHub-hosted workflow repeats release checks and builds experimental Windows packages.

This is an independent private repository. Its default `main` branch contains
the Bot and editing workflow. The editor repository is read at `dev`; development tasks publish
only the shared `codex-discord-bug-fix` branch there. Bot changes do not modify the
editor's `dev` or `main` branches.

## User commands

- `@Papaya <question>` or `/ask`: answer from public documentation, source and
  recently updated issues. The bot translates search keywords when needed and
  cites commit-pinned evidence through compact source buttons below the answer.
  Message bodies omit URLs and disable link previews. The 10–30 second target is a response target,
  not a guarantee of bug reproduction. A lookup has a 26-second deadline.

- `/template`: concise usage, bug and feature request templates.
- `/develop`: attempt a small change on `codex-discord-bug-fix`. The branch
  starts from `dev` if absent and retains existing commits between tasks. Generated code and commit summaries are public in the
  editor repository; do not include secrets, personal files or private information.
- `/revise <feedback> [task_id]`: continue the same branch. Omit `task_id` to
  select the latest task automatically. Only the original
  requester in the original channel can continue the latest task. Each attempt has its
  own task ID and budget reservation.
- `/task <task_id>`: retrieve status and the direct package link, including after a
  Discord delivery failure.

Replies to Papaya also trigger QA without an explicit mention. The bot reads up
to six preceding messages (1,000 characters each) from the same channel and
reply chain, using only the requester and Papaya messages. Context retrieval
has a three-second deadline; missing or inaccessible messages shorten context.
Earlier answers are rechecked against fresh repository evidence.


Guild access is limited to the two configured FruitsAtelier servers. Owner DMs
are supported for questions. Enable Message Content intent in the Discord developer portal so replies with
mentions disabled can be read. The bot responds only to mentions, replies to
itself, and eligible owner DMs. Slash
commands are registered per guild. Messages in any channel, including `feature-requests` and `bug-reports`, require
an explicit mention or a reply to Papaya. Unaddressed conversation is ignored.
QA identifies feature proposals and bug-fix intent in the same response call.
When appropriate, it offers a **Modify project and test** button for 15 minutes. Only the
original requester in the same channel can confirm; code editing starts only
after confirmation. Ordinary questions show no development button. Large subsystem requests and open-ended prototypes are declined by the model
without a development proposal; users are asked to narrow the scope. Concurrent
or repeated clicks cannot dispatch the same offer twice. A busy repair slot
leaves the offer available to retry until it expires. `/develop` and `/revise`
remain explicit command shortcuts.

## Evidence and test packages

The in-memory index refreshes every 15 minutes from `dev` and the 100 most
recently updated public issues/PRs. A stale successful snapshot remains available
when refresh fails; answers receive its synchronization timestamp. Repository
evidence can describe unreleased behavior. Issue reports are not proof of
reproduction, and an absent search result does not establish that a feature or
bug is absent.

Successful editor CI runs provide packages and checksums as GitHub Actions
artifacts in `frankhjwx/FruitsAtelier`, with seven-day retention. Users must sign
into GitHub to download them; access to this private Bot repository is not
required. Windows packages use `0.0.2-alpha.<run-number>` and are for separate test
installations, not the stable updater feed. macOS regressions run, but this
workflow does not distribute macOS packages.

After a successful package run, **Test passed - Open PR** lets the original
requester confirm their test result. The controller opens a PR from the shared
repair branch into `dev` only if the branch still matches the tested commit.
The PR triggers the editor repository's existing CI. It is never merged or set
to auto-merge by the Bot. The shared branch stays locked while PR creation or
maintainer review is pending. PR confirmation persists across Bot restarts.
The server reads a small private source receipt in memory; no package or artifact
folder is created on the server.

Package ZIPs are downloaded by users from GitHub; the Bot server does not
download or extract them.

## Budget and execution boundaries

Maintainers can set `benchmark=true` on the development workflow and supply a
pinned `source_commit` to evaluate a real task without publishing editor source,
creating a PR or packaging. This mode runs the normal private regression gates
and keeps the same model, scope checks and $0.50 cap. Its private result artifact
contains verification status and usage metadata, not source or user attachments.
Run benchmark tasks sequentially against the same commit for comparable results.

QA uses `gpt-6-luna`; development uses `gpt-6.1-sol`. Standard processing is used.
The current price assumptions are $0.10/$0.50 and $2/$10 per million input/output
tokens respectively. Conservative reservations include a cache-write margin.
The development worker preserves controller messages in the conversation prefix,
marks reusable message boundaries, and retains reasoning across turns for cache
reuse. Usage diagnostics include cached reads and cache writes by lifetime.
Validation retries continue the same in-memory conversation, with the same total
50-call allowance and dollar cap. Only failure excerpts are appended; original
attachment images are not resent as new messages. Identical query results refer
back to the retained evidence instead of adding a second full copy. Discovery history is
retained up to a bounded 180 KB compaction threshold (240 KB hard context limit).
After four query-only turns without edits, the worker records a concrete implementation
plan before continuing. All 13 existing Core localization JSON tables may be synchronized separately from
the eight-file source/documentation limit;
other JSON configuration remains outside the edit scope in both editing and validation.
Official cache diagnostics compare each completed response with its predecessor.
Only diagnostic type, reason and token counts are logged; unavailable or expired
comparisons do not stop repair. Private round diagnostics also include usage
counts, so cost analysis does not depend on waiting for package logs.
Retrieval expands common Chinese domain terms without a model call, keeps unknown
Chinese terms searchable, and includes complete bounded repository instructions.
Performance documentation is selected by section with explicit truncation markers.
Failed checks include source around their line number or enclosing indexed method.
Completed calls use Standard Sol prices when cache read/write usage is complete:
$2 ordinary input, $0.10 cached input, $2.50 for 30-minute writes, $5 for 12-hour
writes, and $10 output per million tokens. Incomplete or inconsistent cache usage
falls back to conservative rates. Future reservations never assume cache hits;
reported budget cost is an estimate, not the final API invoice.
See the [official model pages](https://developers.openai.com/api/docs/models).

The durable UTC-calendar-month ledger limits automatic QA to $10 and development
reservations to $80, leaving $10 of a $100 target outside automated allocation.
QA reserves $0.02 before a request and settles successful calls using reported
usage. Failures retain the reservation. Development reserves $0.50 per attempt;
unused development reservation is not refunded. PR creation uses no model call. The worker checks each model call against the same per-attempt ceiling.
Completed calls use reported token counts and cache breakdowns. The next call
reserves the maximum output plus reported prior tokens and a UTF-8 byte bound
for new tool results; missing usage retains the full conservative reservation. Some requests will exceed this small-change budget and stop without a
package. Each user can make 200 QA requests and 100 development attempts per rolling
24 hours. QA counting starts when this limit is deployed. One active repair globally
bound the queue. The private worker holds its concurrency slot through public
testing and packaging. An uncertain dispatch retains the repair slot until a
maintainer reconciles the GitHub run. These application limits are not a replacement for account
billing controls and do not include manually dispatched workflows or other apps.

The development worker supports scoped literal search with pagination, bounded line-range
reads and persistent working notes to reduce repeated queries after context compaction.
QA first checks whether the requested behavior is already supported. Its instructions suppress
development proposals for an already-satisfied goal, while retaining concrete defect reports and
distinguishing dev support from released behavior. This is a model judgment grounded in evidence.
Confirmed proposals carry QA's technical search query, investigation direction, evidence commit,
and up to six source locations. Locations must come from the evidence returned to QA.
CI prioritizes these leads and rereads source from its checkout; line-based excerpts are used only
when the evidence commit matches. Follow-up requests retain the leads as untrusted context.
The evidence limit excludes opaque encrypted reasoning, which remains in requests and
under the separate transport limit. Compaction removes only older query bodies, preserving
the latest tool batch and any original evidence referenced by its duplicate results.
Input reservations carry API-reported token usage across repair rounds and include new
messages and tool results; compaction does not reset the task's budget estimate.
Before generation, the harness builds a bounded context pack from likely implementation
symbols, related tests and API signatures. Repair rounds receive the current patch,
private regression and deduplicated failing diagnostics rather than the earlier exploration
history. Code search returns signatures and locations; source bodies are read on demand.
Model instructions remain stable; changing budget and workspace state are appended to the
request. Cache reuse is measured from API usage and is not assumed in budget accounting.
The Windows validation gate runs first; macOS waits for it and skips full regression if
Windows rejects the candidate. All successful candidates still require both platforms.
Private CI logs record tool names, argument hashes, result sizes and error types,
without tool bodies or original attachment contents.
The editing AI submits a candidate by calling `set_regression` after its source edits.
At the end of that tool batch the harness takes over without another model call,
even when there is no remaining AI budget. It runs baseline/fixed checks, regression
suites and packaging. Only failed validation returns to the AI, within the same
$0.50 task budget. Each repair round must resubmit its regression to avoid handing
off a partially edited candidate with an earlier round's test.
The explicit `finish` status remains available for declined or blocked work.
One premature text response
receives a continuation prompt within the same budget. Final response text, API status
and controller exit reason are retained in job-scoped private diagnostics on the server,
with the same original expiry and cleanup as attachment transfers (at most 24 hours).
They are not GitHub log output or public artifacts; only the editing capability can read them.
The development worker can search/read repository text and replace exact text
in up to eight C#, Markdown or RESX files under source, tests, docs and macOS.
It cannot edit workflow/build configuration, dependencies or its own controller.
It has no shell tool and never executes generated code. Separate Windows and
macOS jobs execute the changed source without OpenAI/Discord secrets and with
read-only repository permissions. No changes are merged automatically.

## Deployment

### Static code retrieval

QA uses a local C# syntax index built with Tree-sitter and queried through SQLite
FTS5. Methods, constructors, properties and type headers retain their source paths
and line ranges. Identifier splitting supports both exact symbols and component
words. This is structural and lexical retrieval, not vector embeddings or a
compiler-resolved call graph; it makes no additional model calls.
QA searches source and test symbols using the model's technical query, without appending the raw
user request to that query. QA can follow symbols across up to eight evidence searches/reads (nine model calls), with a 150-second
wall-clock limit and a $0.02 reservation. Ordinary questions can finish after one search.
Follow-up searches can target a source or test path; prior evidence and tool definitions
remain in the conversation. All development entrances, including `/develop` and `/revise`,
require implementation-code localization before reserving CI budget or dispatching.
Documentation-only or inconclusive lookups remain QA-only. Revisions rerun QA; confirmation
reuses current localization. This gate establishes source leads, not reproduction or correctness.

The server saves `code-index.sqlite3` beside the state database. Its commit SHA
and index format must match before reuse; a different revision triggers rebuilding
and atomic cache replacement during knowledge refresh. Only public repository
source is indexed. User attachments and credentials are never added to this cache.

CI exposes `code_search` using the same indexer against its own checked-out commit.
It includes relevant documentation and tests, and updates changed files after
each edit. Results identify the baseline SHA and working edits. The index remains
in runner memory and is not published. Server indexing never executes editor code.

Real benchmark dispatches require a QA handoff. Prepare one with
`python prepare_task.py --source-commit <sha> --request-file <file> --output <json>`
in the configured server environment. This uses the QA credentials and budget, records `qa_cost`,
and does not dispatch CI. Dispatch only when `accepted` is true, using the returned `request`
unchanged and the same `source_commit`. QA-declined requests remain QA-only. Report preparation
cost separately from the CI worker's $0.50 cap. The command never starts a benchmark sequence.

Parser dependencies are pinned to [tree-sitter 0.25.2](https://github.com/tree-sitter/py-tree-sitter)
and [tree-sitter-c-sharp 0.23.1](https://github.com/tree-sitter/tree-sitter-c-sharp),
both MIT licensed; installed distributions retain their license notices.

On the maintainer's Windows computer, run `python deploy/setup_wizard.py` for
one-session configuration. The local window accepts the QA and CI OpenAI keys
and the two scoped GitHub tokens described below. It provides prefilled GitHub
token-creation links and the Discord invitation link. It checks access, saves
the CI secrets through the locally authenticated GitHub CLI, and installs the
server credentials over the existing host-verified SSH connection. Secrets are
passed through stdin, never command-line arguments or local files. It performs
one small Responses request per OpenAI key locally and another QA request from
the deployment server. A failed server check prevents saving and starting. Startup after save
is optional. The window clears its fields after successful configuration.

Requires Python 3.13+, a Discord bot with View Channel and Send Messages
permissions, and the `applications.commands` scope.
Command registration skips configured guilds where the bot is not yet invited,
so an available test guild can be used before the production invitation.
Install `requirements.txt` in a dedicated virtual environment. The server stores
SQLite state, credentials and temporary user attachments; it never runs editor tests/builds or creates
an `artifacts/` directory.

The `discord-develop.yml` workflow must exist on this Bot repository's default
`main` branch. The editor repository's `discord-preview.yml` must exist on its
own default `main` branch. GitHub requires these locations for dispatch. The
editing workflow dispatches the editor workflow with an exact source SHA; the
Bot tracks both phases and sends users a direct link to the public test-package artifact.
Add the CI service-account key
as repository secret `FA_OPENAI_CI_KEY`. Add `FA_SOURCE_TOKEN`, a separate GitHub
fine-grained token restricted to `frankhjwx/FruitsAtelier` with **Contents: Read
and write**, **Actions: Read and write**, and **Pull requests: Read and write**,
to publish experimental branches and open user-confirmed PRs
and dispatch editor CI. It is passed only to the trusted
editing worker; test/build jobs do not receive it. No Discord token is needed
in GitHub Actions.

On a fresh Debian 13 server, install `python3-venv`, `ca-certificates` and `git`.
The installer creates the service account if it does not exist.
On the server, install this directory at `/opt/fruitsatelier-discord`, create
`/var/lib/fruitsatelier-discord` owned by `fruitsatelier-qabot`, and install the
unit from `deploy/fruitsatelier-discord.service`. Place the Discord token in
`/etc/fruitsatelier-discord/discord-token`, root-owned and group-readable only by
`fruitsatelier-qabot`. Run the interactive credential helper as root:

```sh
python3 /opt/fruitsatelier-discord/deploy/configure.py
```

It prompts without echo for the OpenAI QA service-account key and a GitHub
fine-grained token restricted to `frankhjwx/FruitsAtelier_QA_CI_Bot` and
`frankhjwx/FruitsAtelier`, with **Actions: Read and write**, **Contents: Read**,
and the automatically required metadata access.
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
run the test package, and use `/revise` to verify branch continuation. A missing key or a mocked API
test cannot establish live model access, latency or successful code generation.

## User attachments

Mention Papaya (or reply to it) with up to four attachments. `/ask` also accepts
one optional attachment. Supported formats:

- Images: PNG, JPEG, WebP, BMP and GIF (first frame only). Up to two images are
  sent per question, resized to fit 1536 x 1536. Video is not supported.
- Native files: `.catchdiff`, `.catchproj`, `.catchsync`; archives `.faz`, `.fad`,
  `.fpt` and legacy `.fap`; osu! files `.osu`, `.osz`, `.osk`, `.osb`.
- Diagnostics: `.log`, `.txt`, `.json`, `.ini`, and `.zip` diagnostic bundles.
- Audio: MP3, OGG, WAV, FLAC, AAC and M4A. Only header metadata (duration, sample
  rate, channels, bitrate) is inspected; the Bot does not listen, transcribe,
  fully decode or verify playback.

Files are limited to 25 MiB each and 40 MiB per message. Archive inspection reads
up to eight bounded text entries; it never extracts paths, runs files, or decodes
embedded media/nested archives. Large text includes labeled partial excerpts.
Invalid files produce an explicit unreadable-file observation for the model.
This is static support evidence, not an editor reproduction or import validation.
For beatmap issues, Papaya requests the smallest necessary sample once, and uses
repository documentation to explain FAZ/FAD export steps. It preserves relevant
observations in the proposed CI task. After confirmation, original uploads also
reach private CI through the expiring transfer service; they are never committed
to the public source repository or included in public test packages.

Replies can reuse attachments from the same user's explicit reply chain in the
same channel (up to three source messages). Expired attachments are not fetched
again automatically. The raw files and their temporary filename manifest live in
`/var/lib/fruitsatelier-discord/attachments`, with private directory/file modes.
No attachment contents are logged or added to the Bot database. The generated
QA response and development task can contain relevant findings.

`deploy/cleanup_attachments.py` is run by the independent systemd timer
`fruitsatelier-attachments-cleanup.timer` every hour. It deletes batches aged
23 hours minus one minute, leaving scheduling margin to keep local retention
below 24 hours during normal operation. Reads do not extend retention. Cleanup
also runs before Bot startup, including after downtime; a powered-off host cannot
physically delete files until it starts. This policy covers the Bot's local
copies, not the original Discord upload or provider-side retention.
Temporary storage is capped at 500 MiB. Deployment via `deploy/install.sh`
installs/enables the timer; check it with:

```sh
systemctl list-timers fruitsatelier-attachments-cleanup.timer
systemctl status fruitsatelier-attachments-cleanup.service --no-pager
```

QA and editing model instructions restrict assistance to Fruits Atelier and osu!,
including directly related beatmaps, resources and troubleshooting. Unrelated
requests are declined; unrelated parts of mixed requests are not answered.
This is a prompt-level policy, not a keyword blacklist or code-based topic filter.

## Sample-backed private regression

Confirmation binds the current and referenced attachment IDs to the task. `/revise`
reuses these IDs and accepts an optional replacement attachment; it requests fresh uploads if samples expired or have less than
two hours remaining. Transfer copies retain the earliest original upload time.
The hourly cleanup service also removes `/var/lib/fruitsatelier-discord/ci-transfers`.

The Bot serves per-job capabilities over TLS on port 8443. CI pins the certificate
fingerprint before sending authorization. Editor and validation capabilities have
separate write permissions; result submissions are immutable. Configure the non-secret
repository variables `FA_TRANSFER_URL` and `FA_TRANSFER_FINGERPRINT` in the private
Bot repository. `deploy/configure_transfer.py` creates the server certificate/key
and prints only its public SHA-256 fingerprint. No additional long-lived GitHub
credential is required: the trusted controller uses its job-scoped Actions token
to dispatch validation. The certificate key stays on the VPS.

The AI can inspect original sample text/archive entries and image evidence, and
writes a private C# regression using the real Core or Windows App APIs. A separate
Windows runner builds the same test against the unchanged baseline and candidate.
Baseline compilation failure is not a reproduction: the test must compile, raise
an explicit behavioral regression failure before the fix, and pass after it.
Supplied samples must be accessed through the private harness. Windows regression
suites and the macOS non-device checks also run. The controller verifies sample
hashes and test identity before publishing source and requesting a public package.
This does not establish manual GUI, audio perception or device-specific behavior.
Unsupported or unreproduced cases are reported as unverified, never repaired.

Each editing round permits up to 50 model turns, with remaining turns and task budget
included in the controller instructions. The shared $0.50 task cap still applies.
Failed validation returns private diagnostics to the AI for up to three rounds
within the same $0.50 task budget and workflow timeout. No raw sample, private test
or test output is uploaded as a GitHub artifact. They remain in expiring VPS transfer
storage and disposable runner directories, which are removed in finally/always
cleanup. Download deadlines leave sufficient time before the original expiry;
reruns do not renew retention. Public artifacts contain only the ordinary package
and a small source/verification receipt. Final results mention only the requester.

Maintainers can dispatch `discord-develop.yml` with `acceptance: true` using a
synthetic sample transfer. This validates the complete private flow, deliberately
injects a failed first validation round, and never publishes a source commit or
public package. The second round must pass the actual unchanged-test comparison.
