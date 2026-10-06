script "OXTMediaCheck"
/*
Copyright (C) 2026 OXT-Beyond contributors.

This file is part of OXT-Beyond.

OXT-Beyond is free software; you can redistribute it and/or modify it under
the terms of the GNU General Public License v3 as published by the Free
Software Foundation.

OXT-Beyond is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
FOR A PARTICULAR PURPOSE.  See the GNU General Public License for more
details.

You should have received a copy of the GNU General Public License
along with OXT-Beyond.  If not see <http://www.gnu.org/licenses/>.  */

-- Checks that the browser widget, revBrowser and the player work in a
-- standalone engine with a user interface: a page loads, its JavaScript
-- runs and calls back into the script, and media files play, pause and
-- reach their end. tools/ci/media_check.py copies a runtime of an
-- installed layout to a folder of its own (as a standalone has it), serves
-- the test page, writes the media files and runs
--
--    <standalone engine> tools/ci/media-check.livecodescript
--
-- (on Linux under Xvfb). Environment:
--    OXT_MEDIA_LOG        the file to append the result lines to (PASS,
--                         FAIL, INFO, DONE); a standalone with a user
--                         interface has no reliable stdout on Windows
--    OXT_MEDIA_WHAT       comma-separated: widget, revbrowser, player
--    OXT_MEDIA_URL        the test page (tools/ci/media_check.py PAGE)
--    OXT_MEDIA_FILES      comma-separated media files to play
--    OXT_MEDIA_EXTENSIONS the Extensions folder with the browser widget
--                         and com.livecode.library.widgetutils
--    OXT_MEDIA_REVBROWSER the revbrowser external (.so, .dll or .bundle)
--    OXT_MEDIA_SNAPSHOTS  a folder for the snapshots of the player's
--                         controller (Linux), if set
--
-- Quits with the number of failed checks.

local sFails, sEvents

command logLine pText
   put pText & return after URL ("binfile:" & $OXT_MEDIA_LOG)
end logLine

command check pName, pOK, pDetail
   replace return with " | " in pDetail
   if pOK then
      logLine "PASS " & pName & ": " & pDetail
   else
      add 1 to sFails
      logLine "FAIL " & pName & ": " & pDetail
   end if
end check

-- Called by the test stack's script with what happened
command mediaCheckEvent pEvent
   put pEvent & return after sEvents
end mediaCheckEvent

function waitFor pEvent, pSeconds
   put the milliseconds + pSeconds * 1000 into tEnd
   repeat while the milliseconds < tEnd
      if sEvents contains pEvent then return true
      wait 100 milliseconds with messages
   end repeat
   return sEvents contains pEvent
end waitFor

-- The handlers of the test stack, which report every message to this one
private function stackScript
   local tScript
   repeat for each item tMessage in "browserDocumentLoadComplete,browserDocumentLoadFailed,browserDocumentComplete,browserDocumentFailed,oxtReport,playStopped,currentTimeChanged"
      put "on" && tMessage && "p1, p2" & return after tScript
      put "   dispatch" && quote & "mediaCheckEvent" & quote && "to stack" && quote & "OXTMediaCheck" & quote && "with" && quote & tMessage & ":" & quote && "&& p1 && p2" & return after tScript
      put "end" && tMessage & return after tScript
   end repeat
   return tScript
end stackScript

on startup
   put 0 into sFails
   put empty into sEvents
   put $OXT_MEDIA_WHAT into tWhat
   if tWhat is empty then put "widget,revbrowser,player" into tWhat
   logLine "INFO platform=" & the platform & " version=" & the version & " processor=" & the processor & " environment=" & the environment
   try
      create stack "MediaCheck"
      set the script of stack "MediaCheck" to stackScript()
      set the rect of stack "MediaCheck" to 100,100,740,580
      go stack "MediaCheck"
      wait 500 milliseconds with messages
      if "widget" is among the items of tWhat then checkWidget $OXT_MEDIA_URL
      if "revbrowser" is among the items of tWhat then checkRevBrowser $OXT_MEDIA_URL
      if "player" is among the items of tWhat then
         repeat for each item tFile in $OXT_MEDIA_FILES
            checkPlayer tFile
         end repeat
         -- On Linux the engine draws the controller (mplayer only draws the
         -- video): it is checked with the first AVI file, whose frames are
         -- all key frames (mplayer seeks an MP4 file to its key frame
         -- before the time, and the MP4 files have one)
         if the platform is "Linux" then
            repeat for each item tFile in $OXT_MEDIA_FILES
               if tFile ends with ".avi" then
                  checkController tFile
                  exit repeat
               end if
            end repeat
         end if
      end if
   catch tError
      check "no script error", false, tError
   end try
   logLine "DONE " & sFails
   quit sFails
