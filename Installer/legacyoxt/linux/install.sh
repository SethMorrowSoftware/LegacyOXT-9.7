#!/bin/sh
# Installs @TITLE@ (a LegacyOXT package) for this user, as LiveCode's own
# installer installed a user's copy: the program folder into
# ~/.runrev/components/@INSTALL_NAME@, and LiveCode's desktop entry and icon
# (@DESKTOP_NAME@), so that it is in the applications menu. Nothing needs
# administrator rights.
#
#   ./install.sh [folder]
#
# Run it from the extracted package folder. [folder] installs somewhere
# else instead. An earlier copy that install.sh put there is replaced.
# ./uninstall.sh in the installed folder undoes it all.

set -eu

here=$(cd "$(dirname "$0")" && pwd -P)
engine='@ENGINE@'
name='@INSTALL_NAME@'
desktop='@DESKTOP_NAME@'
data=${XDG_DATA_HOME:-"$HOME/.local/share"}
apps="$data/applications"
icons="$data/icons/hicolor/48x48/apps"
marker=.legacyoxt-install

if [ ! -x "$here/$engine" ]; then
  echo "install.sh: $here/$engine is missing; run install.sh in the extracted package folder." >&2
  exit 1
fi
if [ ! -f "$here/linux/$desktop.desktop" ] || [ ! -f "$here/linux/$desktop.png" ]; then
  echo "install.sh: $here/linux has no $desktop.desktop or $desktop.png." >&2
  exit 1
fi

dest=${1:-"$HOME/.runrev/components/$name"}
case $dest in
  /*) ;;
  *) dest="$(pwd -P)/$dest" ;;
esac
dest=${dest%/}
# The desktop entry quotes the engine's path; these characters would need
# escapes there that not every desktop reads alike
case $dest in
  *[\"\`\$\\%\&]* | *'
'*)
    echo "install.sh: the folder name $dest has a character (\" \` \$ \\ % & or a new line) that a desktop entry cannot hold; choose another folder." >&2
    exit 1 ;;
esac
case $dest/ in
  "$here"/*)
    if [ "$dest" != "$here" ]; then
      echo "install.sh: $dest is inside the package folder; choose another folder." >&2
      exit 1
    fi ;;
esac

if [ "$dest" != "$here" ]; then
  if [ -e "$dest" ] && [ ! -f "$dest/$marker" ]; then
    echo "install.sh: $dest exists and is not a copy that install.sh made; remove it or choose another folder." >&2
    exit 1
  fi
  rm -rf "$dest"
  mkdir -p "$dest"
  # tar keeps the modes, links and times
  (cd "$here" && tar cf - .) | (cd "$dest" && tar xf -)
fi
printf '%s\n' "$name" > "$dest/$marker"

mkdir -p "$apps" "$icons"
awk -v folder="$dest" '{ gsub(/\[\[TargetFolder\]\]/, folder); print }' \
  "$here/linux/$desktop.desktop" > "$apps/$desktop.desktop"
chmod 644 "$apps/$desktop.desktop"
cp "$here/linux/$desktop.png" "$icons/$desktop.png"
chmod 644 "$icons/$desktop.png"
if command -v update-desktop-database > /dev/null 2>&1; then
  update-desktop-database -q "$apps" 2> /dev/null || true
fi
if command -v gtk-update-icon-cache > /dev/null 2>&1 && [ -f "$data/icons/hicolor/index.theme" ]; then
  gtk-update-icon-cache -q -t "$data/icons/hicolor" 2> /dev/null || true
fi

echo "Installed @TITLE@ in $dest"
echo "  menu entry: $apps/$desktop.desktop"
echo "  start it from the applications menu, or run: \"$dest/$engine\""
echo "  remove it with: \"$dest/uninstall.sh\""
