script "TestLibUrlStatusCodes"

local sLogField

-- The response of the test's HTTP server, and the request it read
local sResponse, sRequest

on TestSetup
   TestSkipIfNot "platform", "MacOS,Win32,Linux"
   TestSkipIf "environment", "server"
   
   if not TestIsInStandalone() then
      local tLibURl
      put TestGetEngineRepositoryPath() & "/ide-support/revliburl.livecodescript" into tLibUrl
      send "extensionInitialize" to stack tLibUrl
   end if
end TestSetup


on TestStatusCodes
   # Response Code 204
   -- A server of this process, on a port the system chooses, answers in
   -- place of an outside service (the test asked http://httpstat.us/204,
   -- which has not always answered). Its 204 No Content has no
   -- Content-Length, as a 204 has no body, and it keeps the connection open,
   -- as an HTTP/1.1 server may: libURL must end the request when the header
   -- has come, not wait for a body or for the connection to close, which
   -- would last until the socket timeout.
   local tPort
   put "HTTP/1.1 204 No Content" & crlf & "Server: TestLibUrlStatusCodes" & crlf & crlf into sResponse
   accept connections on port 0 with message "StatusCodesServerAccept"
   put it into tPort

   set the socketTimeoutInterval to 60000
   put the milliseconds into tStartTime
   put URL ("http://127.0.0.1:" & tPort & "/204") into tData
   TestAssert "no error is reported", the result is empty
   TestAssert "response is not delayed until socket timeout", the milliseconds - tStartTime < 59000
   TestAssert "data is empty", tData is empty
   TestAssert "the request reached the server", sRequest begins with "GET /204 "
   close socket tPort
end TestStatusCodes

on StatusCodesServerAccept pSocket
   read from socket pSocket until crlf & crlf with message "StatusCodesServerRead"
end StatusCodesServerAccept

on StatusCodesServerRead pSocket, pRequest
   put pRequest into sRequest
   write sResponse to socket pSocket
end StatusCodesServerRead
