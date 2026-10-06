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
# reads the LiveCode Builder interfaces from its modules/lci. The builder
# unpacks the mergExt bundle for its documentation (builderExtUnpack); its
# download URL is gone, so the bundle the packages carry
# (Installer/legacyoxt/ext/Ext) is put where the builder looks for the
# download first. Writes into the checkout's ide/Documentation; nothing
# else outside <work folder>.

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

# builder_utilities kMergExtVersion and builderMergExtDownloadedFilePath:
# builderExtUnpack "Business" unzips this file when it is there instead of
# downloading it
zip_file="$work/work/ext/downloads/MergExt-Business-2021-6-16.zip"
rm -f "$zip_file"
(cd "$repo/Installer/legacyoxt/ext/Ext" && zip -qr "$zip_file" .)

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
