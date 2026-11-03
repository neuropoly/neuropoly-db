---
description: "Use when writing, pruning, or reviewing documentation, tutorials, setup guides, wiki pages, and documentation-related agent customization."
applyTo: "README.md,docs/**/*.md,wiki/**/*.md,.github/**/*.md"
---

# Documentation

- Write for one audience and one task: users, curators, developers, or node operators.
- Put prerequisites, ordered actions, commands/configuration, and a final check first.
  The procedure must work without reading explanatory sections.
- Keep procedures succinct and linear. Do not insert implementation discussions,
  historical context, option catalogues, or alternate workflows between steps.
- Prefer structured representations over paragraphs containing comma-separated
  lists of items:
  - Numbered lists for sequential actions.
  - Bulleted lists for prerequisites, requirements, and independent items.
  - Tables for comparisons, options, and repeated fields.
  - Mermaid graphs for flows, dependencies, and relationships when clearer than text.
  Keep short prose for explanations; do not add a table or graph that merely
  duplicates a list or makes a simple procedure harder to follow.
- Put necessary explanation and exhaustive reference in a separate block after the
  procedure. Use a dedicated wiki page for substantial shared architecture or procedures.
- Delete repetition, stale details, and prose without a current reader need. Do not
  preserve removed material by automatically relocating it.
- Prefer one standard example. Add alternatives only when the task needs a choice.
  Keep mutation, credential, data-loss, and verification warnings at the affected step.
- Use audience entry points and sparse links: required prerequisites, the next task,
  or specific reference needed for a decision. Do not repeat specialist catalogues.
- Keep existing tables of contents, including during pruning. Update their entries,
  order, and anchor links whenever headings or sections change. Sparse navigation
  is not a reason to remove a page's table of contents.
- Keep user workflows in `docs/`; put curation, development, and operations in the wiki.
  Use CLI help and source for implementation facts rather than copying them everywhere.
- Preserve existing worktree edits. Verify commands and configuration against local
  source; do not claim live verification when working offline.
- Protect `README.md` and `docs/install.md`: change only existing links when necessary.
  Ask for explicit validation before adding a link to either page. Preserve their
  linked destinations when reorganizing documentation.
- Apply these rules to documentation deliverables, not to the rigor of engineering
  investigation, acceptance tests, or implementation plans.
