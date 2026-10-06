script "oxt_smoke_test"
-- Headless smoke test for a build of OpenXTalk-Lite (Windows, Linux or macOS).
--
-- Run it with the development engine, without the IDE:
--
--    win-x86_64-bin\LiveCode-Community.exe -ui tools\ci\smoke-test.livecodescript
--    OpenXTalk-Lite.exe -ui tools\ci\smoke-test.livecodescript    (installed)
--
-- tools/ci/smoke-test.ps1 (Windows) and tools/ci/run_livecode_check.py
-- (every platform) do this and set the expected values below from the
-- source tree. The script prints one PASS or FAIL line per check to stdout
-- and quits with the number of failed checks as the exit code.
--
-- Optional environment variables:
--    OXT_EXPECT_VERSION   expected "the version" (BUILD_SHORT_VERSION)
--    OXT_EXPECT_SQLITE    expected sqlite_version() (SQLITE_VERSION in
--                         thirdparty/libsqlite/include/sqlite3.h)
--    OXT_EXPECT_PROCESSOR expected "the processor" (default x86_64)
--    OXT_EXTERNAL_SUFFIX  file name suffix of the externals: .dll (the
--                         default), .so on Linux, .bundle on macOS
--    OXT_EXTERNALS_DIR    folder with revdb, revxml and revzip
--    OXT_DRIVERS_DIR      folder with dbsqlite
--    OXT_CODE_FOLDERS     the code folders of an LCB extension whose
--                         libraries the IDE maps on this platform, as
--                         comma-separated patterns in the IDE's order
--                         (revideextensionlibrary __MapCodeLibraryForIDE):
--                         x86_64-linux*, or arm64-mac*,universal-mac* on
--                         Apple Silicon; default <processor>-win32*
--    OXT_XTALK_PROBES     file listing the bundled xTalk Suite extensions to
--                         load and call, in load order (see smoke-test.ps1)
--    OXT_EXTENSIONS_DIR   the layout's Extensions folder
-- Both folders default to the engine's folder, as in win-x86_64-bin. In an
-- installed layout they are Externals and Externals/Database Drivers. The
-- defaults are the Windows x86-64 values, so smoke-test.ps1 sets none of
-- the three platform variables.

local sCodeFolders, sSystemName

local sFails, sPasses

command check pName, pOK, pDetail
   if pOK then
      add 1 to sPasses
      write "PASS " & pName & ": " & pDetail & return to stdout
   else
      add 1 to sFails
      write "FAIL " & pName & ": " & pDetail & return to stdout
   end if
end check

