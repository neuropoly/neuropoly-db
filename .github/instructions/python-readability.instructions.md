---
description: "Use when writing or formatting Python source and tests: preserve readable logical paragraphs with Black-compatible spacing."
applyTo: "src/**/*.py,test/**/*.py"
---

# Python readability

- Separate meaningful phases inside functions with one blank line: guards,
  setup, I/O with its error handling, parsing, validation, and result handling.
  These are cues, not mandatory sections.
- Keep related guards, assignments and their immediate uses together. Do not
  add a blank line after every `if`, loop, or statement, or pad trivial functions.
- Keep `try`/`except` and `if`/`elif`/`else` intact. Do not add blank lines inside
  multiline arguments or literals to indicate phases.
- In tests, separate arrange, act, and assert when useful, without redundant
  comments or breaking up a cohesive assertion group.
- Use Black for wrapping and isort for imports; preserve intentional import
  ordering. Neither tool infers semantic paragraph boundaries.

Before:

```python
if not path.is_file():
    raise ValueError("Expected a file")
content = path.read_text()
return parse(content)
```

After:

```python
if not path.is_file():
    raise ValueError("Expected a file")

content = path.read_text()
return parse(content)
```

Install the locked development tools in the project environment:

```bash
uv sync --locked --group dev
```

Format using the locked project environment:

```bash
uv run --locked isort src test
uv run --locked black src test
```

To check without rewriting, use `uv run --locked isort --check-only src test`
and `uv run --locked black --check src test`. VS Code uses these project tools
on save; run the setup command if a formatter is missing. Style configuration
lives in `pyproject.toml`.

Review logical spacing separately from formatter checks. Keep cleanup scoped
to repository-owned Python in `src/` and `test/`; exclude fixtures, generated
or vendored code, data, snapshots, and submodules. Do not change behavior
during formatting.
