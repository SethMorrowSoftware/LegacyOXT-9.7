script "oxt_ide_compile_check"
-- Headless compile check of the IDE scripts in an installed OpenXTalk-Lite
-- layout (the folder with Toolset, Plugins and Extensions).
--
-- Run it with the development engine, without the IDE:
--
--    OpenXTalk-Lite.exe -ui tools\ci\ide-compile-check.livecodescript
--
-- tools/ci/ide-compile-check.ps1 does this, compares the errors with
-- tools/ci/ide-compile-baseline.txt and decides whether the check passed.
--
-- Environment variables:
--    OXT_CHECK_ROOT  the installed layout to check (required)
--    OXT_CHECK_OUT   file to write the error records to, UTF-8, one per
--                    line (optional)
--    OXT_CHECK_EXCLUDE_FILE
--                    file listing paths (relative to the root, one per
--                    line) not to check (optional): tools/ci/
--                    run_livecode_check.py leaves out a file that made the
--                    engine crash and checks the rest in a second run
--
-- What is compiled:
--    - every script-only stack (*.livecodescript, *.oxtscript) under
--      Toolset, Plugins and Extensions;
--    - every object script of every binary stack (*.livecode, *.rev,
--      *.oxtstack) under Toolset and Plugins: the main stack and its
--      substacks, their cards, and the groups and controls on each card
--      (and groups that are on no card), each object once.
--
-- Stack files are only loaded into memory, with messages locked, and never
-- opened or saved, so none of their handlers run and no dialog can open.
-- Each stack is removed from memory again before the next file is loaded.
-- Each script is compiled by setting it as the script of a button in a
-- scratch stack, with explicitVariables false (as the IDE loads scripts).
-- The compiler stops at the first error in a script, so there is at most
-- one error per script.
--
-- Output on stdout: INFO lines, a FILE line per file, one ERROR line per
-- error and a final SUMMARY line. An error record is
--
--    <file> | <object> | line <n> | <message>
--
-- where <file> is relative to the layout root with "/" separators, <object>
-- is the path of the object inside the file, <n> is the line in the
-- script-only stack file or in the object's script (0 when the file could
-- not be loaded) and <message> is the compiler's description of the error
-- (from the scriptParsingErrors) with the token it was near. The records
-- contain no absolute paths, times or other run-dependent values, so the
-- same files give the same records.
--
-- The exit code is 0 when every file was processed, whether or not errors
-- were found, and 2 when the check could not run.

local sRoot
local sScratch
local sErrors
local sErrorCount
local sScriptCount
local sObjectCount
local sFileCount
local sVisited

on startup
   local tStart, tOut, tScriptOnly, tBinary
   put the milliseconds into tStart
   -- No preOpenStack, openStack, openCard or other message may reach a
   -- stack that is loaded for checking.
   set the lockMessages to true
   set the explicitVariables to false
   put empty into sErrors
   put 0 into sErrorCount
   put 0 into sScriptCount
   put 0 into sObjectCount
   put 0 into sFileCount

   put $OXT_CHECK_ROOT into sRoot
   put $OXT_CHECK_OUT into tOut
   replace "\" with "/" in sRoot
   if the last char of sRoot is "/" then delete the last char of sRoot
   write "INFO version=" & the version & " build=" & the buildNumber & " environment=" & the environment & return to stdout
   if sRoot is empty or there is not a folder sRoot then
      write "FATAL OXT_CHECK_ROOT is not a folder: " & sRoot & return to stdout
      quit 2
   end if
   if there is not a folder (sRoot & "/Toolset") then
      write "FATAL no Toolset folder in " & sRoot & return to stdout
      quit 2
   end if
   write "INFO root=" & sRoot & return to stdout

   try
      create stack "oxt_ide_compile_check_scratch"
      set the defaultStack to "oxt_ide_compile_check_scratch"
      create button "scratch"
      put the long id of button "scratch" of card 1 of stack "oxt_ide_compile_check_scratch" into sScratch

      put empty into tScriptOnly
      put empty into tBinary
      repeat for each item tTop in "Toolset,Plugins,Extensions"
         collectFiles tTop, "livecodescript,oxtscript", tScriptOnly
      end repeat
      repeat for each item tTop in "Toolset,Plugins"
         collectFiles tTop, "livecode,rev,oxtstack", tBinary
      end repeat
      sortPaths tScriptOnly
      sortPaths tBinary
      if $OXT_CHECK_EXCLUDE_FILE is not empty then
         excludePaths $OXT_CHECK_EXCLUDE_FILE, tScriptOnly, tBinary
      end if
      write "INFO script-only stacks=" & the number of lines of tScriptOnly & " binary stacks=" & the number of lines of tBinary & return to stdout

      repeat for each line tRel in tScriptOnly
         checkScriptOnlyStack tRel
      end repeat
      repeat for each line tRel in tBinary
         checkBinaryStack tRel
      end repeat
   catch tError
      write "FATAL script error in the checker: " & oneLine(tError) & return to stdout
      quit 2
   end try

   if tOut is not empty then
      put textEncode(sErrors, "UTF-8") into URL ("binfile:" & tOut)
      if the result is not empty then
         write "FATAL could not write " & tOut & ": " & the result & return to stdout
         quit 2
      end if
   end if
   write "SUMMARY files=" & sFileCount & " objects=" & sObjectCount & " scripts=" & sScriptCount & " errors=" & sErrorCount & " seconds=" & round((the milliseconds - tStart) / 1000, 1) & return to stdout
   quit 0
