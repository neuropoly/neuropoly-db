---
agent: ask
description: "Use when you need a hard safety rule: never run any npdb command or related operation that can interact with the Gitea server."
---
# No Gitea Access Through npdb

You must follow this rule for the rest of this chat:

## Absolute rule
Under no circumstances run any `npdb` command that can interact directly or indirectly with Gitea.

## What counts as interaction
Treat all of the following as forbidden:
- Running `npdb` commands that fetch, clone, pull, push, list, or read datasets from Gitea.
- Running `npdb` commands that use credentials from `.env` for Gitea access.
- Executing Python code paths that call `subprocess` with `git` against Gitea URLs.
- Any command that can reach `data.neuro.polymtl.ca` through `npdb`, `git`, or HTTP APIs.

## Forbidden examples
Do not run commands like:
- `npdb whole-spine testo/ --no-verify-ssl`
- `npdb ...` when it may call clone/fetch from Gitea
- `git clone https://data.neuro.polymtl.ca/...`
- Any script that triggers these actions

## Required behavior instead
When a requested action would involve this forbidden area:
1. Refuse to execute it.
2. Explain briefly that Gitea-touching `npdb` operations are blocked by policy.
3. Offer an offline alternative (static analysis, dry-run logic review, command preview, or local-file-only changes).

## Strictness note
If there is uncertainty, assume the command is unsafe and do not execute it.
