FROM mcr.microsoft.com/devcontainers/python:1-3.12-bookworm

# uv manages the project environment (.venv) from uv.lock
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /uvx /bin/

# GitHub CLI from GitHub's apt repository. It installs to /usr/bin/gh, the
# path used by the gh credential helper that VS Code copies from the host
# gitconfig. The base image's Yarn apt source has an expired signing key that
# breaks apt-get update, so it is removed.
RUN rm -f /etc/apt/sources.list.d/yarn.list \
    && install -d -m 0755 /etc/apt/keyrings \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && chmod a+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive \
        apt-get install --yes --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

# Mount points for the Claude Code and GitHub CLI config volumes (see
# devcontainer.json). They must exist and be owned by vscode, or the new
# volumes are owned by root.
RUN install -d -o vscode -g vscode \
        /home/vscode/.claude /home/vscode/.config /home/vscode/.config/gh