end startup

-- Appends the paths (relative to sRoot) of the files under pRel whose
-- extension is in pExtensions to xList, one per line
private command collectFiles pRel, pExtensions, @xList
   local tFolder, tExtension
   put sRoot & "/" & pRel into tFolder
   if there is not a folder tFolder then exit collectFiles
   set the itemDelimiter to "."
   repeat for each line tFile in files(tFolder)
      if the number of items of tFile < 2 then next repeat
      put toLower(item -1 of tFile) into tExtension
      set the itemDelimiter to comma
      if tExtension is among the items of pExtensions then
         put pRel & "/" & tFile & return after xList
      end if
      set the itemDelimiter to "."
   end repeat
   set the itemDelimiter to comma
   repeat for each line tSub in folders(tFolder)
      if tSub is "." or tSub is ".." then next repeat
      collectFiles pRel & "/" & tSub, pExtensions, xList
   end repeat
end collectFiles

-- Removes the paths listed in the file pFile (UTF-8, one per line) from
-- both lists
private command excludePaths pFile, @xScriptOnly, @xBinary
   local tList, tCount, tLine
   replace "\" with "/" in pFile
   put textDecode(URL ("binfile:" & pFile), "UTF-8") into tList
   replace numToCodepoint(13) with empty in tList
   put 0 into tCount
   set the wholeMatches to true
   repeat for each line tRel in tList
      if tRel is empty then next repeat
      put lineOffset(tRel, xScriptOnly) into tLine
      if tLine > 0 then
         delete line tLine of xScriptOnly
         add 1 to tCount
         next repeat
      end if
      put lineOffset(tRel, xBinary) into tLine
      if tLine > 0 then
         delete line tLine of xBinary
         add 1 to tCount
      end if
   end repeat
   set the wholeMatches to false
   write "INFO excluded=" & tCount & return to stdout
end excludePaths

-- Sorts a path list the same way on every run and machine
private command sortPaths @xList
   if the last char of xList is return then delete the last char of xList
   set the caseSensitive to true
   sort lines of xList ascending text
   set the caseSensitive to false
end sortPaths

private function oneLine pText
   replace crlf with " " in pText
   replace return with " " in pText
   replace numToCodepoint(13) with " " in pText
   replace tab with " " in pText
   return pText
end oneLine

-- A caught error as a stable message. An engine error is a list of
-- <code>,<line>,<column>,<token> lines whose line numbers point into this
-- checker and whose tokens can hold absolute paths, so only the
-- description of the first code is kept. Errors thrown by this script are
-- returned as they are.
private function describeError pError
   local tCode, tMessage
   put item 1 of line 1 of pError into tCode
   if tCode is not an integer then return oneLine(pError)
   if tCode > 0 then put line tCode of the scriptExecutionErrors into tMessage
   if tMessage is empty then put "execution error " & tCode into tMessage
   return tMessage
end describeError

