#include "Arduino_stubs.h"
#include <iostream>
#include <map>
#include <chrono>

static std::map<int, int> pin_states;
static auto start_time = std::chrono::steady_clock::now();

std::vector<DigitalWriteCall> mock_digitalWrite_calls;
std::vector<DigitalWriteCall>& digitalWrite_calls = mock_digitalWrite_calls; // Alias

std::vector<DelayCall> mock_delay_calls;
std::vector<DelayCall>& delay_calls = mock_delay_calls; // Alias

void reset_arduino_stubs() {
    mock_digitalWrite_calls.clear();
    mock_delay_calls.clear();
    pin_states.clear();
    Serial.reset();
    start_time = std::chrono::steady_clock::now();
}

void digitalWrite(int pin, int value) {
    mock_digitalWrite_calls.push_back({pin, value});
    pin_states[pin] = value;
    // std::cout << "digitalWrite(" << pin << ", " << value << ")" << std::endl;
}

int digitalRead(int pin) {
    return pin_states[pin];
}

void pinMode(int pin, int mode) {
    // std::cout << "pinMode(" << pin << ", " << mode << ")" << std::endl;
}

void delay(int ms) {
    mock_delay_calls.push_back({(unsigned long)ms});
    // std::this_thread::sleep_for(std::chrono::milliseconds(ms)); // Don't actually sleep in tests
}

unsigned long millis() {
    auto now = std::chrono::steady_clock::now();
    auto duration = now - start_time;
    return std::chrono::duration_cast<std::chrono::milliseconds>(duration).count();
}

SerialClass Serial;

void SerialClass::reset() {
    println_calls.clear();
    print_calls.clear();
    outputBuffer.clear();
    begin_baud = 0;
    begin_call_count = 0;
    println_call_count = 0;
    print_call_count = 0;
    last_baud_rate = 0;
}

void SerialClass::begin(int baud) {
    begin_baud = baud;
    last_baud_rate = baud;
    begin_call_count++;
    // std::cout << "Serial.begin(" << baud << ")" << std::endl;
}

void SerialClass::print(const char* str) {
    print_calls.push_back(str);
    outputBuffer += str;
    print_call_count++;
    // std::cout << str;
}

void SerialClass::println(const char* str) {
    println_calls.push_back(str);
    outputBuffer += str;
    outputBuffer += "\n";
    println_call_count++;
    // std::cout << str << std::endl;
}

void SerialClass::print(int val) {
    print_calls.push_back(std::to_string(val));
    std::cout << val;
}

void SerialClass::println(int val) {
    println_calls.push_back(std::to_string(val));
    std::cout << val << std::endl;
}

void SerialClass::print(const String& str) {
    print_calls.push_back(str.content);
    outputBuffer += str.content;
    print_call_count++;
    // std::cout << str.c_str();
}

void SerialClass::println(const String& str) {
    println_calls.push_back(str.content);
    outputBuffer += str.content;
    outputBuffer += "\n";
    println_call_count++;
    // std::cout << str.c_str() << std::endl;
}

String::String() {}

String::String(const char* str) : content(str) {}

String::String(int val) : content(std::to_string(val)) {}

String& String::operator+=(const char* str) {
    content += str;
    return *this;
}

String& String::operator+=(char ch) {
    content += ch;
    return *this;
}

String& String::operator+=(const String& other) {
    content += other.content;
    return *this;
}

String String::operator+(const char* str) const {
    String result = *this;
    result.content += str;
    return result;
}

String String::operator+(const String& other) const {
    String result = *this;
    result.content += other.content;
    return result;
}

String operator+(const char* lhs, const String& rhs) {
    String result(lhs);
    result.content += rhs.content;
    return result;
}

const char* String::c_str() const {
    return content.c_str();
}

int String::toInt() const {
    try {
        return std::stoi(content);
    } catch (...) {
        return 0;
    }
}

bool String::equals(const char* str) const {
    return content == str;
}

bool String::equals(const String& str) const {
    return content == str.content;
}

int String::indexOf(const char* str, int fromIndex) const {
    size_t pos = content.find(str, fromIndex);
    return pos == std::string::npos ? -1 : static_cast<int>(pos);
}

int String::indexOf(char ch, int fromIndex) const {
    size_t pos = content.find(ch, fromIndex);
    return pos == std::string::npos ? -1 : static_cast<int>(pos);
}

