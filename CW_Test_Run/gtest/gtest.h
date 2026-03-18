#pragma once
// Minimal Google Test-like framework for testing
#include <iostream>
#include <vector>
#include <functional>
#include <cassert>

namespace testing {

class Test {
public:
    virtual ~Test() {}
    virtual void SetUp() {}
    virtual void TearDown() {}
};

}

class TestRegistry {
private:
    struct TestInfo {
        std::string name;
        std::function<void()> func;
    };
    std::vector<TestInfo> tests_;
    static TestRegistry* instance_;

    TestRegistry() {}

public:
    static TestRegistry& instance() {
        if (!instance_) instance_ = new TestRegistry();
        return *instance_;
    }

    void register_test(const std::string& name, std::function<void()> func) {
        tests_.push_back({name, func});
    }

    int run_all_tests() {
        int failures = 0;
        for (const auto& test : tests_) {
            try {
                test.func();
                std::cout << "[ PASS ] " << test.name << std::endl;
            } catch (const std::exception& e) {
                std::cout << "[ FAIL ] " << test.name << ": " << e.what() << std::endl;
                failures++;
            } catch (...) {
                std::cout << "[ FAIL ] " << test.name << ": Unknown exception" << std::endl;
                failures++;
            }
        }
        return failures;
    }
};

TestRegistry* TestRegistry::instance_ = nullptr;

#define TEST(suite, name) \
    void Test_##suite##_##name(); \
    struct Registrar_##suite##_##name { \
        Registrar_##suite##_##name() { \
            TestRegistry::instance().register_test(#suite "." #name, Test_##suite##_##name); \
        } \
    } registrar_##suite##_##name; \
    void Test_##suite##_##name()

#define ASSERT_EQ(a, b) assert((a) == (b))
#define ASSERT_NE(a, b) assert((a) != (b))
#define ASSERT_TRUE(a) assert((a))
#define ASSERT_FALSE(a) assert(!(a))
#define ASSERT_GT(a, b) assert((a) > (b))
#define ASSERT_GE(a, b) assert((a) >= (b))
#define ASSERT_LT(a, b) assert((a) < (b))
#define ASSERT_LE(a, b) assert((a) <= (b))

#define EXPECT_EQ(a, b) ASSERT_EQ(a, b)
#define EXPECT_NE(a, b) ASSERT_NE(a, b)
#define EXPECT_TRUE(a) ASSERT_TRUE(a)
#define EXPECT_FALSE(a) ASSERT_FALSE(a)
#define EXPECT_GT(a, b) ASSERT_GT(a, b)
#define EXPECT_GE(a, b) ASSERT_GE(a, b)
#define EXPECT_LT(a, b) ASSERT_LT(a, b)
#define EXPECT_LE(a, b) ASSERT_LE(a, b)

#define EXPECT_STREQ(a, b) assert(std::string(a) == std::string(b))

#define TEST_F(fixture, name) \
    class Test_##fixture##_##name : public fixture { \
    public: \
        Test_##fixture##_##name() {} \
        void TestBody(); \
    }; \
    void TestFunc_##fixture##_##name() { \
        Test_##fixture##_##name test; \
        test.SetUp(); \
        test.TestBody(); \
        test.TearDown(); \
    } \
    struct Registrar_##fixture##_##name { \
        Registrar_##fixture##_##name() { \
            TestRegistry::instance().register_test(#fixture "." #name, TestFunc_##fixture##_##name); \
        } \
    } registrar_##fixture##_##name; \
    void Test_##fixture##_##name::TestBody()

namespace testing {

void InitGoogleTest(int* argc, char** argv) {
    // Dummy
}

}

int RUN_ALL_TESTS() {
    return TestRegistry::instance().run_all_tests();
}