private command addError pRel, pObject, pLine, pMessage
   local tRecord
   put pRel & " | " & oneLine(pObject) & " | line " & pLine & " | " & oneLine(pMessage) into tRecord
   put tRecord & return after sErrors
   add 1 to sErrorCount
   write "ERROR " & tRecord & return to stdout
end addError

-- Loads a stack file into memory without opening it and returns the short
-- name of its main stack. Throws if the file cannot be loaded or if a stack
-- of the same name from another file is in memory.
private function loadStack pRel
   local tPath, tName, tLoadedFile
   put sRoot & "/" & pRel into tPath
   put the short name of stack tPath into tName
   put the effective filename of stack tName into tLoadedFile
   replace "\" with "/" in tLoadedFile
   if tLoadedFile is not tPath then
      throw "a stack named " & quote & tName & quote & " from another file is already in memory"
   end if
   return tName
end loadStack

-- Removes a main stack (and its substacks) from memory. The files are not
-- touched.
private command unloadStack pName
   try
      delete stack pName
   catch tError
      -- A stack with cantDelete set cannot be removed. Clear the flag in
      -- memory only (the stack is never saved) and try again.
      try
         set the cantDelete of stack pName to false
         repeat for each line tSub in the substacks of stack pName
            set the cantDelete of stack tSub of stack pName to false
         end repeat
         delete stack pName
      catch tError
         write "WARNING could not remove stack " & quote & pName & quote & " from memory: " & oneLine(tError) & return to stdout
      end try
   end try
end unloadStack

-- Compiles pScript in the scratch button and records the first error.
-- pLineOffset is added to the error's line number.
private command compileScript pScript, pRel, pObject, pLineOffset
   local tResult, tCode, tLine, tHint, tMessage
   if pScript is empty then exit compileScript
   add 1 to sScriptCount
   set the script of sScratch to pScript
   put the result into tResult
   set the script of sScratch to empty
   if tResult is empty then exit compileScript

   -- The result lists the error and then its enclosing constructs, one per
   -- line, as <code>,<line>,<column>,<token>; the first line is the error.
   put item 1 of line 1 of tResult into tCode
   put item 2 of line 1 of tResult into tLine
   put item 4 to -1 of line 1 of tResult into tHint
   if tCode is an integer and tCode > 0 then
      put line tCode of the scriptParsingErrors into tMessage
   end if
   if tMessage is empty then put "compile error " & tCode into tMessage
   if tHint is not empty then
      put " (near " & quote & tHint & quote & ")" after tMessage
   end if
   if tLine is an integer then
      add pLineOffset to tLine
   else
      put 0 into tLine
   end if
   addError pRel, pObject, tLine, tMessage
end compileScript

-- Number of lines before the body of a script-only stack (the header,
-- normally the single line 'script "<name>"'), so that errors can be
-- reported with the line number in the file
private function headerLineCount pRel, pBody
   local tData, tText
   put URL ("binfile:" & sRoot & "/" & pRel) into tData
   -- The engine reads a script-only stack as UTF-8 or UTF-16 when it starts
   -- with a byte order mark and as native text otherwise.
   if byte 1 to 3 of tData is (numToByte(239) & numToByte(187) & numToByte(191)) then
      put textDecode(byte 4 to -1 of tData, "UTF-8") into tText
   else if byte 1 to 2 of tData is (numToByte(255) & numToByte(254)) then
      put textDecode(byte 3 to -1 of tData, "UTF-16LE") into tText
   else if byte 1 to 2 of tData is (numToByte(254) & numToByte(255)) then
      put textDecode(byte 3 to -1 of tData, "UTF-16BE") into tText
   else
      put textDecode(tData, "native") into tText
   end if
   replace crlf with return in tText
   replace numToCodepoint(13) with return in tText
   if the number of lines of tText >= the number of lines of pBody then
      return the number of lines of tText - the number of lines of pBody
   end if
   return 1
end headerLineCount

private command checkScriptOnlyStack pRel
   local tName, tBody
   add 1 to sFileCount
   write "FILE " & pRel & return to stdout
   try
      put loadStack(pRel) into tName
   catch tError
      addError pRel, "(file)", 0, "not loaded as a stack: " & describeError(tError)
      exit checkScriptOnlyStack
   end try
   try
      add 1 to sObjectCount
      put the script of stack tName into tBody
      compileScript tBody, pRel, "stack" && quote & tName & quote, headerLineCount(pRel, tBody)
   catch tError
      addError pRel, "stack" && quote & tName & quote, 0, "script not readable: " & describeError(tError)
   end try
   unloadStack tName