int String::indexOf(const String& str, int fromIndex) const {
    size_t pos = content.find(str.content, fromIndex);
    return pos == std::string::npos ? -1 : static_cast<int>(pos);
}

int String::length() const {
    return static_cast<int>(content.length());
}

String String::substring(int start, int end) const {
    if (end == -1) end = length();
    if (start < 0) start = 0;
    if (end > length()) end = length();
    if (start >= end) return String("");
    return String(content.substr(start, end - start).c_str());
}

char String::operator[](int index) const {
    if (index < 0 || index >= length()) return '\0';
    return content[index];
}

bool String::operator==(const char* str) const {
    return content == str;
}

bool String::operator==(const String& other) const {
    return content == other.content;
}

#define HTTP_CODE_OK 200

// Define static members
int HTTPClient::mock_response_code = 200;
std::string HTTPClient::mock_response_body = "";

void HTTPClient::setTimeout(int ms) {
    // std::cout << "HTTPClient.setTimeout(" << ms << ")" << std::endl;
}

void HTTPClient::begin(const String& url) {
    begin_call_count++;
    begin_url = url.content;
    // std::cout << "HTTPClient.begin(" << url.content << ")" << std::endl;
}

int HTTPClient::GET() {
    // std::cout << "HTTPClient.GET() -> " << mock_response_code << std::endl;
    return mock_response_code;
}

String HTTPClient::getString() {
    return String(mock_response_body.c_str());
}

void HTTPClient::end() {
    // std::cout << "HTTPClient.end()" << std::endl;
}

void HTTPClient::reset() {
    mock_response_code = 200;
    mock_response_body = "";
}

SPIFFSClass SPIFFS;

bool SPI_DEBUGGING = false;

// Define static members
bool SPIFFSClass::mock_begin_success = true;
bool SPIFFSClass::mock_begin_format_success = true;

bool SPIFFSClass::begin(bool format) {
    // std::cout << "SPIFFS.begin(" << format << ")" << std::endl;
    return mock_begin_success;
}

bool SPIFFSClass::begin() {
    return begin(false);
}

File SPIFFSClass::open(const char* path, const char* mode) {
    // std::cout << "SPIFFS.open(" << path << ", " << mode << ")" << std::endl;
    if (!File::mock_open_success) {
        return File(); // Return invalid file
    }
    // In a real mock, we might return a valid file object that knows it's valid
    // For now, File::operator bool() returns true by default, so we need to handle the "invalid" case
    // But File is a value type here.
    // Let's assume the default constructor creates a "valid" file unless we add a flag.
    // Wait, File::operator bool() needs to know if it's valid.
    // We should probably add an isValid flag to File instance.
    // But for now, let's just rely on the static mock_open_success being checked by the test?
    // No, the code checks `if (!file)`.
    // So File needs an internal state.
    File f;
    // We need a way to mark 'f' as valid/invalid.
    // Since we can't easily change the File class definition in the cpp file, 
    // and I didn't add an isValid member in the header, 
    // I should have added it.
    // But wait, File::operator bool() is const.
    // Let's assume for now that if mock_open_success is false, we return a File that evaluates to false.
    // I need to update File class in header to support this state.
    return f;
}

// Define static members
bool File::mock_open_success = true;
std::string File::mock_content = "";
int File::mock_pos = 0;
std::vector<std::string> File::mock_printed;

bool File::available() {
    return mock_pos < mock_content.length();
}

char File::read() {
    if (mock_pos < mock_content.length()) {
        return mock_content[mock_pos++];
    }
    return 0;
}

void File::close() {
    // std::cout << "File.close()" << std::endl;
}

File::operator bool() const {
    // This is tricky without an instance member.
    // But since we are using static mocks, maybe we can just return mock_open_success?
    // The problem is that `if (!file)` is called on the returned object.
    // If I return a File object, and mock_open_success was false when open() was called...
    // Ideally, open() returns an object that knows it failed.
    // Let's use the static flag for now, assuming single-threaded tests.
    return mock_open_success; 
}

String File::readStringUntil(char terminator) {
    std::string result;
    char ch;
    while (available() && (ch = read()) != terminator) {
        result += ch;
    }
    return String(result.c_str());
}

size_t File::print(const String& str) {
    mock_printed.push_back(str.content);
    // std::cout << str.content;
    return str.length();
}
