#include "HTTPClient.h"

int HTTPClient::mock_response_code = 200;
String HTTPClient::mock_response_body = "";
String HTTPClient::last_url = "";

HTTPClient::HTTPClient() {}

void HTTPClient::setTimeout(int ms) {
    // Mock implementation
}

void HTTPClient::begin(String url) {
    last_url = url;
}

int HTTPClient::GET() {
    return mock_response_code;
}

String HTTPClient::getString() {
    return mock_response_body;
}

void HTTPClient::end() {
    // Mock implementation
}
