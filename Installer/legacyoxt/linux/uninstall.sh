#!/bin/sh
# Removes a copy of @TITLE@ that install.sh installed for this user: its
# desktop entry and icon (@DESKTOP_NAME@, when they belong to this copy)
# and this folder. The IDE's preferences and the stacks you made stay where
# they are.
#
#   ./uninstall.sh

set -eu

here=$(cd "$(dirname "$0")" && pwd -P)
desktop='@DESKTOP_NAME@'
data=${XDG_DATA_HOME:-"$HOME/.local/share"}
apps="$data/applications"
entry="$apps/$desktop.desktop"
marker=.legacyoxt-install

if [ ! -f "$here/$marker" ]; then
  echo "uninstall.sh: $here is not a copy that install.sh installed, so nothing was removed." >&2
  echo "(Run the uninstall.sh of the installed copy, or delete this folder yourself.)" >&2
  exit 1
fi

if [ -f "$entry" ] && grep -qF "$here/" "$entry"; then
  rm -f "$entry" "$data/icons/hicolor/48x48/apps/$desktop.png"
  if command -v update-desktop-database > /dev/null 2>&1; then
    update-desktop-database -q "$apps" 2> /dev/null || true
  fi
  echo "Removed the menu entry and icon of @TITLE@."
fi

cd /
rm -rf "$here"
echo "Removed $here."