end startup

command checkWidget pUrl
   put empty into sEvents
   put $OXT_MEDIA_EXTENSIONS into tExtensions
   load extension from file (tExtensions & "/com.livecode.library.widgetutils/module.lcm")
   load extension from file (tExtensions & "/com.livecode.widget.browser/module.lcm")
   put the result into tResult
   check "browser widget: extension loads", tResult is empty, tResult
   if tResult is not empty then exit checkWidget
   create widget "Browser" as "com.livecode.widget.browser" in card 1 of stack "MediaCheck"
   set the rect of widget "Browser" of stack "MediaCheck" to 0,0,640,400
   set the javascriptHandlers of widget "Browser" of stack "MediaCheck" to "oxtReport"
   set the url of widget "Browser" of stack "MediaCheck" to pUrl
   check "browser widget: the page loads", waitFor("browserDocumentLoadComplete:", 30), sEvents
   check "browser widget: its JavaScript calls a handler of the script", waitFor("oxtReport: loaded", 15), sEvents
   put the htmlText of widget "Browser" of stack "MediaCheck" into tHtml
   check "browser widget: htmlText is the page after its JavaScript ran", tHtml contains "script ran: 3", char 1 to 120 of tHtml
   delete widget "Browser" of stack "MediaCheck"
end checkWidget

command checkRevBrowser pUrl
   put empty into sEvents
   -- A stack whose externals property lists revbrowser, as a standalone's
   -- mainstack does
   put specialFolderPath("temporary") & "/oxt-media-check-externals.livecode" into tFile
   create stack "RevBrowserHost"
   set the externals of stack "RevBrowserHost" to $OXT_MEDIA_REVBROWSER
   save stack "RevBrowserHost" as tFile
   delete stack "RevBrowserHost"
   start using stack tFile
   check "revBrowser: the external loads", "revBrowser" is among the lines of the externalPackages of stack "RevBrowserHost", the externalPackages of stack "RevBrowserHost"
   try
      if the platform is "MacOS" then
         put revBrowserOpen(the windowID of stack "MediaCheck", pUrl) into tId
      else
         put revBrowserOpenCef(the windowID of stack "MediaCheck", pUrl) into tId
      end if
   catch tError
      put tError into tId
   end try
   check "revBrowser: a browser opens", tId is an integer, tId
   if tId is not an integer then exit checkRevBrowser
   revBrowserSet tId, "rect", "0,0,640,400"
   check "revBrowser: the page loads", waitFor("browserDocumentComplete:", 30), sEvents
   -- (htmltext is the page's source on macOS, the document after its
   -- JavaScript ran with CEF)
   put revBrowserGet(tId, "htmltext") into tHtml
   check "revBrowser: htmltext is the page", tHtml contains "<title>OXT media check</title>", char 1 to 120 of tHtml
   put revBrowserExecuteScript(tId, "result = document.getElementById('h').textContent;") into tText
   check "revBrowser: the page's JavaScript ran (revBrowserExecuteScript)", tText is "script ran: 3", tText
   revBrowserClose tId
end checkRevBrowser