end checkScriptOnlyStack

private command checkBinaryStack pRel
   local tName
   add 1 to sFileCount
   write "FILE " & pRel & return to stdout
   try
      put loadStack(pRel) into tName
   catch tError
      addError pRel, "(file)", 0, "not loaded as a stack: " & describeError(tError)
      exit checkBinaryStack
   end try
   try
      walkStack tName, empty, pRel, "stack" && quote & tName & quote
      repeat for each line tSub in the substacks of stack tName
         walkStack tName, tSub, pRel, "stack" && quote & tName & quote && ">" && "substack" && quote & tSub & quote
      end repeat
   catch tError
      addError pRel, "stack" && quote & tName & quote, 0, "objects not readable: " & describeError(tError)
   end try
   unloadStack tName
end checkBinaryStack

-- "button "OK" (id 1005)", or "button (id 1005)" for an unnamed object
private function objectLabel pObject
   local tType, tID, tShortName
   put word 1 of the name of pObject into tType
   put the id of pObject into tID
   put the short name of pObject into tShortName
   if tShortName is (tType && "id" && tID) then
      return tType && "(id" && tID & ")"
   end if
   return tType && quote & tShortName & quote && "(id" && tID & ")"
end objectLabel

private command checkObject pObject, pRel, pLabel
   add 1 to sObjectCount
   try
      compileScript the script of pObject, pRel, pLabel, 0
   catch tError
      addError pRel, pLabel, 0, "script not readable: " & describeError(tError)
   end try
end checkObject

-- Checks a stack (main stack pMain, or its substack pSub), its cards and
-- every control once, in card order and then layer order. Groups placed on
-- several cards are checked once, at their first card; groups on no card
-- are checked last.
private command walkStack pMain, pSub, pRel, pLabel
   local tCardCount, tGroupCount, tCard, tCardLabel, tGroup
   put empty into sVisited
   if pSub is empty then
      checkObject the long id of stack pMain, pRel, pLabel
      put the number of cards of stack pMain into tCardCount
      put the number of backgrounds of stack pMain into tGroupCount
   else
      checkObject the long id of stack pSub of stack pMain, pRel, pLabel
      put the number of cards of stack pSub of stack pMain into tCardCount
      put the number of backgrounds of stack pSub of stack pMain into tGroupCount
   end if
   repeat with tCardNumber = 1 to tCardCount
      if pSub is empty then
         put the long id of card tCardNumber of stack pMain into tCard
      else
         put the long id of card tCardNumber of stack pSub of stack pMain into tCard
      end if
      put pLabel && ">" && objectLabel(tCard) into tCardLabel
      checkObject tCard, pRel, tCardLabel
      walkControls tCard, pRel, tCardLabel
   end repeat
   repeat with tGroupNumber = 1 to tGroupCount
      if pSub is empty then
         put the long id of background tGroupNumber of stack pMain into tGroup
      else
         put the long id of background tGroupNumber of stack pSub of stack pMain into tGroup
      end if
      if sVisited[the id of tGroup] then next repeat
      walkControl tGroup, pRel, pLabel && ">" && "(on no card)"
   end repeat
end walkStack

-- Checks the child controls of a card or group
private command walkControls pOwner, pRel, pLabel
   repeat for each line tID in the childControlIDs of pOwner
      walkControl "control id" && tID && "of" && pOwner, pRel, pLabel
   end repeat
end walkControls

private command walkControl pControl, pRel, pLabel
   local tControl, tLabel
   put the long id of pControl into tControl
   if sVisited[the id of tControl] then exit walkControl
   put true into sVisited[the id of tControl]
   put pLabel && ">" && objectLabel(tControl) into tLabel
   checkObject tControl, pRel, tLabel
   -- The long id of a group can start with "bkgnd"; its name always
   -- starts with "group".
   if word 1 of the name of tControl is "group" then
      walkControls tControl, pRel, tLabel
   end if
end walkControl
