script "DateTime"
/*
Copyright (C) 2017 LiveCode Ltd.

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


on TestLongTime24Hours
 local tDateItems
 set the twelveHourTime to false
 put "2017,3,28,2,0,0,3" into tDateItems
 convert tDateItems to long time

 TestAssert "Test 24-hour format 1", char 1 to 2 of tDateItems is a number
 TestAssert "Test 24-hour format 2", char 3 of tDateItems is ":"
 TestAssert "Test 24-hour format 3", tDateItems is "02:00:00"
end TestLongTime24Hours

on Test2038Problem
   local tDate
   put 2147483648 into tDate
   convert tDate to dateItems
   if the platform is "HTML5" or (the platform is "Linux" and the processor is "x86") then
      TestAssertBroken "Convert dates over long max seconds", item 1 of tDate is 2038, "Bug 19801"
   else
      TestAssert "Convert dates over long max seconds", item 1 of tDate is 2038
   end if
end Test2038Problem

on TestConvertDatesBefore1970
   -- No date before 1970 converted on Windows (its C runtime's time
   -- functions start then): the value stayed as it was.
   local tDate
   put "1/1/1960" into tDate
   convert tDate to dateItems
   TestAssert "convert 1/1/1960 to dateItems", tDate is "1960,1,1,0,0,0,6"

   put "12/31/1969 11:59:59 PM" into tDate
   convert tDate to dateItems
   TestAssert "convert 12/31/1969 11:59:59 PM to dateItems", tDate is "1969,12,31,23,59,59,4"

   put "7/4/1776" into tDate
   convert tDate to long date
   TestAssert "convert 7/4/1776 to a long date", tDate is "Thursday, July 4, 1776"

   put "2/29/1904" into tDate
   TestAssert "the leap day 2/29/1904 is a date", tDate is a date
end TestConvertDatesBefore1970
