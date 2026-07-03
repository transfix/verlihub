--[[
    Test Lua script for SQL operations.
    
    This script tests SQL query functionality available
    to Lua scripts in Verlihub.
--]]

VH_SQL_TEST = {
    results = {},
    errors = {}
}

local function record_result(test, passed, msg)
    VH_SQL_TEST.results[test] = {
        passed = passed,
        message = msg or ""
    }
end

local function record_error(test, err)
    table.insert(VH_SQL_TEST.errors, {
        test = test,
        error = err
    })
end

-- Test: Basic SELECT query
local function test_select_query()
    if not VH or not VH.SQLQuery then
        record_result("select_query", false, "SQLQuery not available")
        return
    end
    
    -- Try to query the reg_list table
    local success, err = pcall(function()
        local result = VH.SQLQuery("SELECT COUNT(*) as cnt FROM reg_list")
        if result then
            record_result("select_query", true, "SELECT query works")
        else
            record_result("select_query", false, "Query returned nil")
        end
    end)
    
    if not success then
        record_result("select_query", false, "Query failed: " .. tostring(err))
        record_error("select_query", err)
    end
end

-- Test: SQLFetch operation
local function test_sql_fetch()
    if not VH or not VH.SQLQuery or not VH.SQLFetch then
        record_result("sql_fetch", false, "SQL functions not available")
        return
    end
    
    local success, err = pcall(function()
        local result = VH.SQLQuery("SELECT nick FROM reg_list LIMIT 1")
        if result then
            local row = VH.SQLFetch(result)
            if row then
                record_result("sql_fetch", true, "SQLFetch works")
            else
                record_result("sql_fetch", true, "SQLFetch works (no rows)")
            end
            VH.SQLFree(result)
        else
            record_result("sql_fetch", false, "Query failed")
        end
    end)
    
    if not success then
        record_result("sql_fetch", false, "Fetch failed: " .. tostring(err))
        record_error("sql_fetch", err)
    end
end

-- Test: GetConfig/SetConfig
local function test_config_ops()
    if not VH or not VH.GetConfig then
        record_result("config_ops", false, "Config functions not available")
        return
    end
    
    local success, err = pcall(function()
        -- Try to read a config value
        local hub_name = VH.GetConfig("config", "hub_name")
        if hub_name then
            record_result("config_ops", true, "GetConfig works, hub_name=" .. tostring(hub_name))
        else
            record_result("config_ops", true, "GetConfig works (value is nil)")
        end
    end)
    
    if not success then
        record_result("config_ops", false, "Config op failed: " .. tostring(err))
        record_error("config_ops", err)
    end
end

-- Run all SQL tests
function RunSQLTests()
    test_select_query()
    test_sql_fetch()
    test_config_ops()
    return VH_SQL_TEST.results
end

-- Get test results
function GetSQLTestResults()
    return VH_SQL_TEST
end

-- Auto-run on load
RunSQLTests()

-- Report results
if VH and VH.SendToOpChat then
    local passed, failed = 0, 0
    for name, result in pairs(VH_SQL_TEST.results) do
        if result.passed then
            passed = passed + 1
        else
            failed = failed + 1
        end
    end
    VH.SendToOpChat("[LuaTest] SQL tests: " .. passed .. " passed, " .. failed .. " failed")
end
