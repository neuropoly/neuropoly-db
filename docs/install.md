# Install npdb

Two commands, no Python knowledge needed. Works on Linux and macOS (on Windows, use [WSL](https://learn.microsoft.com/windows/wsl/install)).

## 1. Install the tools npdb relies on

`npdb` downloads with **git** and **git-annex**.

- **Ubuntu/Debian:**

  ```bash
  sudo apt-get install -y git git-annex
  ```

- **macOS** (with [Homebrew](https://brew.sh)):

  ```bash
  brew install git git-annex
  ```

## 2. Install uv

[uv](https://docs.astral.sh/uv/) installs `npdb` and the right Python for you.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Close and reopen your terminal** afterwards.

## 3. Install npdb

```bash
uv tool install "npdb[all] @ git+https://github.com/neuropoly/neuropoly-db"
```

## 4. Check it works

```bash
npdb --help
```

You should see the list of `npdb` commands.

> [!TIP]
> `npdb: command not found`? Run `uv tool update-shell`, then close and reopen your terminal.

## Update or remove

```bash
uv tool upgrade npdb      # get the latest version
uv tool uninstall npdb    # remove it
```

Next: [Find data](find-data.md)
