#!/usr/bin/env bash
# Runs once when the codespace is created. Installs dependencies, builds the mock database, and puts the
# Claude Code CLI that ships inside claude-agent-sdk on the PATH, so you can sign in by typing `claude`.
set -euo pipefail

pip3 install --user --quiet -r requirements.txt
python3 -m mockworld.seed

cli="$(python3 -c 'import claude_agent_sdk, pathlib; print(pathlib.Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude")')"
sudo ln -sf "$cli" /usr/local/bin/claude

echo
echo "Setup complete. Next: run 'claude' in the terminal, sign in with your Claude account, then type /exit."
echo "The demo starts on port 8501. If it was already open before you signed in, click 'Reset demo'."
