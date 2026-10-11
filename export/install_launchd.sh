#!/bin/bash
# Writes ~/Library/LaunchAgents/com.nanobotco.motdang-open.plist and loads it.
# Runs release_monthly.sh on the 1st of each month at 05:00; a Mac asleep then runs it on wake.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
label="com.nanobotco.motdang-open"
plist="$HOME/Library/LaunchAgents/$label.plist"
log="$HOME/Library/Logs/motdang-open.log"
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$here/release_monthly.sh</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Day</key><integer>1</integer>
    <key>Hour</key><integer>5</integer>
    <key>Minute</key><integer>0</integer>
  </dict>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOME</key><string>$HOME</string>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
  <key>StandardOutPath</key><string>$log</string>
  <key>StandardErrorPath</key><string>$log</string>
</dict>
</plist>
EOF
launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$plist"
launchctl print "gui/$(id -u)/$label" | head -12
