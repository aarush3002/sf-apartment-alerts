#!/bin/bash
set -euo pipefail
LABEL=com.aptbot.sf
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
echo "stopped and removed $LABEL"
