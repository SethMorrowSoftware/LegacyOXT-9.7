script "TestLibUrlHeaders"

-- The answer of the test's HTTP server, and the request it read
local sResponse, sRequest

on TestSetup
   TestSkipIfNot "platform", "MacOS,Win32,Linux"
   TestSkipIf "stack", "tsNetLibURL"
   TestSkipIf "environment", "server"
   
   if not TestIsInStandalone() then
      local tLibURl
      put TestGetEngineRepositoryPath() & "/ide-support/revliburl.livecodescript" into tLibUrl
      send "extensionInitialize" to stack tLibUrl
   end if
end TestSetup

on TestHostHeader
   constant kTestHost = "uniquehostname.example.org"
   -- A server of this process, on a port the system chooses, reads the
   -- request and answers 204 No Content. (The test posted to a port
   -- nothing listened on and read the request from libURL's log field:
   -- libURL sends and logs the request only once the connection is open,
   -- so that passed only where the engine reported the refused connection
   -- as opened before its error, as on Linux and macOS, and failed on
   -- Windows.)
   local tPort
   put "HTTP/1.1 204 No Content" & crlf & "Server: TestLibUrlHeaders" & crlf & crlf into sResponse
   accept connections on port 0 with message "HeadersServerAccept"
   put it into tPort

   set the httpHeaders to "Host:" && kTestHost
   post "dummy content" to "http://127.0.0.1:" & tPort
   TestAssert "the request reached the server", sRequest begins with "POST / HTTP/1.1" & crlf

   -- The header lines of the request, up to the empty line that ends them
   local tHeaders
   replace crlf with return in sRequest
   repeat for each line tLine in sRequest
      if tLine is empty then
         exit repeat
      end if
      put tLine & return after tHeaders
   end repeat
   TestDiagnostic tHeaders

   local tHostCount
   put 0 into tHostCount
   repeat for each line tLine in tHeaders
      put word 1 to -1 of tLine into tLine
      if tLine begins with "Host:" then
         TestAssert "host header has correct value", tLine ends with kTestHost
         add 1 to tHostCount
      end if
   end repeat

   TestAssert "exactly one host header", tHostCount is 1
   close socket tPort
end TestHostHeader

on HeadersServerAccept pSocket
   read from socket pSocket until crlf & crlf with message "HeadersServerRead"
end HeadersServerAccept

on HeadersServerRead pSocket, pRequest
   put pRequest into sRequest
   write sResponse to socket pSocket
end HeadersServerRead

on TestTeardown
   set the httpHeaders to empty
end TestTeardown
