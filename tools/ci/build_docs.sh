#!/bin/sh
# Builds the IDE's documentation data with LiveCode's own docs builder, as
# LiveCode's build machines did before packaging ("builder_tool.livecodescript
# --stage docs"): the Dictionary's API database and data
# (ide/Documentation/html_viewer/resources/data/api, api_livecode_script,
# api_livecode_builder) and the guides (.../data/guide), which
# Installer/package.txt installs and which LiveCode's repositories did not
# keep. Linux or macOS (the builder runs shell commands and loads its
# externals as there).
#
#   tools/ci/build_docs.sh <build output folder> <work folder>
#
# <build output folder> is a Linux build's linux-<arch>-bin or the macOS
# build's Release folder: its development engine runs the builder, which
# reads the LiveCode Builder interfaces from its modules/lci.
#
# For the documentation of the externals, the builder unpacks LiveCode's
# mergExt and tsNet bundles of the Business edition (builderExtUnpack
# "Business": mergExt_Business_2021-6-16.zip and tsNet_Business_1.4.5.zip
# from downloads.livecode.com), as LiveCode's own Community builds did:
# LiveCode Community 9.6.3's Dictionary documents tsNet and every mergExt
# external although its packages ship neither tsNet nor most of mergExt.
# Only their api.lcdoc files are read; nothing of them is packaged. This
# script downloads them where the builder looks first; when LiveCode's
# server no longer serves one, it puts a stand-in there instead (for
# mergExt the packages' own Ext bundle, Installer/legacyoxt/ext/Ext; for
# tsNet nothing), and the Dictionary then lacks those entries (a warning
# says so). Writes into the checkout's ide/Documentation; nothing else
# outside <work folder>.

set -eu

if [ $# -ne 2 ]; then
  echo "usage: $0 <build output folder> <work folder>" >&2
  exit 2
fi
bin=$(cd "$1" && pwd -P)
mkdir -p "$2"
work=$(cd "$2" && pwd -P)
repo=$(pwd -P)
if [ ! -f "$repo/builder/builder_tool.livecodescript" ]; then
  echo "$0: run it from the repository root" >&2
  exit 2
fi

if [ -x "$bin/LiveCode-Community" ]; then
  engine="$bin/LiveCode-Community"
  platform=linux-x86_64
elif [ -x "$bin/LiveCode-Community.app/Contents/MacOS/LiveCode-Community" ]; then
  engine="$bin/LiveCode-Community.app/Contents/MacOS/LiveCode-Community"
  platform=macosx
else
  echo "$0: no development engine in $bin" >&2
  exit 2
fi

# builderFetchEngine finds a platform's build as <engine dir>/<name>-bin;
# the docs read the LiveCode Builder interfaces of "mac-bin"
mkdir -p "$work/engines" "$work/work/ext/downloads" "$work/output"
for name in mac-bin linux-x86_64-bin; do
  rm -f "$work/engines/$name"
  ln -s "$bin" "$work/engines/$name"
done

# builder_utilities: builderEnsureZip uses a bundle that is already at its
# download path (builderMergExtDownloadedFilePath, kMergExtVersion;
# builderTSNetDownloadedFilePath, kTSNetVersion) and downloads it otherwise
downloads="$work/work/ext/downloads"
fetch() {   # <url> <file>: 0 when <file> is a zip archive from <url>
  rm -f "$2"
  curl -fsSL --retry 3 --max-time 600 -o "$2" "$1" 2> /dev/null && unzip -tq "$2" > /dev/null 2>&1
}
mergext="$downloads/MergExt-Business-2021-6-16.zip"
if fetch https://downloads.livecode.com/mergext/mergExt_Business_2021-6-16.zip "$mergext"; then
  echo "mergExt: LiveCode's Business bundle ($(wc -c < "$mergext" | tr -d ' ') bytes)"
else
  echo "::warning title=Docs::LiveCode's mergExt Business bundle could not be downloaded; the Dictionary documents only the mergExt externals that the packages ship"
  rm -f "$mergext"
  (cd "$repo/Installer/legacyoxt/ext/Ext" && zip -qr "$mergext" .)
fi
tsnet="$downloads/tsNet_Business_1.4.5.zip"
if fetch https://downloads.livecode.com/tsNet/tsNet_Business_1.4.5.zip "$tsnet"; then
  echo "tsNet: LiveCode's Business bundle ($(wc -c < "$tsnet" | tr -d ' ') bytes)"
else
  echo "::warning title=Docs::LiveCode's tsNet bundle could not be downloaded; the Dictionary has no tsNet entries"
  rm -f "$tsnet"
  printf 'tsNet was not available; its documentation is left out.\n' > "$work/README-tsNet.txt"
  (cd "$work" && zip -q "$tsnet" README-tsNet.txt)
fi

echo "Docs builder: $engine ($platform)"
"$engine" -ui "$repo/builder/builder_tool.livecodescript" \
  --platform "$platform" --stage docs --edition community \
  --engine-dir "$work/engines" --work-dir "$work/work" --output-dir "$work/output"

data="$repo/ide/Documentation/html_viewer/resources/data"
status=0
for d in api api_livecode_script api_livecode_builder guide; do
  if [ -d "$data/$d" ] && [ -n "$(ls -A "$data/$d")" ]; then
    echo "  $d: $(find "$data/$d" -type f | wc -l | tr -d ' ') files, $(du -sk "$data/$d" | cut -f1) KB"
  else
    echo "$0: the docs builder wrote no $data/$d" >&2
    status=1
  fi
done
exit $status
