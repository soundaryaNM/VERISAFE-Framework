#ifndef MOCK_HTTP_CLIENT_H
#define MOCK_HTTP_CLIENT_H

#include <Arduino_stubs.h>

class MockHTTPClient {
public:
    void setResponseCode(int code) { mock_response_code = code; }
    void setResponseString(String str) { mock_response_body = str; }
    int GET() { return mock_response_code; }
    String getString() { return mock_response_body; }
    void begin(String url) {}
    void setTimeout(int timeout) {}
    void end() {}

private:
    int mock_response_code = 200;
    String mock_response_body = "";
};

#endif