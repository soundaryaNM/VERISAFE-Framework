#pragma once

#include <string>
#include <iostream>
#include <chrono>
#include <thread>
#include <vector>

void digitalWrite(int pin, int value);
int digitalRead(int pin);
void pinMode(int pin, int mode);
void delay(int ms);
unsigned long millis();

// Helper to reset all stubs
void reset_arduino_stubs();

class String {
public:
    std::string content;
    String();
    String(const char* str);
    String(int val);
    String& operator+=(const char* str);
    String& operator+=(char ch);
    String& operator+=(const String& other);
    String operator+(const char* str) const;
    String operator+(const String& other) const;
    const char* c_str() const;
    int toInt() const;
    bool equals(const char* str) const;
    bool equals(const String& str) const;
    
    int indexOf(const char* str, int fromIndex = 0) const;
    int indexOf(char ch, int fromIndex = 0) const;
    int indexOf(const String& str, int fromIndex = 0) const;
    int length() const;
    String substring(int start, int end = -1) const;
    char operator[](int index) const;
    bool operator==(const char* str) const;
    bool operator==(const String& other) const;
    
    friend String operator+(const char* lhs, const String& rhs);
};

struct DigitalWriteCall {
    int pin;
    int value;
};

struct DelayCall {
    unsigned long ms;
};

// Use names compatible with both styles (mock_ prefix and without)
extern std::vector<DigitalWriteCall> mock_digitalWrite_calls;
extern std::vector<DigitalWriteCall>& digitalWrite_calls; // Alias

extern std::vector<DelayCall> mock_delay_calls;
extern std::vector<DelayCall>& delay_calls; // Alias

class SerialClass {
public:
    std::vector<std::string> println_calls;
    std::vector<std::string> print_calls;
    std::string outputBuffer; // Accumulated output
    
    int begin_baud = 0;
    int begin_call_count = 0;
    int println_call_count = 0;
    int print_call_count = 0;
    int last_baud_rate = 0; // Alias for begin_baud
    
    void begin(int baud);
    void print(const char* str);
    void println(const char* str);
    void print(int val);
    void println(int val);
    void print(const String& str);
    void println(const String& str);
    
    void clear() { println_calls.clear(); print_calls.clear(); outputBuffer.clear(); }
    std::string lastPrintln() { return println_calls.empty() ? "" : println_calls.back(); }
    
    void reset();
};

extern SerialClass Serial;

#define HIGH 1
#define LOW 0
#define INPUT 0
#define OUTPUT 1

#define HTTP_CODE_OK 200
#define HTTP_CODE_BAD_REQUEST 400

class HTTPClient {
public:
    // Static mock control
    static int mock_response_code;
    static std::string mock_response_body;
    
    // Instance tracking (optional, but good for verification)
    int begin_call_count = 0;
    std::string begin_url;
    
    void setTimeout(int ms);
    void begin(const String& url);
    int GET();
    String getString();
    void end();
    
    static void reset();
};

class File {
public:
    // Static mock control
    static bool mock_open_success;
    static std::string mock_content;
    static int mock_pos;
    static std::vector<std::string> mock_printed;

    bool available();
    char read();
    void close();
    operator bool() const;
    String readStringUntil(char terminator);
    size_t print(const String& str);
};

class SPIFFSClass {
public:
    // Static mock control
    static bool mock_begin_success;
    static bool mock_begin_format_success;

    bool begin(bool format);
    bool begin();
    File open(const char* path, const char* mode = "r");
};

extern SPIFFSClass SPIFFS;

extern bool SPI_DEBUGGING;