on startup
   put 0 into sFails
   put 0 into sPasses
   put specialFolderPath("engine") into tEngine
   put specialFolderPath("temporary") into tTemp
   put $OXT_EXPECT_VERSION into tExpectVersion
   put $OXT_EXPECT_SQLITE into tExpectSqlite
   put $OXT_EXTERNALS_DIR into tExternals
   if tExternals is empty then put tEngine into tExternals
   replace "\" with "/" in tExternals
   put $OXT_DRIVERS_DIR into tDrivers
   if tDrivers is empty then put tEngine into tDrivers
   replace "\" with "/" in tDrivers
   put $OXT_EXPECT_PROCESSOR into tExpectProcessor
   if tExpectProcessor is empty then put "x86_64" into tExpectProcessor
   put $OXT_EXTERNAL_SUFFIX into tSuffix
   if tSuffix is empty then put ".dll" into tSuffix
   put $OXT_CODE_FOLDERS into sCodeFolders
   if sCodeFolders is empty then put the processor & "-win32*" into sCodeFolders
   -- What the dependency check is called: Windows DLL imports, or the
   -- libraries an ELF or Mach-O file needs
   if the platform is "Win32" then
      put "DLL imports" into sSystemName
   else
      put "native dependencies" into sSystemName
   end if
   write "INFO version=" & the version & " build=" & the buildNumber & " environment=" & the environment & " platform=" & the platform & " processor=" & the processor & return to stdout
   write "INFO engine=" & tEngine & return to stdout
   write "INFO externals=" & tExternals & " drivers=" & tDrivers & " suffix=" & tSuffix & " code folders=" & sCodeFolders & return to stdout

   try
      if tExpectVersion is empty then
         check "version", the version is not empty, the version
      else
         check "version", the version is tExpectVersion, the version & " (expected " & tExpectVersion & ")"
      end if
      check "processor", the processor is tExpectProcessor, the processor & " (expected " & tExpectProcessor & ")"

      -- Script engine
      put 0 into tSum
      repeat with i = 1 to 100
         add i to tSum
      end repeat
      check "arithmetic", tSum = 5050, tSum
      put "b,c,a" into tList
      sort items of tList
      check "sort", tList is "a,b,c", tList
      put "x" into tArray["k"]
      check "arrays", tArray["k"] is "x" and the keys of tArray is "k", the keys of tArray
      get matchText("abc123def", "([0-9]+)", tNumber)
      check "regular expressions (PCRE)", it and tNumber is "123", tNumber
      -- In LiveCode, matchText, matchChunk and replaceText are always
      -- case-sensitive, whatever the caseSensitive (OpenXTalk Lite changed
      -- that; this repository is LiveCode's own)
      set the caseSensitive to false
      check "regular expressions are case-sensitive (caseSensitive false)", not matchText("ABC", "abc") and replaceText("ABC", "b", "x") is "ABC", matchText("ABC", "abc") && replaceText("ABC", "b", "x")
      set the caseSensitive to true
      check "regular expressions are case-sensitive (caseSensitive true)", not matchText("ABC", "abc"), matchText("ABC", "abc")
      set the caseSensitive to false
      put "hello hello hello hello hello" into tData
      check "compress (zlib)", decompress(compress(tData)) is tData, the number of bytes in compress(tData) & " bytes compressed"
      get binaryDecode("H*", messageDigest("abc", "SHA-256"), tHex)
      check "SHA-256", tHex is "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", tHex

      -- Unicode (ICU)
      put "e" & numToCodepoint(0x301) into tCombined
      check "grapheme clusters (ICU)", the number of chars in tCombined = 1 and the number of codepoints in tCombined = 2, the number of chars in tCombined & " char, " & the number of codepoints in tCombined & " codepoints"
      check "astral codepoint", the number of codepoints in numToCodepoint(0x1F600) = 1, codepointToNum(numToCodepoint(0x1F600))
      put normalizeText(numToCodepoint(0xE9), "NFD") into tDecomposed
      check "normalizeText NFD (ICU)", the number of codepoints in tDecomposed = 2, the number of codepoints in tDecomposed & " codepoints"
      set the caseSensitive to true
      put toUpper("abc" & numToCodepoint(0xE9)) into tUpper
      check "toUpper", tUpper is ("ABC" & numToCodepoint(0xC9)), codepointToNum(codepoint -1 of tUpper)
      set the caseSensitive to false

      -- OpenSSL, through revsecurity (.dll, .so, .dylib next to the engine)
      encrypt "secret message" using "aes-256-cbc" with password "pw"
      put it into tCipher
      put the result into tEncryptResult
      decrypt tCipher using "aes-256-cbc" with password "pw"
      check "encrypt/decrypt aes-256-cbc (OpenSSL)", it is "secret message" and tCipher is not "secret message", "result='" & tEncryptResult & "', ciphertext " & the number of bytes in tCipher & " bytes"
      -- Blowfish is one of the older ciphers that OpenSSL 3 keeps in its
      -- legacy provider, which revsecurity has built in and the engine loads
      encrypt "secret message" using "bf-cbc" with password "pw"
      put it into tCipher
      put the result into tEncryptResult
      decrypt tCipher using "bf-cbc" with password "pw"
      check "encrypt/decrypt bf-cbc (OpenSSL legacy provider)", it is "secret message" and tCipher is not "secret message", "result='" & tEncryptResult & "', ciphertext " & the number of bytes in tCipher & " bytes"
      check "randomBytes", the number of bytes in randomBytes(16) = 16, the number of bytes in randomBytes(16) & " bytes"

      -- Windows light/dark mode support. Without a user interface the
      -- engine reports its default ("light"); the IDE reports the setting.
      put the systemAppearance into tAppearance
      check "systemAppearance", tAppearance is among the items of "light,dark", tAppearance

      -- Files and processes
      put tTemp & "/oxt_smoke_test.bin" into tFile
      put "round" & numToByte(0) & "trip" into URL ("binfile:" & tFile)
      check "binary file I/O", URL ("binfile:" & tFile) is ("round" & numToByte(0) & "trip"), the number of bytes in URL ("binfile:" & tFile) & " bytes"
      delete file tFile
      put shell("echo oxt") into tShell
      check "shell", word 1 of tShell is "oxt", word 1 of tShell

      -- Externals: revDB, revXML and revZip (on macOS, bundles)
      set the externals of the templateStack to tExternals & "/revdb" & tSuffix & return & tExternals & "/revxml" & tSuffix & return & tExternals & "/revzip" & tSuffix
      create stack "oxt_smoke_test_externals"
      start using stack "oxt_smoke_test_externals"
      put the number of lines of the externalCommands of stack "oxt_smoke_test_externals" into tCommandCount
      put the number of lines of the externalFunctions of stack "oxt_smoke_test_externals" into tFunctionCount
      check "externals load", tCommandCount > 0 and tFunctionCount > 0, tCommandCount & " commands, " & tFunctionCount & " functions"
   catch tError
      check "no script error (core checks)", false, tError
   end try

   -- The light and dark appearance (the appAppearance and stackAppearance)
   try
      checkAppearance
   catch tError
      check "no script error (appearance checks)", false, oneLine(tError)
   end try

   -- SQLite, through revDB and dbsqlite (revDB adds the platform's suffix)
   try
      revSetDatabaseDriverPath tDrivers
      put tTemp & "/oxt_smoke_test.sqlite" into tDatabase
      if there is a file tDatabase then delete file tDatabase
      put revOpenDatabase("sqlite", tDatabase) into tConnection
      check "SQLite open", tConnection is an integer, tConnection
      if tConnection is an integer then
         put revDataFromQuery(tab, return, tConnection, "select sqlite_version()") into tSqliteVersion
         if tExpectSqlite is empty then
            check "SQLite version", tSqliteVersion begins with "3.", tSqliteVersion
         else
            check "SQLite version", tSqliteVersion is tExpectSqlite, tSqliteVersion & " (expected " & tExpectSqlite & ")"
         end if
         revExecuteSQL tConnection, "create table t(id integer primary key, name text)"
         revExecuteSQL tConnection, "insert into t(name) values ('alpha'), ('beta')"
         put revDataFromQuery(tab, return, tConnection, "select group_concat(name) from t") into tRows
         check "SQLite query", tRows is "alpha,beta", tRows
         revExecuteSQL tConnection, "create virtual table ft using fts5(body)"
         revExecuteSQL tConnection, "insert into ft(body) values ('hello world'), ('goodbye moon')"
         put revDataFromQuery(tab, return, tConnection, "select body from ft where ft match 'hello'") into tMatch
         check "SQLite FTS5", tMatch is "hello world", tMatch
         put revDataFromQuery(tab, return, tConnection, "select json_extract('{" & quote & "a" & quote & ":{" & quote & "b" & quote & ":42}}', '$.a.b')") into tJson
         check "SQLite JSON", tJson is "42", tJson
         put revDataFromQuery(tab, return, tConnection, "select count(*) from pragma_compile_options where compile_options like 'ENABLE_RTREE%' or compile_options like 'ENABLE_FTS5%'") into tOptions
         check "SQLite RTREE and FTS5 compiled in", tOptions = 2, tOptions & " of 2"
         revCloseDatabase tConnection
      end if
      if there is a file tDatabase then delete file tDatabase
   catch tError
      check "no script error (SQLite checks)", false, tError
   end try

   -- revXML
   try
      put revXMLCreateTree("<root><item id=" & quote & "1" & quote & ">hi</item></root>", false, true, false) into tTree
      check "revXML parse", tTree is an integer, tTree
      if tTree is an integer then
         check "revXML node contents", revXMLNodeContents(tTree, "/root/item") is "hi", revXMLNodeContents(tTree, "/root/item")
         revXMLDeleteTree tTree
      end if
   catch tError
      check "no script error (revXML checks)", false, tError
   end try

   -- revZip
   try
      put tTemp & "/oxt_smoke_test.zip" into tZip
      if there is a file tZip then delete file tZip
      put "zip content 123" into tZipData
      revZipOpenArchive tZip, "write"
      revZipAddItemWithData tZip, "hello.txt", "tZipData"
      revZipCloseArchive tZip
      revZipOpenArchive tZip, "read"
      put revZipEnumerateItems(tZip) into tItems
      revZipExtractItemToVariable tZip, "hello.txt", "tUnzipped"
      revZipCloseArchive tZip
      check "revZip round trip", line 1 of tItems is "hello.txt" and tUnzipped is "zip content 123", line 1 of tItems & ": " & tUnzipped
      if there is a file tZip then delete file tZip
   catch tError
      check "no script error (revZip checks)", false, tError
   end try

   -- The xTalk Suite extensions bundled in Extensions (only when the
   -- layout has them)
   if $OXT_XTALK_PROBES is not empty then
      try
         checkXtalkExtensions $OXT_XTALK_PROBES, $OXT_EXTENSIONS_DIR
      catch tError
         check "no script error (xTalk extension checks)", false, oneLine(tError)
      end try
   end if

   write "SUMMARY passed=" & sPasses & " failed=" & sFails & return to stdout
   quit sFails
end startup

private function oneLine pText
   replace return with " " in pText
   replace numToCodepoint(13) with " " in pText
   return pText
end oneLine

-- The appAppearance and the stackAppearance (engine/src/appearance.cpp),
-- and the colours an object reports in the dark appearance (the effective
-- foreColor, MCObject::isdarkappearance in engine/src/object.cpp). Engines
-- without the properties (Tom Perry's 9.7.1-OXT engine, and OXT-Beyond 0.1.0 and earlier) print a SKIP line. Every use of the new syntax is inside "do": this script must still
-- compile on those engines.
private command checkAppearance
   local tValue, tError, tThrew, tSystem, tPaint, tLinux, tExternals
   local tMain, tSub, tFileA, tFileB, tSame
   try
      do "get the appAppearance"
   catch tError
      write "SKIP appearance (engine has no appAppearance)" & return to stdout
      exit checkAppearance
   end try

   -- On Linux the GTK theme decides (the properties are stored but change
   -- nothing), so the effective values there are the systemAppearance
   put the platform is "Linux" into tLinux
   put the systemAppearance into tSystem
   put paintDefaults() into tPaint

   -- The default, before anything sets it
   do "put the appAppearance into tValue"
   check "appAppearance default", isExactly(tValue, "light"), tValue

   do "set the appAppearance to " & quote & "Dark" & quote
   do "put the appAppearance into tValue"
   check "appAppearance set Dark", isExactly(tValue, "dark"), tValue
   do "put the effective appAppearance into tValue"
   if tLinux then
      check "effective appAppearance dark (Linux: the GTK theme's)", isExactly(tValue, tSystem), tValue & ", systemAppearance " & tSystem
   else
      check "effective appAppearance dark", isExactly(tValue, "dark"), tValue
   end if

   do "set the appAppearance to " & quote & "SYSTEM" & quote
   do "put the appAppearance into tValue"
   check "appAppearance set system", isExactly(tValue, "system"), tValue
   do "put the effective appAppearance into tValue"
   check "effective appAppearance system", isExactly(tValue, expectedEffective(tSystem, tLinux, tSystem)), tValue & ", systemAppearance " & tSystem

   put false into tThrew
   try
      do "set the appAppearance to " & quote & "bogus" & quote
   catch tError
      put true into tThrew
   end try
   do "put the appAppearance into tValue"
   check "appAppearance bogus", tThrew and isExactly(tValue, "system"), "threw " & tThrew & ", then " & tValue

   do "set the appAppearance to " & quote & "light" & quote
   do "put the effective appAppearance into tValue"
   if tLinux then
      check "effective appAppearance light (Linux: the GTK theme's)", isExactly(tValue, tSystem), tValue & ", systemAppearance " & tSystem
   else
      check "effective appAppearance light", isExactly(tValue, "light"), tValue
   end if

   check "appAppearance leaves the systemAppearance", the systemAppearance is tSystem, the systemAppearance & " (was " & tSystem & ")"
   check "appAppearance leaves the paint colours", paintDefaults() is tPaint, paintDefaults() & " (were " & tPaint & ")"

   -- The stackAppearance: "" inherits (a substack its mainstack's, a
   -- mainstack the appAppearance), runtime only
   put the externals of the templateStack into tExternals
   set the externals of the templateStack to empty
   put "oxt_smoke_appearance" into tMain
   put "oxt_smoke_appearance_sub" into tSub
   create stack tMain
   create stack tSub
   set the mainStack of stack tSub to tMain

   do "put the stackAppearance of stack tMain into tValue"
   check "stackAppearance default", tValue is empty, "'" & tValue & "'"

   do "set the appAppearance to " & quote & "dark" & quote
   do "put the effective stackAppearance of stack tMain into tValue"
   check "stackAppearance empty follows the appAppearance", isExactly(tValue, expectedEffective("dark", tLinux, tSystem)), tValue
   do "set the appAppearance to " & quote & "light" & quote

   do "set the stackAppearance of stack tMain to " & quote & "Dark" & quote
   do "put the stackAppearance of stack tMain into tValue"
   check "stackAppearance set Dark", isExactly(tValue, "dark"), tValue
   do "put the effective stackAppearance of stack tSub into tValue"
   check "substack inherits its mainstack", isExactly(tValue, expectedEffective("dark", tLinux, tSystem)), tValue

   do "set the stackAppearance of stack tSub to " & quote & "light" & quote
   do "put the effective stackAppearance of stack tSub into tValue"
   check "substack light in a dark mainstack", isExactly(tValue, expectedEffective("light", tLinux, tSystem)), tValue

   do "set the stackAppearance of stack tSub to " & quote & "system" & quote
   do "put the effective stackAppearance of stack tSub into tValue"
   check "stackAppearance system", isExactly(tValue, expectedEffective(tSystem, tLinux, tSystem)), tValue & ", systemAppearance " & tSystem
   do "set the stackAppearance of stack tSub to empty"

   put false into tThrew
   try
      do "set the stackAppearance of stack tMain to " & quote & "bogus" & quote
   catch tError
      put true into tThrew
   end try
   do "put the stackAppearance of stack tMain into tValue"
   check "stackAppearance bogus", tThrew and isExactly(tValue, "dark"), "threw " & tThrew & ", then " & tValue

   set the defaultStack to tMain
   create button "oxt_smoke_appearance_button"
   put false into tThrew
   try
      do "set the stackAppearance of button " & quote & "oxt_smoke_appearance_button" & quote & " of stack tMain to " & quote & "dark" & quote
   catch tError
      put true into tThrew
   end try
   check "only stacks have a stackAppearance", tThrew, "setting it on a button threw: " & tThrew

   -- Not saved: a stack saved after setting it is the same file
   put specialFolderPath("temporary") & "/oxt_smoke_appearance_a.livecode" into tFileA
   put specialFolderPath("temporary") & "/oxt_smoke_appearance_b.livecode" into tFileB
   do "set the stackAppearance of stack tMain to empty"
   save stack tMain as tFileA
   do "set the stackAppearance of stack tMain to " & quote & "dark" & quote
   save stack tMain as tFileB
   put URL ("binfile:" & tFileA) is URL ("binfile:" & tFileB) into tSame
   check "stackAppearance is not saved", tSame, the number of bytes in URL ("binfile:" & tFileA) & " and " & the number of bytes in URL ("binfile:" & tFileB) & " bytes"
   delete stack tMain
   do "put the stackAppearance of stack tFileB into tValue"
   check "stackAppearance empty after loading", tValue is empty, "'" & tValue & "'"
   delete stack tFileB
   delete file tFileA
   delete file tFileB

   -- The colours of objects in the dark appearance, through the effective
   -- foreColor: a stack designed light (the owner's libMQTTxt pattern: a
   -- light card, a white panel, a field with a white background and no text
   -- colour, a checkbox with no colours) keeps dark text; a stack with no
   -- colours gets white text. Not on Linux, where the GTK theme decides.
   if not tLinux then
      create stack "oxt_smoke_pattern"
      set the defaultStack to "oxt_smoke_pattern"
      set the backColor of card 1 of stack "oxt_smoke_pattern" to "240,242,246"
      create graphic "panel"
      set the style of it to "rectangle"
      set the opaque of it to true
      set the backColor of it to "255,255,255"
      set the rect of it to 10,10,400,300
      create field "mdHost"
      set the opaque of it to true
      set the showBorder of it to true
      set the threeD of it to false
      set the borderWidth of it to 1
      set the borderColor of it to "203,210,222"
      set the backColor of it to "255,255,255"
      set the rect of it to 20,20,200,44
      set the text of it to "broker.example.com"
      create button "mdTLS"
      set the style of it to "checkbox"
      set the rect of it to 20,60,200,84
      create stack "oxt_smoke_unstyled"
      set the defaultStack to "oxt_smoke_unstyled"
      create field "plain"
      set the text of it to "No colours"
      -- An opaque checkbox fills its rect with its own background: a white
      -- one keeps a dark label on a card with no colour (the opaque is set
      -- after the style, which clears it)
      create button "boxed"
      set the style of it to "checkbox"
      set the opaque of it to true
      set the backColor of it to "255,255,255"
      set the rect of it to 20,60,200,84

      do "set the appAppearance to " & quote & "dark" & quote
      put the effective foreColor of field "mdHost" of stack "oxt_smoke_pattern" into tValue
      check "dark: white field keeps dark text", relativeLuminance(tValue) < 0.179, tValue
      put the effective foreColor of button "mdTLS" of stack "oxt_smoke_pattern" into tValue
      check "dark: checkbox on a light panel keeps a dark label", relativeLuminance(tValue) < 0.179, tValue
      put the effective foreColor of field "plain" of stack "oxt_smoke_unstyled" into tValue
      check "dark: unstyled field gets white text", relativeLuminance(tValue) > 0.8, tValue
      put the effective foreColor of button "boxed" of stack "oxt_smoke_unstyled" into tValue
      check "dark: opaque white checkbox keeps a dark label", relativeLuminance(tValue) < 0.179, tValue

      do "set the appAppearance to " & quote & "light" & quote
      put the effective foreColor of field "mdHost" of stack "oxt_smoke_pattern" into tValue
      check "light: white field text is black", relativeLuminance(tValue) < 0.05, tValue
      put the effective foreColor of button "mdTLS" of stack "oxt_smoke_pattern" into tValue
      check "light: checkbox label is black", relativeLuminance(tValue) < 0.05, tValue
      put the effective foreColor of field "plain" of stack "oxt_smoke_unstyled" into tValue
      check "light: unstyled field text is black", relativeLuminance(tValue) < 0.05, tValue
      put the effective foreColor of button "boxed" of stack "oxt_smoke_unstyled" into tValue
      check "light: opaque white checkbox label is black", relativeLuminance(tValue) < 0.05, tValue

      delete stack "oxt_smoke_pattern"
      delete stack "oxt_smoke_unstyled"
   end if

   do "set the appAppearance to " & quote & "light" & quote
   set the externals of the templateStack to tExternals
end checkAppearance

-- Comparisons of values that must be lower case
private function isExactly pValue, pExpected
   set the caseSensitive to true
   return pValue is pExpected
end isExactly

-- What the effective appearance of a stack set to pMode is: pMode, except on
-- Linux, where it is the GTK theme's
private function expectedEffective pMode, pLinux, pSystem
   if pLinux then return pSystem
   return pMode
end expectedEffective

-- The colours of the paint tools, which no appearance may change
private function paintDefaults
   return the penColor & "|" & the brushColor & "|" & the penPattern & "|" & the brushPattern
end paintDefaults

-- The WCAG relative luminance of "r,g,b" (0 to 1); 1 for anything else, so
-- that a colour name fails a check for a dark colour
private function relativeLuminance pColor
   local tSum, tChannel, tWeights
   if the number of items of pColor is not 3 then return 1
   put "0.2126,0.7152,0.0722" into tWeights
   put 0 into tSum
   repeat with i = 1 to 3
      if item i of pColor is not an integer then return 1
      put (item i of pColor) / 255 into tChannel
      if tChannel <= 0.04045 then
         put tChannel / 12.92 into tChannel
      else
         put ((tChannel + 0.055) / 1.055) ^ 2.4 into tChannel
      end if
      add (item i of tWeights) * tChannel to tSum
   end repeat
   return tSum
end relativeLuminance

-- Loads and calls every extension in the list pProbeFile (tab-separated
-- lines written by smoke-test.ps1 from tools/oxt/xtalk-extensions.json):
--    lcb <folder> <module id> <probe> <expected (regex)> <missing DLLs>
--    lcs <folder> <stack> <probe> <expected (regex)> <requires>
--    missing <folder>
private command checkXtalkExtensions pProbeFile, pExtensionsDir
   local tList, tFields
   replace "\" with "/" in pProbeFile
   replace "\" with "/" in pExtensionsDir
   put textDecode(URL ("binfile:" & pProbeFile), "UTF-8") into tList
   replace numToCodepoint(13) with empty in tList
   write "INFO xtalk extensions=" & pExtensionsDir & return to stdout
   repeat for each line tLine in tList
      if tLine is empty then next repeat
      put tLine into tFields
      split tFields by tab
      switch tFields[1]
         case "lcb"
            checkXtalkLcb pExtensionsDir & "/" & tFields[2], tFields[2], tFields[3], tFields[4], tFields[5], tFields[6]
            break
         case "lcs"
            checkXtalkLcs pExtensionsDir & "/" & tFields[2], tFields[2], tFields[3], tFields[4], tFields[5]
            break
         default
            check "xtalk " & tFields[2] & " present", false, "listed in tools/oxt/xtalk-extensions.json but not in " & pExtensionsDir
      end switch
   end repeat
end checkXtalkExtensions

-- An LCB library, loaded as the IDE loads it (revideextensionlibrary
-- __LoadExtension): first every file of the code folders that match
-- sCodeFolders (code/<processor>-win32 on Windows) is mapped in
-- revLibraryMapping under its name without the extension
-- (__MapCodeLibraryForIDE), because the engine resolves a foreign handler's
-- "c:<token>>" library through that mapping; then module.lcm is loaded.
private command checkXtalkLcb pDir, pFolder, pId, pProbe, pExpect, pMissingRuntime
   local tCode, tLibrary, tMapped, tResult, tFolders
   try
      put 0 into tMapped
      put codeFolders(pDir & "/code") into tFolders
      repeat for each line tFolder in tFolders
         put pDir & "/code/" & tFolder into tCode
         repeat for each line tFile in files(tCode)
            if tFile begins with "." then next repeat
            set the itemDelimiter to "."
            put item 1 to -2 of tFile into tLibrary
            set the itemDelimiter to comma
            if tLibrary is empty then next repeat
            set the revLibraryMapping[tLibrary] to tCode & "/" & tLibrary
            add 1 to tMapped
         end repeat
      end repeat
      load extension from file (pDir & "/module.lcm") with resource path pDir
      put the result into tResult
      check "xtalk " & pFolder & " loads", tResult is empty and pId is among the lines of the loadedExtensions, \
            "result '" & tResult & "', " & tMapped & " code libraries mapped"
      if tResult is empty then
         checkXtalkProbe pFolder, pProbe, pExpect
      end if
   catch tError
      check "xtalk " & pFolder & " loads", false, oneLine(tError)
   end try
   -- This machine has the Visual C++ runtime (or, on Linux and macOS,
   -- whatever the build machine installed), so the library loaded
   -- whatever is next to it; on a machine without it it would not.
   if pMissingRuntime is empty then
      if the platform is "Win32" then
         check "xtalk " & pFolder & " DLL imports", true, "every import is a Windows system DLL or next to the library"
      else
         check "xtalk " & pFolder & " " & sSystemName, true, "every library it needs is a system library or next to it"
      end if
   else
      check "xtalk " & pFolder & " " & sSystemName, false, pMissingRuntime & " (not bundled; cannot load on a machine without it)"
   end if
end checkXtalkLcb

-- The folders of pCode (an extension's code folder) whose libraries the
-- IDE maps on this platform: those matching the patterns of sCodeFolders,
-- in their order, each once
private function codeFolders pCode
   local tAll, tMatched, tResult
   if there is not a folder pCode then return empty
   put folders(pCode) into tAll
   set the itemDelimiter to comma
   repeat for each item tPattern in sCodeFolders
      filter lines of tAll with tPattern into tMatched
      repeat for each line tFolder in tMatched
         if tFolder is not empty and tFolder is not among the lines of tResult then
            put tFolder & return after tResult
         end if
      end repeat
   end repeat
   if tResult is not empty then delete the last char of tResult
   return tResult
end codeFolders

-- A script library: started with "start using", as the members' docs tell
-- users, then its probe; then the extensionInitialize and extensionFinalize
-- handlers that the IDE sends must put it into the back scripts and take
-- it out again.
private command checkXtalkLcs pDir, pFolder, pStack, pProbe, pExpect
   local tName, tInBack, tOutOfBack
   try
      put the short name of stack (pDir & "/" & pStack & ".livecodescript") into tName
      start using stack tName
      check "xtalk " & pFolder & " start using", tName is pStack and tName is among the lines of the stacksInUse, \
            "stack " & quote & tName & quote
      checkXtalkProbe pFolder, pProbe, pExpect
      send "extensionInitialize" to stack tName
      put the long id of stack tName is among the lines of the backScripts into tInBack
      send "extensionFinalize" to stack tName
      put the long id of stack tName is not among the lines of the backScripts into tOutOfBack
      check "xtalk " & pFolder & " extensionInitialize/extensionFinalize", tInBack and tOutOfBack, \
            "in the back scripts after extensionInitialize: " & tInBack & ", out after extensionFinalize: " & tOutOfBack
   catch tError
      check "xtalk " & pFolder & " start using", false, oneLine(tError)
   end try
end checkXtalkLcs

private command checkXtalkProbe pFolder, pProbe, pExpect
   local tValue
   try
      put value(pProbe) into tValue
      check "xtalk " & pFolder & " " & pProbe, matchText(tValue, pExpect), tValue
   catch tError
      check "xtalk " & pFolder & " " & pProbe, false, "script error: " & oneLine(tError)
   end try
end checkXtalkProbe
