# Bot development

This repository is independent of the FruitsAtelier editor. Keep all Bot source,
deployment scripts, CI workflows and local artifacts here. Do not place Bot code
or configuration in the editor repository or commit it to the editor's dev branch.

- Server runtime: direct Discord gateway, OpenAI Responses API, public evidence
  retrieval and durable task/budget state. Never run editor builds or tests on
  the server, and never create an artifacts directory there.
- Editor changes: publish only isolated codex/discord-* branches in
  frankhjwx/FruitsAtelier. Generated source runs only in separate GitHub-hosted
  jobs without OpenAI, Discord or source-write credentials. Do not auto-merge.
- Secrets: never commit or print credentials. Keep separate QA and CI OpenAI
  service accounts, a server token scoped to Bot Actions, and a CI source token
  scoped to editor contents. Use hidden input for manual configuration.
- Verify with python -m unittest discover -s tests -v. Put local environments,
  logs and deployment archives under ignored artifacts/.
- Document implemented behavior and distinguish mock tests from live API,
  Discord, screenshot and package acceptance. Preserve the user's selected models.
- Do not start subagents unless explicitly requested.
