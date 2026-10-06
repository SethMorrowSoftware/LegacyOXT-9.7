script "TestLibUrlCONNECT"

-- The answer of the test's proxy, and the request it read
local sResponse, sRequest

on TestSetup
   TestSkipIfNot "platform", "MacOS,Win32,Linux"
   TestSkipIf "stack", "StandaloneTestRunnerMainstack"
   TestSkipIf "environment", "server"
         
   local tLibURl
   put TestGetEngineRepositoryPath() & "/ide-support/revliburl.livecodescript" into tLibUrl
   send "extensionInitialize" to stack tLibUrl
end TestSetup


on TestCONNECTHeader
   constant kTestHost = "google.com"
   -- The proxy is a server of this process, on a port the system chooses,
   -- which answers the CONNECT with 502 Bad Gateway, so that libURL goes
   -- no further. (The test used a port nothing listened on: libURL builds
   -- the CONNECT only once the connection is open, so that passed only
   -- where the engine reported the refused connection as opened before
   -- its error, as on Linux and macOS, and failed on Windows.)
   local tPort
   put "HTTP/1.1 502 Bad Gateway" & crlf & "Content-Length: 0" & crlf & crlf into sResponse
   accept connections on port 0 with message "ConnectProxyAccept"
   put it into tPort
   set the httpproxy to "http://127.0.0.1:" & tPort
   
   put url ("https://" & kTestHost) into tValue
   
   put liburlLastHTTPHeaders() into tHeaders
   
   put "CONNECT" && kTestHost & ":443 HTTP/1.1" & cr & \
         "Host: " & kTestHost & ":443" into tExpectedHeader
   
   TestAssert "correct CONNECT header", tHeaders is tExpectedHeader
   TestAssert "the proxy got the CONNECT", \
         sRequest begins with "CONNECT" && kTestHost & ":443 HTTP/1.1" & crlf
   set the httpproxy to empty
   close socket tPort
end TestCONNECTHeader

on ConnectProxyAccept pSocket
   read from socket pSocket until crlf & crlf with message "ConnectProxyRead"
end ConnectProxyAccept

on ConnectProxyRead pSocket, pRequest
   put pRequest into sRequest
   write sResponse to socket pSocket
end ConnectProxyRead