command checkPlayer pFile
   put empty into sEvents
   set the itemdelimiter to "/"
   put "player " & item -1 of pFile into tName
   set the itemdelimiter to comma
   create player "Player" in card 1 of stack "MediaCheck"
   set the rect of player "Player" of stack "MediaCheck" to 0,0,320,260
   set the filename of player "Player" of stack "MediaCheck" to pFile
   if the result is not empty then logLine "INFO " & tName & ": setting the filename: " & the result
   start player "Player" of stack "MediaCheck"
   -- until it has played for a while (a player may take a moment to start)
   put the milliseconds + 4000 into tEnd
   repeat while the milliseconds < tEnd
      wait 250 milliseconds with messages
      if the currentTime of player "Player" of stack "MediaCheck" > 0 then exit repeat
   end repeat
   wait 500 milliseconds with messages
   put the duration of player "Player" of stack "MediaCheck" into tDuration
   put the timeScale of player "Player" of stack "MediaCheck" into tScale
   check tName & ": duration and timeScale", tDuration > 0 and tScale > 0, "duration=" & tDuration && "timeScale=" & tScale
   put the currentTime of player "Player" of stack "MediaCheck" into tTime
   check tName & ": it plays (currentTime moves on)", tTime > 0 and tTime < tDuration and not the paused of player "Player" of stack "MediaCheck", "currentTime=" & tTime && "paused=" & the paused of player "Player" of stack "MediaCheck"
   -- Paused, the currentTime must stop: at once with a sound device, or
   -- once the sound already sent has drained without one (mplayer with
   -- ao=null keeps counting that for a moment), and then stay put
   set the paused of player "Player" of stack "MediaCheck" to true
   put the currentTime of player "Player" of stack "MediaCheck" into tTime
   put the milliseconds + 2000 into tEnd
   repeat while the milliseconds < tEnd
      wait 200 milliseconds with messages
      put the currentTime of player "Player" of stack "MediaCheck" into tLater
      if tLater = tTime then exit repeat
      put tLater into tTime
   end repeat
   wait 600 milliseconds with messages
   put the currentTime of player "Player" of stack "MediaCheck" into tLater
   check tName & ": it pauses", the paused of player "Player" of stack "MediaCheck" and tScale > 0 and abs(tLater - tTime) / tScale < 0.05, "currentTime " & tTime & " then " & tLater && "paused=" & the paused of player "Player" of stack "MediaCheck"
   put empty into sEvents
   start player "Player" of stack "MediaCheck"
   if tScale > 0 then put tDuration / tScale + 10 into tWait
   else put 15 into tWait
   check tName & ": playStopped at the end", waitFor("playStopped:", tWait), sEvents
   delete player "Player" of stack "MediaCheck"
end checkPlayer

-- The controller of the Linux player (engine/src/player-legacy.cpp): its
-- play button plays and pauses the movie and shows which it does, a click
-- in its well seeks there (with currentTimeChanged) and moves the thumb.
-- The controller is the bottom 26 pixels of the player: the play button
-- the 26 on the left, the well from there to 8 pixels from the right.
command checkController pFile
   put empty into sEvents
   set the itemdelimiter to "/"
   put "player controller (" & item -1 of pFile & ")" into tName
   set the itemdelimiter to comma
   set the defaultStack to "MediaCheck"
   create player "Player" in card 1 of stack "MediaCheck"
   set the showBorder of player "Player" of stack "MediaCheck" to false
   set the rect of player "Player" of stack "MediaCheck" to 0,0,320,260
   set the showController of player "Player" of stack "MediaCheck" to true
   -- (the part played is drawn in the hiliteColor)
   set the hiliteColor of player "Player" of stack "MediaCheck" to 255,0,0
   set the filename of player "Player" of stack "MediaCheck" to pFile
   wait 500 milliseconds with messages
   put 247 into tY
   check tName & ": it is drawn, paused (the play button)", controllerPixels(tY, "2,13") is "dark,light", controllerPixels(tY, "2,13")
   controllerSnapshot "paused"

   click at 13, tY
   put the milliseconds + 4000 into tEnd
   repeat while the milliseconds < tEnd
      wait 250 milliseconds with messages
      if the currentTime of player "Player" of stack "MediaCheck" > 0 then exit repeat
   end repeat
   wait 300 milliseconds with messages
   put the currentTime of player "Player" of stack "MediaCheck" into tTime
   check tName & ": its play button plays the movie", tTime > 0 and not the paused of player "Player" of stack "MediaCheck", "currentTime=" & tTime && "paused=" & the paused of player "Player" of stack "MediaCheck"
   check tName & ": it shows the pause button while the movie plays", item 2 of controllerPixels(tY, "2,13") is "dark", controllerPixels(tY, "2,13")
   controllerSnapshot "playing"

   click at 13, tY
   wait 300 milliseconds with messages
   check tName & ": its pause button pauses the movie", the paused of player "Player" of stack "MediaCheck", "paused=" & the paused of player "Player" of stack "MediaCheck"
   check tName & ": it shows the play button while paused", item 2 of controllerPixels(tY, "2,13") is "light", controllerPixels(tY, "2,13")

   -- The middle of the well (26 to 312, its thumb 10 wide) is half the
   -- movie
   put empty into sEvents
   click at 169, tY
   wait 500 milliseconds with messages
   put the duration of player "Player" of stack "MediaCheck" into tDuration
   put the timeScale of player "Player" of stack "MediaCheck" into tScale
   put the currentTime of player "Player" of stack "MediaCheck" into tTime
   check tName & ": a click in its well seeks there", tDuration > 0 and tScale > 0 and abs(tTime - tDuration / 2) / tScale < 0.3, "currentTime=" & tTime && "duration=" & tDuration && "timeScale=" & tScale
   check tName & ": currentTimeChanged after the seek", waitFor("currentTimeChanged:", 2), sEvents
   check tName & ": the thumb is at the middle of the well", controllerPixels(tY, "100,169,250") is "played,light,dark", controllerPixels(tY, "100,169,250")
   controllerSnapshot "seeked"

   put empty into sEvents
   click at 13, tY
   if tScale > 0 then put tDuration / tScale + 10 into tWait
   else put 15 into tWait
   check tName & ": playStopped at the end", waitFor("playStopped:", tWait), sEvents
   wait 300 milliseconds with messages
   check tName & ": at the end, the play button and the thumb at the end", controllerPixels(tY, "13,250,306") is "light,played,light", controllerPixels(tY, "13,250,306")
   controllerSnapshot "ended"
   delete player "Player" of stack "MediaCheck"
