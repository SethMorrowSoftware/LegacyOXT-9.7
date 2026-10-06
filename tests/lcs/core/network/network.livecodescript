script "CoreNetwork"
/*
Copyright (C) 2015 LiveCode Ltd.

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

on TestSetup
   if the platform is "HTML5" then
      return "SKIP DNS and socket APIs unavailable on HTML5"
   end if
end TestSetup

on TestNetworkDNSServers
   TestAssert "dnsservers", the dnsservers is not empty

   repeat for each line tServer in the dnsservers
   	set the itemdelimiter to "."
      local tIPv4Items
      put the number of items in tServer into tIPv4Items
      set the itemdelimiter to ":"
      local tIPv6Items
      put the number of items in tServer into tIPv6Items
   	TestAssert "test", tIPv4Items is 4 or tIPv6Items is 8
      
      if tIPv4Items is 4 then
         set the itemdelimiter to "."
      end if
      
   	repeat for each item tComponent in tServer
         if tIPv4Items is 4 then
      		TestAssert "test", tComponent is an integer
      		TestAssert "test", tComponent >= 0 and tComponent <= 255
         else
            TestAssert "test", tComponent is empty or \
               baseConvert(tComponent, 16, 10) <= 0xffff
         end if
   	end repeat
   end repeat

end TestNetworkDNSServers

on TestNetwork4

TestAssert "hostnametoaddress", hostnametoaddress("google-public-dns-a.google.com") is "8.8.8.8"

end TestNetwork4
on TestNetwork13

put "www.livecode.com:80|" & uuid() into tSock
open socket tSock
TestAssert "test", tSock is among the lines of the openSockets
close socket tSock

end TestNetwork13

on TestPeerAddress
   put "www.livecode.com:80|" & uuid() into tSock
open socket tSock 

local tTimeOut, tTime
put 100 into tTimeout
put the millisecs into tTime
repeat while the millisecs - tTime < tTimeOut
	get the peeraddress of tSock
	if it is not empty then
		TestAssert "peeraddress", it is among the lines of hostnametoaddress(tSock)
		close socket tSock
		exit TestPeerAddress
	end if
end repeat

TestSkip "peeraddress", "connection timed out"
close socket tSock
end TestPeerAddress

on TestOpenSocketFrom
   put "livecode.com|" & uuid() into tSock

   open socket from ":8000" to tSock
   TestAssert "open socket from", tSock is among the lines of the openSockets
   close socket tSock
end TestOpenSocketFrom

-- The path of lsof, or empty when it is not installed: Windows has no
-- lsof, and not every Linux has it (the Linux CI container has none)
private function __LsofPath
   if the platform is not "MacOS" and the platform is not "Linux" then
      return empty
   end if
   local tPath
   repeat for each item tPath in "/usr/sbin/lsof,/usr/bin/lsof"
      if there is a file tPath then
         return tPath
      end if
   end repeat
   put line 1 of shell("command -v lsof") into tPath
   if tPath is not empty and there is a file tPath then
      return tPath
   end if
   return empty
end __LsofPath

on TestOpenSocketFromWithLSOF
   local tLsof
   put __LsofPath() into tLsof
   if tLsof is empty then
      TestSkip "open socket from testing with lsof", "lsof is not installed"
      exit TestOpenSocketFromWithLSOF
   end if

   -- a connection to a server of this process, which needs no outside host
   local tPort, tSock
   accept connections on port "0" with message "connectedPlaceHolder"
   put it into tPort
   put "127.0.0.1:" & tPort into tSock
   open socket from ":8001" to tSock
   TestAssert "open socket result is empty: " & the result, the result is empty

   -- The TCP sockets of this process on port 8001, with addresses and ports
   -- as numbers (-n -P): the one opened from local port 8001 has it before
   -- the "->" (the server's end of the connection has it after)
   local tSockets
   put shell(tLsof && "-nP -a -p" && the processID && "-iTCP:8001") into tSockets
   TestDiagnostic tSockets
   TestAssert "open socket from testing with lsof", tSockets contains ":8001->"
   close socket tSock
   close socket tPort
end TestOpenSocketFromWithLSOF

on TestOpenSocketFromMultipleHostsSamePort
   put "livecode.com|" & uuid() into tSock1
   put "google.com|" & uuid() into tSock2
   open socket from ":8002" to tSock1
   TestAssert "open socket result is empty: " & the result, the result is empty
   TestAssert "open socket from with multiple hosts on same local port", tSock1 is among the lines of the openSockets
   open socket from ":8002" to tSock2
   TestAssert "open socket result is empty: " & the result, the result is empty
   -- the second open looks up its host in a wait that also handles the
   -- first socket, and ends it if its connection failed meanwhile, which
   -- frees the local port: then the two sockets never shared it
   if tSock1 is among the lines of the openSockets then
      TestAssert "open socket from with multiple hosts on same local port", tSock2 is among the lines of the openSockets
   else
      TestSkip "open socket from with multiple hosts on same local port", "the first connection failed before the second socket was opened"
   end if
   close socket tSock1
   close socket tSock2
end TestOpenSocketFromMultipleHostsSamePort

on TestOpenSocketFromSameHostDifferentPort
   put "livecode.com|" & uuid() into tSock1
   put "livecode.com|" & uuid() into tSock2
   open socket from ":8003" to tSock1
   open socket from ":8004" to tSock2
   TestAssert "open socket from with same host on different local port", tSock1 is among the lines of the openSockets
   TestAssert "open socket from with same host on different local port", tSock2 is among the lines of the openSockets
   close socket tSock1
   close socket tSock2
end TestOpenSocketFromSameHostDifferentPort

on TestOpenSocketFromWithAddress
   local tLocalAddress
   repeat for each line tInterface in the networkInterfaces
      if tInterface is not "127.0.0.1" then
         put tInterface into tLocalAddress
         exit repeat
      end if
   end repeat

   if tLocalAddress is not empty then
      put "livecode.com|" & uuid() into tSock
      open socket from tLocalAddress to tSock
      TestAssert "open socket from with local address", tSock is among the lines of the openSockets
      close socket tSock
   end if
end TestOpenSocketFromWithAddress

on TestOpenSocketFromWithAddressAndPort
   local tLocalAddress
   repeat for each line tInterface in the networkInterfaces
      if tInterface is not "127.0.0.1" then
         put tInterface into tLocalAddress
         exit repeat
      end if
   end repeat

   if tLocalAddress is not empty then
      put "livecode.com|" & uuid() into tSock
      open socket from tLocalAddress & ":8005" to tSock
      TestAssert "open socket from with local address and port", tSock is among the lines of the openSockets
      close socket tSock
   end if
end TestOpenSocketFromWithAddressAndPort

on TestOpenSocketFromMultipleHostsSameAddress
   local tLocalAddress
   repeat for each line tInterface in the networkInterfaces
      if tInterface is not "127.0.0.1" then
         put tInterface into tLocalAddress
         exit repeat
      end if
   end repeat

   if tLocalAddress is not empty then
      put "livecode.com|" & uuid() into tSock1
      put "google.com|" & uuid() into tSock2
      open socket from tLocalAddress & ":8006" to tSock1
      TestAssert "open socket result is empty: " & the result, the result is empty
      TestAssert "open socket from with multiple hosts using same local address", tSock1 is among the lines of the openSockets
      open socket from tLocalAddress & ":8006" to tSock2
      TestAssert "open socket result is empty: " & the result, the result is empty
      -- the second open looks up its host in a wait that also handles the
      -- first socket, and ends it if its connection failed meanwhile, which
      -- frees the local port: then the two sockets never shared it
      if tSock1 is among the lines of the openSockets then
         TestAssert "open socket from with multiple hosts using same local address", tSock2 is among the lines of the openSockets
      else
         TestSkip "open socket from with multiple hosts using same local address", "the first connection failed before the second socket was opened"
      end if
      close socket tSock1
      close socket tSock2
   end if
end TestOpenSocketFromMultipleHostsSameAddress

on TestOpenSocketFromSameHostDifferentAddress
   local tLocalAddresses
   put the networkInterfaces into tLocalAddresses
   filter tLocalAddresses without "127.0.0.1"

   if the number of lines in tLocalAddresses > 1 then
      local tSocketCount
      put 1 into tSocketCount
      local tSockets
      repeat for each line tLocalAddress in tLocalAddresses
         put "livecode.com|" & uuid() into tSock
         open socket from tLocalAddress & ":8007" to tSock
         TestAssert "open socket from with same host on different local address", tSock is among the lines of the openSockets
         put tSock into tSockets[tSocketCount]
         add 1 to tSocketCount
      end repeat
      repeat with tSocketCount = 1 to the number of lines in tLocalAddresses
         close socket tSockets[tSocketCount]
      end repeat
   end if
end TestOpenSocketFromSameHostDifferentAddress

on TestOpenSocketFromWithAddressUsingLocalServer
   accept connections on port "8008" with message "connectedPlaceHolder"
   wait until "8008" is among the lines of the openSockets with messages

   open socket from "127.0.0.1" to "127.0.0.1:8008"
   TestAssert "open socket from with local address to local server", "127.0.0.1:8008" is among the lines of the openSockets

   repeat for each line tSocket in the openSockets
      close socket tSocket
   end repeat
end TestOpenSocketFromWithAddressUsingLocalServer

on TestOpenSocketFromWithAddressAndPortUsingLocalServer
   accept connections on port "8009" with message "connectedPlaceHolder"
   wait until "8009" is among the lines of the openSockets with messages

   open socket from "127.0.0.1:8010" to "127.0.0.1:8009"
   TestAssert "open socket from with local address and port to local server", "127.0.0.1:8009" is among the lines of the openSockets

   close socket "127.0.0.1:8010"
   close socket "127.0.0.1:8009"
   close socket "8009"
end TestOpenSocketFromWithAddressAndPortUsingLocalServer

on TestAcceptConnectionsInEphemeralPortRange
   -- in lieu of coming up with a per-OS/config list of ephemeral ranges
   -- we will just test if the port opened is greater than 1024 which
   -- is the bare minimum for BSD sockets then try and connect to it
   local tPort
   accept connections on port "0" with message "connectedPlaceHolder"
   put it into tPort
   
   TestAssert "accept on port 0 binds to socket in ephemeral port range", tPort > 1024
   TestAssert "ephemeral port is in the open sockets", tPort is among the lines of the openSockets
   TestAssert "Port 0 is not in the open sockets", 0 is not among the lines of the openSockets
   
   local tSocket
   put "127.0.0.1:" & tPort into tSocket
   
   open socket to tSocket
   TestAssert "open socket to ephemeral port", tSocket is among the lines of the openSockets
   
   close socket tSocket
   close socket tPort
end TestAcceptConnectionsInEphemeralPortRange
