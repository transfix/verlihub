#include <gtest/gtest.h>
#include "wrapper.h"
#include <fstream>
#include <string>
#include <vector>

// Mock callbacks (empty functions for the callback table)
w_Targs* mock_callback(int id, w_Targs* args) { return nullptr; }
w_Tcallback mock_callbacks[W_MAX_CALLBACKS] = { mock_callback };

// Test fixture
class PythonWrapperTest : public ::testing::Test {
protected:
    void SetUp() override {
        // Create a test script file
        std::ofstream script_file("test_script.py");
        script_file << "def OnTimer():\n"
                    << "    return 42\n"
                    << "def OnParsedMsgChat(user, data):\n"
                    << "    return 'Processed: ' + data\n";
        script_file.close();
    }

    void TearDown() override {
        // Remove test script
        std::remove("test_script.py");
    }
};

// Test initialization and end
TEST_F(PythonWrapperTest, InitAndEnd) {
    EXPECT_EQ(1, w_Begin(mock_callbacks));
    EXPECT_EQ(1, w_End());
}

// Test script loading and unloading
TEST_F(PythonWrapperTest, LoadAndUnloadScript) {
    w_Begin(mock_callbacks);

    int id = w_ReserveID();
    EXPECT_GE(id, 0);

    // Mock args: id, path, botname, opchatname, config_dir, starttime, config_name
    w_Targs* args = w_pack("lssssls", id, "test_script.py", "TestBot", "OpChat", ".", (long)0, "config");
    EXPECT_EQ(id, w_Load(args));
    free(args);  // Use free(), not w_free_args() - w_Load() copies strings with strdup()

    // Check hooks
    EXPECT_TRUE(w_HasHook(id, W_OnTimer));
    EXPECT_TRUE(w_HasHook(id, W_OnParsedMsgChat));

    EXPECT_EQ(1, w_Unload(id));

    w_End();
}

// Test calling a hook
TEST_F(PythonWrapperTest, CallHook) {
    w_Begin(mock_callbacks);

    int id = w_ReserveID();
    w_Targs* args = w_pack("lssssls", id, "test_script.py", "TestBot", "OpChat", ".", (long)0, "config");
    w_Load(args);
    free(args);  // Use free(), not w_free_args() - w_Load() copies strings with strdup()

    // Call OnTimer (no params)
    w_Targs* params = w_pack("");
    w_Targs* res = w_CallHook(id, W_OnTimer, params);
    free(params);  // Use free() for simple params

    ASSERT_NE(res, nullptr);
    long ret_val;
    w_unpack(res, "l", &ret_val);
    EXPECT_EQ(42, ret_val);
    free(res);

    // Call OnParsedMsgChat (with params: user pointer, data string)
    void* mock_user = nullptr;  // Mock user
    params = w_pack("ps", mock_user, "hello");
    res = w_CallHook(id, W_OnParsedMsgChat, params);
    free(params);  // Use free() for simple params

    ASSERT_NE(res, nullptr);
    char* ret_str;
    w_unpack(res, "s", &ret_str);
    EXPECT_STREQ("Processed: hello", ret_str);
    free(ret_str);
    free(res);

    w_Unload(id);
}

// ===== Encoding Conversion Tests =====

TEST_F(PythonWrapperTest, UTF8Encoding) {
    // Create a script that returns and receives UTF-8 strings
    std::string encoding_script = std::string(BUILD_DIR) + "/test_utf8_" + std::to_string(getpid()) + ".py";
    std::ofstream script_file(encoding_script);
    script_file << "# -*- coding: utf-8 -*-\n"
                << "def echo_string(text):\n"
                << "    return text\n"
                << "def get_unicode():\n"
                << "    return 'Hello 世界 🌍'\n";
    script_file.close();
    
    int id = w_ReserveID();
    w_Targs* args = w_pack("lssssls", id, encoding_script.c_str(), "TestBot", "OpChat", ".", (long)0, "config");
    ASSERT_EQ(id, w_Load(args));
    free(args);
    
    // Test: Send UTF-8 from C++, get it back from Python
    w_Targs* params = w_pack("s", "Привет");  // Russian "Hello"
    w_Targs* res = w_CallFunction(id, "echo_string", params);
    free(params);
    
    ASSERT_NE(res, nullptr);
    char* ret_str;
    ASSERT_TRUE(w_unpack(res, "s", &ret_str));
    EXPECT_STREQ("Привет", ret_str);
    free(ret_str);
    free(res);
    
    // Test: Get UTF-8 string from Python
    res = w_CallFunction(id, "get_unicode", nullptr);
    ASSERT_NE(res, nullptr);
    ASSERT_TRUE(w_unpack(res, "s", &ret_str));
    EXPECT_STREQ("Hello 世界 🌍", ret_str);
    free(ret_str);
    free(res);
    
    w_Unload(id);
    std::remove(encoding_script.c_str());
}

