--[[
    Test Lua script for basic functionality validation.
    
    This script tests the basic Lua-VH integration callbacks
    and can be loaded via the plugin interface.
--]]

-- Test flag to indicate successful load
VH_TEST_LOADED = true
VH_TEST_RESULTS = {}

-- Helper to record test results
local function record_result(test_name, passed, message)
    VH_TEST_RESULTS[test_name] = {
        passed = passed,
        message = message or ""
    }
end

-- Test: VH namespace exists
local function test_vh_namespace()
    if VH then
        record_result("vh_namespace", true, "VH namespace exists")
        return true
    else
        record_result("vh_namespace", false, "VH namespace not found")
        return false
    end
end

-- Test: Basic VH functions exist
local function test_vh_functions()
    local funcs = {
        "SendToUser",
        "SendToAll",
        "SendPMToAll",
        "GetMyINFO",
        "GetUserIP",
        "GetUserClass",
        "IsUserOnline",
        "GetNickList",
        "GetOPList",
        "KickUser",
        "SetConfig",
        "GetConfig"
    }
    
    local missing = {}
    for _, fname in ipairs(funcs) do
        if not VH or not VH[fname] then
            table.insert(missing, fname)
        end
    end
    
    if #missing == 0 then
        record_result("vh_functions", true, "All basic VH functions exist")
        return true
    else
        record_result("vh_functions", false, "Missing functions: " .. table.concat(missing, ", "))
        return false
    end
end

-- Test: SQL functions exist  
local function test_sql_functions()
    local funcs = {"SQLQuery", "SQLFetch", "SQLFree"}
    local missing = {}
    
    for _, fname in ipairs(funcs) do
        if not VH or not VH[fname] then
            table.insert(missing, fname)
        end
    end
    
    if #missing == 0 then
        record_result("sql_functions", true, "SQL functions exist")
        return true
    else
        record_result("sql_functions", false, "Missing SQL functions: " .. table.concat(missing, ", "))
        return false
    end
end

-- Callback: OnScriptLoaded (called when script loads successfully)
function VH.OnScriptLoaded()
    test_vh_namespace()
    test_vh_functions()
    test_sql_functions()
    
    -- Report results to op chat if possible
    local passed = 0
    local failed = 0
    for name, result in pairs(VH_TEST_RESULTS) do
        if result.passed then
            passed = passed + 1
        else
            failed = failed + 1
        end
    end
    
    if VH and VH.SendToOpChat then
        VH.SendToOpChat("[LuaTest] Basic tests complete: " .. passed .. " passed, " .. failed .. " failed")
    end
end

-- Manual test runner (can be called from other scripts)
function RunBasicTests()
    test_vh_namespace()
    test_vh_functions()
    test_sql_functions()
    return VH_TEST_RESULTS
end

-- Auto-run tests on load
RunBasicTests()
