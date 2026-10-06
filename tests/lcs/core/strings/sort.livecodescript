script "CoreStringSort"
/*
Copyright (C) 2019 LiveCode Ltd.

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


on TestInterface95
   local tContainer
   
   put "b,d,f,g,c,h,e,a" into tContainer
   
   sort items of tContainer
   
   TestAssert "test", tContainer is "a,b,c,d,e,f,g,h" 
end TestInterface95

on TestSortMixedText
   local tVar, tSorted
   put "Chinese 中文" & return & \
         "Georgian  ქართული" & return & \
         "Arabic عربى" & return & \
         "Greek Ελληνικά" & return & \
         "Russian русский" & return & \
         "Norwegian Norsk" into tVar
   sort lines of tVar ascending text
   
   put "Arabic عربى" & return & \
         "Chinese 中文" & return & \
         "Georgian  ქართული" & return & \
         "Greek Ελληνικά" & return & \
         "Norwegian Norsk" & return & \
         "Russian русский"into tSorted
   TestAssert "Test sorting mixed text", tVar is tSorted
end TestSortMixedText

on TestSortTextByCodepoint
   -- Text sorts by codepoint, however the engine holds the strings. Held
   -- as native chars, these sorted by their codes in Windows-1252 (where the
   -- euro sign is 0x80) on Windows and Mac Roman (where "Ä" is) on macOS.
   local tList, tByCodepoint
   repeat for each item tCode in "0x41,0xC4,0xC9,0xE1,0xFF,0x0152,0x0192,0x2019,0x201E,0x20AC,0x2122"
      put numToCodepoint(tCode) & return after tList
   end repeat
   delete the last char of tList

   set the caseSensitive to true
   put tList into tByCodepoint
   sort lines of tList ascending text
   sort lines of tByCodepoint ascending numeric by codepointToNum(each)
   TestAssert "case-sensitive text sort is by codepoint", tList is tByCodepoint

   set the caseSensitive to false
   sort lines of tList ascending text
   sort lines of tByCodepoint ascending numeric by codepointToNum(toLower(each))
   TestAssert "case-insensitive text sort is by codepoint of the folded chars", tList is tByCodepoint
end TestSortTextByCodepoint

on TestCompareTextByCodepoint
   local tEuro, tAAcute, tUnicodeEuro
   put numToCodepoint(0x20AC) into tEuro
   put numToCodepoint(0xE1) into tAAcute
   -- The same euro sign held as UTF-16: a char outside the native encoding
   -- makes the engine hold a string so, and it stays so when that is deleted
   put tEuro & numToCodepoint(0x1F600) into tUnicodeEuro
   delete the last char of tUnicodeEuro

   set the caseSensitive to true
   TestAssert "the euro sign comes after a-acute", tEuro > tAAcute
   TestAssert "a-acute comes before the euro sign", tAAcute < tEuro
   TestAssert "the euro sign held as UTF-16 comes after a-acute", tUnicodeEuro > tAAcute
   set the caseSensitive to false
   TestAssert "the euro sign comes after a-acute, ignoring case", tEuro > tAAcute
   TestAssert "OE ligature comes after y-diaeresis, ignoring case", numToCodepoint(0x0152) > numToCodepoint(0xFF)
end TestCompareTextByCodepoint
