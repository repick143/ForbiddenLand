# Dotfiles

Personal shell and terminal configurations, kept in this repo so they can be
synchronized across devices via Git.

## Layout

- `zsh/zshrc` — Zsh configuration, deployed as `~/.zshrc`. Secrets are removed;
  machine-specific values are loaded from `~/.zshrc.local` (never tracked).
- `zsh/zshrc.local.example` — template for `~/.zshrc.local` (secrets, per-device
  overrides). Copy it to `~/.zshrc.local` on each device and fill in real values.
- `tmux/tmux.conf` — Tmux configuration, deployed as `~/.tmux.conf`. Plugin paths
  use `$HOME` instead of a hard-coded username.
- `install.sh` — idempotent installer: backs up any existing files, then symlinks
  the managed configs into `$HOME`, and creates `~/.zshrc.local` from the example
  if it does not exist yet.

## Install on a new device

```bash
git clone <repo> && cd <repo>
./dotfiles/install.sh
```

Then edit `~/.zshrc.local` with the real credentials/overrides for that device.
Install Tmux plugins afterwards if needed (`prefix + I` via TPM), and reload Tmux
config with `prefix + r`.

## Sync workflow

- Configs live here and are symlinked, so edits made via `~/.zshrc` /
  `~/.tmux.conf` are already edits to the repo.
- Never commit `~/.zshrc.local` or any real credentials.
- The example file and this repo must stay secret-free; check with
  `grep -n "accessKey\|JUMP_NEON_PASSWORD" dotfiles/` before pushing.

## Platform notes

- The Zsh config assumes a macOS machine (Homebrew, pyenv, goenv, jenv, nvm,
  pnpm paths). On Linux, adjust those paths in your copy or via
  `~/.zshrc.local`.
- The Tmux config uses macOS-specific clipboard commands (`pbcopy`,
  `reattach-to-user-namespace`). On Linux, switch those to `xclip`/`xsel` and
  drop the `reattach-to-user-namespace` wrapper in `default-command`.
