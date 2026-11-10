#!/usr/bin/env bash
set -euo pipefail

# Dotfiles installer: symlink managed configs into $HOME (with backups).
# Usage: ./dotfiles/install.sh

DOTFILES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TS="$(date +%Y%m%d%H%M%S)"

link_file() {
    local src="$1" dst="$2"
    if [[ -e "$dst" || -L "$dst" ]]; then
        if [[ -L "$dst" && "$(readlink "$dst")" == "$src" ]]; then
            echo "already linked: $dst"
            return
        fi
        cp "$dst" "$dst.bak.$TS"
        echo "backed up: $dst -> $dst.bak.$TS"
    fi
    ln -sfn "$src" "$dst"
    echo "linked: $dst -> $src"
}

link_file "$DOTFILES_DIR/zsh/zshrc" "$HOME/.zshrc"
link_file "$DOTFILES_DIR/tmux/tmux.conf" "$HOME/.tmux.conf"

if [[ ! -f "$HOME/.zshrc.local" ]]; then
    cp "$DOTFILES_DIR/zsh/zshrc.local.example" "$HOME/.zshrc.local"
    chmod 600 "$HOME/.zshrc.local"
    echo "created $HOME/.zshrc.local from example - fill in your secrets"
else
    echo "kept existing $HOME/.zshrc.local"
fi

echo "done. For tmux: press prefix + I to install TPM plugins."