TEST_F(PythonWrapperTest, CP1251EncodingConversion) {
    // Test Cyrillic (CP1251) encoding conversion
    // Python always uses UTF-8, wrapper should convert to/from hub encoding
    
    std::string encoding_script = std::string(BUILD_DIR) + "/test_cp1251_" + std::to_string(getpid()) + ".py";
    std::ofstream script_file(encoding_script);
    script_file << "# -*- coding: utf-8 -*-\n"
                << "def process_cyrillic(text):\n"
                << "    # Python receives UTF-8, returns UTF-8\n"
                << "    return 'Получено: ' + text\n";  // "Received: "
    script_file.close();
    
    int id = w_ReserveID();
    w_Targs* args = w_pack("lssssls", id, encoding_script.c_str(), "TestBot", "OpChat", ".", (long)0, "config");
    ASSERT_EQ(id, w_Load(args));
    free(args);
    
    // Send Cyrillic text from C++ (in UTF-8)
    // Wrapper should convert UTF-8 -> hub encoding -> Python receives UTF-8
    w_Targs* params = w_pack("s", "Тест");  // "Test" in Russian
    w_Targs* res = w_CallFunction(id, "process_cyrillic", params);
    free(params);
    
    ASSERT_NE(res, nullptr);
    char* ret_str;
    ASSERT_TRUE(w_unpack(res, "s", &ret_str));
    
    // Should get back "Получено: Тест" in UTF-8
    EXPECT_NE(strstr(ret_str, "Получено"), nullptr) << "Missing 'Получено' in result: " << ret_str;
    EXPECT_NE(strstr(ret_str, "Тест"), nullptr) << "Missing 'Тест' in result: " << ret_str;
    
    free(ret_str);
    free(res);
    
    w_Unload(id);
    std::remove(encoding_script.c_str());
}

TEST_F(PythonWrapperTest, ISO88591EncodingConversion) {
    // Test Latin-1 (ISO-8859-1) encoding conversion
    
    std::string encoding_script = std::string(BUILD_DIR) + "/test_latin1_" + std::to_string(getpid()) + ".py";
    std::ofstream script_file(encoding_script);
    script_file << "# -*- coding: utf-8 -*-\n"
                << "def process_latin(text):\n"
                << "    return 'Café ' + text\n";
    script_file.close();
    
    int id = w_ReserveID();
    w_Targs* args = w_pack("lssssls", id, encoding_script.c_str(), "TestBot", "OpChat", ".", (long)0, "config");
    ASSERT_EQ(id, w_Load(args));
    free(args);
    
    // Send Latin-1 text with accented characters
    w_Targs* params = w_pack("s", "ñoño");  // Spanish text
    w_Targs* res = w_CallFunction(id, "process_latin", params);
    free(params);
    
    ASSERT_NE(res, nullptr);
    char* ret_str;
    ASSERT_TRUE(w_unpack(res, "s", &ret_str));
    
    // Should contain both "Café" and "ñoño" properly encoded
    EXPECT_NE(strstr(ret_str, "Café"), nullptr) << "Missing 'Café' in result: " << ret_str;
    EXPECT_NE(strstr(ret_str, "ñoño"), nullptr) << "Missing 'ñoño' in result: " << ret_str;
    
    free(ret_str);
    free(res);
    
    w_Unload(id);
    std::remove(encoding_script.c_str());
}

TEST_F(PythonWrapperTest, EncodingRoundTrip) {
    // Test that strings survive round-trip conversion
    
    std::string encoding_script = std::string(BUILD_DIR) + "/test_roundtrip_" + std::to_string(getpid()) + ".py";
    std::ofstream script_file(encoding_script);
    script_file << "# -*- coding: utf-8 -*-\n"
                << "def echo(text):\n"
                << "    return text\n";
    script_file.close();
    
    int id = w_ReserveID();
    w_Targs* args = w_pack("lssssls", id, encoding_script.c_str(), "TestBot", "OpChat", ".", (long)0, "config");
    ASSERT_EQ(id, w_Load(args));
    free(args);
    
    // Test various character sets
    const char* test_strings[] = {
        "Simple ASCII",
        "Café résumé",           // French
        "Привет мир",            // Russian
        "Cześć świat",           // Polish
        "Hello 世界",            // Mixed English + Chinese
        "España ñoño",           // Spanish
        nullptr
    };
    
    for (int i = 0; test_strings[i] != nullptr; i++) {
        w_Targs* params = w_pack("s", test_strings[i]);
        w_Targs* res = w_CallFunction(id, "echo", params);
        free(params);
        
        ASSERT_NE(res, nullptr) << "Failed on: " << test_strings[i];
        char* ret_str;
        ASSERT_TRUE(w_unpack(res, "s", &ret_str)) << "Failed to unpack: " << test_strings[i];
        
        // Round-trip should preserve the string
        EXPECT_STREQ(test_strings[i], ret_str) 
            << "Round-trip failed for: " << test_strings[i] 
            << " got: " << ret_str;
        
        free(ret_str);
        free(res);
    }
    
    w_Unload(id);
    std::remove(encoding_script.c_str());
}

