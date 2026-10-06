script "CoreFieldPageHeights"
/*
Copyright (C) 2018 LiveCode Ltd.

This file is part of LiveCode.

LiveCode is free software; you can redistribute it and/or modify it under
the terms of the GNU General Public License v3 as published by the Free
Software Foundation.

LiveCode is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or
FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License
for more details.

You should have received a copy of the GNU General Public License
along with LiveCode.  If not see <http://www.gnu.org/licenses/>.  */

on TestPageHeights
    
   open this stack
   create field "testPageHeightsField" 
   
   // Recreate field similar to the one of the attached stack to bug 21491
   set the textFont of field "testPageHeightsField" to "Segoe UI"
   set the textSize of field "testPageHeightsField" to 13
   set the textAlign of field "testPageHeightsField" to "left"
   set the fixedLineHeight of field "testPageHeightsField" to true
   set the borderwidth of field "testPageHeightsField" to 0
   set the margins of field "testPageHeightsField" to 6
   set the width of field "testPageHeightsField" to 474
   set the height of field "testPageHeightsField" to 692 
   
   local tFieldContentFilename
   set the itemDel to slash
   put item 1 to -2 of the filename of this stack into tFieldContentFilename
   put slash & "_pageHeightsFieldContent.txt" after tFieldContentFilename
   -- (a "file:" URL takes a path; "file:/" & a Windows path, "/C:/...", is
   -- no file)
   set the htmlText of field "testPageHeightsField" to url ("file:" & tFieldContentFilename)
   
   TestAssert "Field is not empty", field "testPageHeightsField" is not empty
      
   local tObservedPageHeights
   put the pageHeights of field "testPageHeightsField" into tObservedPageHeights
      
   TestAssert "pageHeights is not empty", tObservedPageHeights is not empty
   
   -- All of the text is in Arial 12, and the pages are made of its lines:
   -- in a font of other metrics the text wraps elsewhere, and the number of
   -- pages differs (25 on a Linux with only the DejaVu fonts, all that the
   -- Linux CI container has). Windows and macOS have Arial; Linux has it
   -- when it is installed, else fontconfig uses a font with its metrics in
   -- its place, Liberation Sans or Arimo, when one of these is.
   TestDiagnostic "pages:" && the number of lines of tObservedPageHeights
   if the platform is "Linux" and not __HasArialMetrics() then
      TestSkip "The number of lines of the pageheights is 24", "no font with the metrics of Arial (Arial, Liberation Sans, Arimo) is installed"
   else
      TestAssert "The number of lines of the pageheights is 24", the number of lines of tObservedPageHeights is 24
   end if
   delete field 1
    
end TestPageHeights

private function __HasArialMetrics
   repeat for each item tFont in "Arial,Liberation Sans,Arimo"
      if tFont is among the lines of the fontNames then
         return true
      end if
   end repeat
   return false
end __HasArialMetrics
