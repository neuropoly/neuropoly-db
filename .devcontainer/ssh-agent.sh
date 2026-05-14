#!/usr/bin/env bash
# Ensure ssh-agent is running and SSH keys from ~/.ssh are loaded.
# Sourced from ~/.bashrc on every interactive terminal session.
# Safe to source multiple times — exits early if agent already has identities.

[[ $- != *i* ]] && return 0

_SSH_ENV="$HOME/.ssh/agent-env"

_load_ssh_keys() {
    find "$HOME/.ssh" -maxdepth 1 -type f ! -name "*.pub" ! -name "known_hosts" \
        ! -name "authorized_keys" ! -name "config" ! -name "agent-env" \
        -exec echo {} \; -exec ssh-add {} \; 2>/dev/null || true
}

_start_ssh_agent() {
    ssh-agent > "$_SSH_ENV"
    chmod 600 "$_SSH_ENV"
    # shellcheck source=/dev/null
    source "$_SSH_ENV" > /dev/null
    _load_ssh_keys
}

if [ -f "$_SSH_ENV" ]; then
    # shellcheck source=/dev/null
    source "$_SSH_ENV" > /dev/null
    ssh-add -l > /dev/null 2>&1
    case $? in
        0) ;; # Agent running and has keys — nothing to do
        1) _load_ssh_keys ;;  # Agent running but no keys loaded
        2) _start_ssh_agent ;;  # Agent socket gone, restart
    esac
else
    _start_ssh_agent
fi

unset -f _load_ssh_keys _start_ssh_agent