TEST_F(PythonWrapperTest, InvalidCharacterHandling) {
    // Test that invalid/unconvertible characters are handled gracefully
    
    std::string encoding_script = std::string(BUILD_DIR) + "/test_invalid_chars_" + std::to_string(getpid()) + ".py";
    std::ofstream script_file(encoding_script);
    script_file << "# -*- coding: utf-8 -*-\n"
                << "def echo(text):\n"
                << "    return text\n"
                << "def return_mixed():\n"
                << "    # Mix of ASCII, valid UTF-8, and edge cases\n"
                << "    return 'Test™©®'\n";  // Trademark, copyright, registered symbols
    script_file.close();
    
    int id = w_ReserveID();
    w_Targs* args = w_pack("lssssls", id, encoding_script.c_str(), "TestBot", "OpChat", ".", (long)0, "config");
    ASSERT_EQ(id, w_Load(args));
    free(args);
    
    // Test 1: Characters that don't exist in some encodings (e.g., CP1251 can't encode Chinese)
    // Should either substitute or handle gracefully without crashing
    w_Targs* params = w_pack("s", "Hello 世界");  // Chinese characters
    w_Targs* res = w_CallFunction(id, "echo", params);
    free(params);
    
    ASSERT_NE(res, nullptr) << "Should not crash on unconvertible characters";
    char* ret_str;
    ASSERT_TRUE(w_unpack(res, "s", &ret_str));
    // The string should come back (possibly with substitutions depending on hub encoding)
    // At minimum, "Hello" should be present
    EXPECT_NE(strstr(ret_str, "Hello"), nullptr) << "ASCII part should survive: " << ret_str;
    free(ret_str);
    free(res);
    
    // Test 2: Special symbols that might not exist in legacy encodings
    res = w_CallFunction(id, "return_mixed", nullptr);
    ASSERT_NE(res, nullptr);
    ASSERT_TRUE(w_unpack(res, "s", &ret_str));
    EXPECT_NE(strstr(ret_str, "Test"), nullptr) << "ASCII part should be present: " << ret_str;
    free(ret_str);
    free(res);
    
    // Test 3: Control characters and newlines
    params = w_pack("s", "Line1\nLine2\tTabbed\rCarriage");
    res = w_CallFunction(id, "echo", params);
    free(params);
    
    ASSERT_NE(res, nullptr);
    ASSERT_TRUE(w_unpack(res, "s", &ret_str));
    EXPECT_NE(strstr(ret_str, "Line1"), nullptr) << "Should handle newlines";
    EXPECT_NE(strstr(ret_str, "Line2"), nullptr);
    free(ret_str);
    free(res);
    
    // Test 4: Very long strings with mixed encodings
    std::string long_mixed = "ASCII ";
    for (int i = 0; i < 100; i++) {
        long_mixed += "Ñ";  // Repeat Spanish Ñ
    }
    long_mixed += " END";
    
    params = w_pack("s", long_mixed.c_str());
    res = w_CallFunction(id, "echo", params);
    free(params);
    
    ASSERT_NE(res, nullptr);
    ASSERT_TRUE(w_unpack(res, "s", &ret_str));
    EXPECT_NE(strstr(ret_str, "ASCII"), nullptr) << "Start should be present";
    EXPECT_NE(strstr(ret_str, "END"), nullptr) << "End should be present";
    free(ret_str);
    free(res);
    
    // Test 5: Empty string edge case
    params = w_pack("s", "");
    res = w_CallFunction(id, "echo", params);
    free(params);
    
    ASSERT_NE(res, nullptr);
    ASSERT_TRUE(w_unpack(res, "s", &ret_str));
    EXPECT_STREQ("", ret_str);
    free(ret_str);
    free(res);
    
    // Test 6: String with only problematic characters
    params = w_pack("s", "🌍🌎🌏");  // Earth emojis - likely can't encode in legacy charsets
    res = w_CallFunction(id, "echo", params);
    free(params);
    
    ASSERT_NE(res, nullptr) << "Should handle emoji gracefully without crashing";
    ASSERT_TRUE(w_unpack(res, "s", &ret_str));
    // We got *something* back - exact result depends on hub encoding
    // Main point: didn't crash
    free(ret_str);
    free(res);
    
    w_Unload(id);
    std::remove(encoding_script.c_str());
}

int main(int argc, char **argv) {
    ::testing::InitGoogleTest(&argc, argv);
    ::testing::AddGlobalTestEnvironment(new GlobalPythonEnv());
    return RUN_ALL_TESTS();
}