end checkController

-- What the controller shows at x, y for each x of pXs (in the snapshot of
-- the player the engine draws): dark (its background or the well), light
-- (an icon or the thumb, white), played (red, the hiliteColor), or the
-- color
private function controllerPixels pY, pXs
   create invisible image "Snapshot" in card 1 of stack "MediaCheck"
   export snapshot from player "Player" of stack "MediaCheck" to image "Snapshot" of stack "MediaCheck" as PNG
   put the width of image "Snapshot" of stack "MediaCheck" into tWidth
   put the imageData of image "Snapshot" of stack "MediaCheck" into tData
   delete image "Snapshot" of stack "MediaCheck"
   repeat for each item tX in pXs
      put (pY * tWidth + tX) * 4 into tOffset
      put byteToNum(byte tOffset + 2 of tData) & comma & byteToNum(byte tOffset + 3 of tData) & comma & byteToNum(byte tOffset + 4 of tData) into tRGB
      if max(item 1 of tRGB, item 2 of tRGB, item 3 of tRGB) < 60 then
         put "dark" into tWhat
      else if min(item 1 of tRGB, item 2 of tRGB, item 3 of tRGB) > 200 then
         put "light" into tWhat
      else if item 1 of tRGB > 200 and max(item 2 of tRGB, item 3 of tRGB) < 60 then
         put "played" into tWhat
      else
         put tRGB into tWhat
      end if
      put tWhat & comma after tResult
   end repeat
   delete the last char of tResult
   return tResult
end controllerPixels

-- The player as the engine draws it and as the screen shows it (with
-- mplayer's video), when OXT_MEDIA_SNAPSHOTS names a folder for them
private command controllerSnapshot pName
   if $OXT_MEDIA_SNAPSHOTS is empty then exit controllerSnapshot
   export snapshot from player "Player" of stack "MediaCheck" to file ($OXT_MEDIA_SNAPSHOTS & "/controller-" & pName & ".png") as PNG
   put globalLoc(the topLeft of player "Player" of stack "MediaCheck") into tTopLeft
   put globalLoc(the bottomRight of player "Player" of stack "MediaCheck") into tBottomRight
   set the JPEGQuality to 60
   export snapshot from rectangle (tTopLeft & comma & tBottomRight) to file ($OXT_MEDIA_SNAPSHOTS & "/screen-" & pName & ".jpg") as JPEG
end controllerSnapshot
