# Workspace Copilot Instructions

## Hard Safety Rule: No Gitea Operations

Never execute any command, script, subprocess, or tool action that can interact with the Gitea server at data.neuro.polymtl.ca.

This prohibition includes direct and indirect paths, including but not limited to:
- Any `npdb` command that may reach Gitea.
- Any `git` command against Gitea repositories.
- Any HTTP/API request to Gitea.
- Any Python code path that calls subprocess or libraries to contact Gitea.

## Required Behavior

If a request would involve Gitea interaction:
1. Do not run the command.
2. State that workspace policy blocks Gitea interaction.
3. Offer a safe alternative that is fully offline (static code review, dry-run command construction, local-file edits, or tests not requiring network access).

## Strict Interpretation

If there is any uncertainty about whether a step could contact Gitea, treat it as prohibited and do not execute it.
