# Ext: the mergExt bundle of LiveCode's packages

`Installer/package.txt` installs a component `Ext` into every package:

```
component Ext
	into [[ToolsFolder]] place
		rfolder "ext:Ext"
```

`ext:` is the folder where LiveCode's builder unpacked the mergExt bundle
it downloaded (`builder/builder_utilities.livecodescript`:
`builderMergExtUnpack`, `kMergExtVersion` 2021-6-16,
`https://downloads.livecode.com/mergext/mergExt_Community_2021-6-16.zip`).
That server no longer serves it, so `Ext/` here is the bundle as LiveCode's
own installers installed it: the 46 files of `Ext/` in LiveCode Community
9.6.3's Windows installer (`LiveCodeCommunityInstaller-9_6_3-Windows-x86_64.exe`,
SHA-256 `46277079f694ecfc07baad66b40fb1fa3d10e317b968558d866b8e933929d66e`),
byte for byte. LiveCode built 9.6.3 with the same `kMergExtVersion` as this
source. `.gitattributes` keeps git from changing their line endings.

| folder | what | licence (its `LICENSE.txt`) |
|---|---|---|
| `blur-1.1.53` | the `blur` command (Trevor DeVore) | MIT |
| `mergJSON-1.0.70` | `mergJSONEncode`, `mergJSONDecode` (Monte Goulding) | GPLv3 |
| `mergMarkdown-1.0.66` | `mergMarkdownToXHTML` (Monte Goulding) | MIT |
| `mergMicrophone-1.0.66` | microphone recording, macOS only (Monte Goulding) | GPLv3 |

Each folder has the builds for every platform (Windows `.dll`, Linux `.so`,
macOS `.bundle` and `.dylib`). The IDE loads the ones its engine can at
startup (`Toolset/home.livecodescript`, `revInternal__SetupExternals`): the
macOS builds are i386 and x86_64, so on Apple Silicon none of them loads,
and the universal app skips them as an Intel-only LiveCode would. They are
installed and signed as they are (`tools/ci/sign_mac_app.py` and
`tools/ci/merge_universal.py --check` leave `Contents/Tools/Ext` alone).
