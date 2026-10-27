#!/usr/bin/env bash
# Sets up this machine for agent-driven Godot development. Safe to re-run.
#
#   ./install.sh                 everything
#   ./install.sh --no-register   skip MCP registration with Claude Code
#
# Installs into your home directory (no sudo). It prints the one apt command
# needed for system packages (Xvfb, ffmpeg) when they are missing:
#   ~/.local/bin/godot-agent     CLI (symlink into this repo)
#   ~/.local/bin/godot           symlink to your Godot binary, if `godot` is not on PATH yet
#   gdlint / gdformat            via `uv tool install gdtoolkit`
#   ~/.cache/godot-agent/api     class reference for the installed Godot version
#   ~/.agents/skills/godot-agent agent skill (linked from ~/.claude/skills)
#   Claude Code user-scope MCP servers: godot-agent, plus godot-mcp and aseprite-mcp when found
set -euo pipefail

KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$HOME/.local/bin"
REGISTER=1
for arg in "$@"; do
  case "$arg" in
    --no-register) REGISTER=0 ;;
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
  esac
done

step() { printf '\n\033[1m== %s\033[0m\n' "$1"; }
ok()   { printf '  ok   %s\n' "$1"; }
skip() { printf '  --   %s\n' "$1"; }

mkdir -p "$BIN"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "warning: $BIN is not on PATH; add it to your shell profile" ;; esac

step "CLI"
chmod +x "$KIT/bin/godot-agent"
ln -sfn "$KIT/bin/godot-agent" "$BIN/godot-agent"
ok "godot-agent -> $KIT/bin/godot-agent"

step "Godot"
GODOT="$("$KIT/bin/godot-agent" doctor --print-godot 2>/dev/null || true)"
if [[ -z "$GODOT" || ! -x "$GODOT" ]]; then
  echo "  Godot not found. Download it, then: godot-agent doctor --set-godot /path/to/Godot"
  exit 1
fi
ok "$("$GODOT" --version) at $GODOT"
if ! command -v godot >/dev/null; then
  ln -sfn "$GODOT" "$BIN/godot"
  ok "godot -> $GODOT (so agents can call plain 'godot')"
else
  skip "godot already on PATH: $(command -v godot)"
fi

step "System packages (need sudo once)"
missing=()
command -v Xvfb >/dev/null || missing+=(xvfb)      # off-screen rendering for captures
command -v ffmpeg >/dev/null || missing+=(ffmpeg)  # audio decoding and loudness (LUFS) checks
if [[ ${#missing[@]} -eq 0 ]]; then
  skip "Xvfb and ffmpeg already installed"
else
  echo "  missing: ${missing[*]}. Install them with:"
  echo "    sudo apt install ${missing[*]}"
fi

step "GDScript lint/format (gdtoolkit)"
if command -v gdlint >/dev/null; then
  skip "already installed: $(command -v gdlint)"
elif command -v uv >/dev/null; then
  uv tool install --quiet gdtoolkit && ok "gdlint, gdformat"
elif command -v pipx >/dev/null; then
  pipx install gdtoolkit && ok "gdlint, gdformat"
else
  skip "install uv or pipx, then: uv tool install gdtoolkit"
fi

step "Pillow (contact sheets)"
if python3 -c "import PIL" 2>/dev/null; then skip "already available"; else
  python3 -m pip install --user --quiet pillow 2>/dev/null && ok "pillow" || skip "pip refused; contact sheets will be skipped in the CLI (the MCP server has its own)"
fi

step "Godot class reference"
"$BIN/godot-agent" api build

step "Agent skill"
mkdir -p "$HOME/.agents/skills" "$HOME/.claude/skills"
ln -sfn "$KIT/skill" "$HOME/.agents/skills/godot-agent"
ln -sfn "../../.agents/skills/godot-agent" "$HOME/.claude/skills/godot-agent"
ok "~/.claude/skills/godot-agent -> ~/.agents/skills/godot-agent -> $KIT/skill"

step "MCP servers"
UV="$(command -v uv || true)"
if [[ -n "$UV" ]]; then
  # Resolve the MCP server's dependencies now, so the first agent call is fast.
  "$UV" run --quiet --script "$KIT/mcp/server.py" </dev/null >/dev/null 2>&1 || true
  ok "godot-agent MCP dependencies cached"
fi
if [[ $REGISTER -eq 0 ]]; then
  skip "registration skipped (--no-register)"
elif ! command -v claude >/dev/null; then
  skip "claude CLI not found; register manually (see README)"
else
  register() {  # name, then the command line
    local name="$1"; shift
    if claude mcp get "$name" >/dev/null 2>&1; then skip "$name already registered"; return; fi
    claude mcp add --scope user "$name" "$@" >/dev/null && ok "registered $name"
  }
  NODE="$(command -v node || true)"
  [[ -n "$UV" ]] && register godot-agent -- "$UV" run --quiet --script "$KIT/mcp/server.py"
  # Sibling MCP servers, when checked out and built next to this repo.
  GH="$(dirname "$KIT")"
  if [[ -n "$NODE" && -f "$GH/godot-mcp/build/index.js" ]]; then
    register godot -e GODOT_PATH="$GODOT" -- "$NODE" "$GH/godot-mcp/build/index.js"
  fi
  if [[ -n "$NODE" && -f "$GH/aseprite-mcp/build/index.js" ]]; then
    ASEPRITE="$(command -v aseprite || true)"
    if [[ -n "$ASEPRITE" ]]; then
      register aseprite -e ASEPRITE_PATH="$ASEPRITE" -- "$NODE" "$GH/aseprite-mcp/build/index.js"
    else
      register aseprite -- "$NODE" "$GH/aseprite-mcp/build/index.js"
    fi
  fi
fi

step "Check"
"$BIN/godot-agent" doctor
