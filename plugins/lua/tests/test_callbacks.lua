--[[
    Test Lua script for callback functions.
    
    This script tests that VH event callbacks work correctly.
    It tracks which callbacks are invoked during hub operation.
--]]

VH_CALLBACK_TEST = {
    callbacks_received = {},
    test_mode = true
}

-- Helper to track callback invocation
local function track_callback(name, ...)
    table.insert(VH_CALLBACK_TEST.callbacks_received, {
        name = name,
        time = os.time(),
        args = {...}
    })
end

-- User connection callbacks
function VH.OnUserConnected(nick, ip)
    track_callback("OnUserConnected", nick, ip)
    return 1  -- Allow
end

function VH.OnUserDisconnected(nick, ip)
    track_callback("OnUserDisconnected", nick, ip)
    return 1
end

function VH.OnNewConn(ip)
    track_callback("OnNewConn", ip)
    return 1
end

-- Chat callbacks
function VH.OnChat(nick, text)
    track_callback("OnChat", nick, text)
    
    -- Test command handling
    if text == "!luatest" then
        local count = #VH_CALLBACK_TEST.callbacks_received
        if VH and VH.SendToUser then
            VH.SendToUser("<Test> Received " .. count .. " callbacks so far", nick)
        end
    end
    
    return 1  -- Allow message
end

function VH.OnPM(nick, text, dest)
    track_callback("OnPM", nick, text, dest)
    return 1
end

function VH.OnMCTo(nick, text, dest)
    track_callback("OnMCTo", nick, text, dest)
    return 1
end

-- Operation callbacks
function VH.OnOperatorCommand(nick, data)
    track_callback("OnOperatorCommand", nick, data)
    return 1
end

function VH.OnUserCommand(nick, data)
    track_callback("OnUserCommand", nick, data)
    return 1
end

function VH.OnKick(op, nick, reason)
    track_callback("OnKick", op, nick, reason)
    return 1
end

function VH.OnBan(op, nick, reason)
    track_callback("OnBan", op, nick, reason)
    return 1
end

-- Timer callback
function VH.OnTimer()
    track_callback("OnTimer")
    return 1
end

-- Hub events
function VH.OnHubStartup()
    track_callback("OnHubStartup")
    return 1
end

-- Get callback statistics
function GetCallbackStats()
    local stats = {}
    for _, cb in ipairs(VH_CALLBACK_TEST.callbacks_received) do
        stats[cb.name] = (stats[cb.name] or 0) + 1
    end
    return stats
end

-- Get full callback log
function GetCallbackLog()
    return VH_CALLBACK_TEST.callbacks_received
end

-- Clear callback log
function ClearCallbackLog()
    VH_CALLBACK_TEST.callbacks_received = {}
end

-- Report to op chat
if VH and VH.SendToOpChat then
    VH.SendToOpChat("[LuaTest] Callback test script loaded - tracking callbacks")
end
