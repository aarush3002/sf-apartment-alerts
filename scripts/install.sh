#!/bin/bash
# Install the bot as a macOS LaunchAgent so it starts at login and restarts if it crashes.
set -euo pipefail
PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL=com.aptbot.sf
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
mkdir -p "$HOME/Library/LaunchAgents" "$PROJECT/logs" "$PROJECT/data"
sed "s#__PROJECT__#$PROJECT#g" "$PROJECT/scripts/$LABEL.plist" > "$PLIST"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl enable "gui/$(id -u)/$LABEL"
launchctl kickstart -k "gui/$(id -u)/$LABEL"
echo "installed $PLIST"
sleep 2
launchctl print "gui/$(id -u)/$LABEL" | grep -E "state|pid" | head -3
echo "logs: $PROJECT/logs/aptbot.log"
