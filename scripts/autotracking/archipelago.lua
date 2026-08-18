
require("scripts/autotracking/item_mapping")
require("scripts/autotracking/location_mapping")
require("scripts/autotracking/hints_mapping")

CUR_INDEX = -1
-- distinct AP item-indices that delivered a Scooby Snack. Persistent across
-- reconnects (NOT reset in onClear) so an interrupted re-sync can't lose count.
RECEIVED_SNACKS = {}
-- AP-logic snack count: "Scooby Snack" items only (feeds snackcount() gates)
AP_SNACKS = {}
--SLOT_DATA = nil

SLOT_DATA = {}

local highlight_lvl= {
    [0] = Highlight.Unspecified,
    [10] = Highlight.NoPriority,
    [20] = Highlight.Avoid,
    [30] = Highlight.Priority,
    [40] = Highlight.None,
}

-- Auto-tab: the AP client stores Scooby's current scene (e.g. "I001") at the
-- data-storage key NO100F_current_scene_T<team>_P<slot>. The scene's first
-- letter identifies the world, which maps 1:1 to one of the four map tabs.
SCENE_LETTER_TO_TAB = {
    B = "Mystic Manor",     -- Basement
    C = "Hedge Maze",       -- Cliffs
    E = "Hedge Maze",       -- Hedge Maze
    F = "Smuggler's Cove",  -- Fishing Village
    G = "Hedge Maze",       -- Graveyard
    H = "Hub",              -- Hub
    I = "Mystic Manor",     -- Mystic Manor
    L = "Smuggler's Cove",  -- Lighthouse
    O = "Mystic Manor",     -- Rooftops
    P = "Mystic Manor",     -- Secret Passages
    R = "Mystic Manor",     -- Balcony
    S = "Mystic Manor",     -- Super Secret Lab
    W = "Smuggler's Cove",  -- Shipwrecks
}

function autoTab(scene)
    if type(scene) ~= "string" or #scene < 1 then return end
    local toggle = Tracker:FindObjectForCode("auto_tab")
    if toggle and toggle.Active == false then return end   -- respects the toggle
    local tab = SCENE_LETTER_TO_TAB[scene:sub(1, 1):upper()]
    if tab then
        Tracker:UiHint("ActivateTab", tab)
    end
end

-- Follow Scooby: track the room Scooby is currently in so a marker can float
-- over it. Set by the scene notify; scoobyAt() drives each room marker's
-- visibility so only the current room's marker shows.
CURRENT_SCENE = ""
function updateScooby(scene)
    if type(scene) ~= "string" then return end
    CURRENT_SCENE = scene:upper()
    if forceUpdate then pcall(forceUpdate) end
end

function scoobyAt(scene)
    local t = Tracker:FindObjectForCode("follow_scooby")
    if t and t.Active == false then return 0 end
    return (CURRENT_SCENE == tostring(scene):upper()) and 1 or 0
end

-- Reachable-snack counters: keep each area's square "Scooby Snacks" counter set to
-- the number of snacks there you can currently reach and haven't collected yet.
COLLECTED = {}
COUNTERS_DIRTY = false
function markCountersDirty() COUNTERS_DIRTY = true end
function updateSnackCounters()
    COUNTERS_DIRTY = false
    if not SNACK_BY_REGION then return end
    -- global total: count of distinct received Scooby Snack items (keyed by AP item
    -- index, so re-sends dedupe and reconnects can't lose or double-count)
    local received = 0
    for _, v in pairs(RECEIVED_SNACKS) do received = received + v end
    local sobj = Tracker:FindObjectForCode("scoobysnack")
    if sobj then pcall(function() sobj.AcquiredCount = received end) end
    local apn = 0
    for _ in pairs(AP_SNACKS) do apn = apn + 1 end
    local aobj = Tracker:FindObjectForCode("ap_snack_logic")
    if aobj and aobj.AcquiredCount ~= apn then pcall(function() aobj.AcquiredCount = apn end) end
    for region, snacks in pairs(SNACK_BY_REGION) do
        local n = 0
        for _, e in ipairs(snacks) do
            if not COLLECTED[e[1]] and snackReachable(e[2], e[3]) then n = n + 1 end
        end
        local obj = Tracker:FindObjectForCode("@"..region.."/Scooby Snacks")
        if obj then pcall(function() obj.AvailableChestCount = n end) end
    end
end
if ScriptHost and ScriptHost.AddOnFrameHandler then
    SNACK_FRAME = 0
    ScriptHost:AddOnFrameHandler("snack_counters", function()
        SNACK_FRAME = SNACK_FRAME + 1
        if COUNTERS_DIRTY or (SNACK_FRAME % 20 == 0) then
            pcall(updateSnackCounters)
        end
    end)
end

-- Apply the seed's YAML options to the tracker's setting toggles on connect,
-- reading them from slot_data so the player doesn't set them by hand. The
-- setting items are "progressive", so each value maps straight to a stage:
--   include_keys           0 off / 1 keys / 2 keyrings
--   include_monster_tokens 0 off / 1 on
--   include_warpgates      0 off / 1 on
--   include_snacks         0 off / 1 on
function applySettings(slot_data)
    if type(slot_data) ~= "table" then
        print("applySettings: slot_data is not a table (got "..type(slot_data)..")")
        return
    end
    -- coerce whatever the server sent (int, bool, or string) into a stage index
    local function toStage(v)
        if type(v) == "boolean" then return v and 1 or 0 end
        if type(v) == "number"  then return math.floor(v) end
        if type(v) == "string"  then return tonumber(v) end
        return nil
    end
    local map = {
        {"keysanity",   "include_keys"},
        {"tokensanity", "include_monster_tokens"},
        {"warprando",   "include_warpgates"},
        {"snacksanity", "include_snacks"},
    }
    for _, pair in ipairs(map) do
        local code, key = pair[1], pair[2]
        local stage = toStage(slot_data[key])
        local obj = Tracker:FindObjectForCode(code)
        if not obj then
            print(string.format("applySettings: item '%s' not found", code))
        elseif stage == nil then
            print(string.format("applySettings: '%s' absent from slot_data (raw=%s)", key, tostring(slot_data[key])))
        else
            obj.CurrentStage = stage
        end
    end
    -- completion goal + the counts it needs (all straight from slot_data)
    local goal = toStage(slot_data["completion_goal"])
    local gobj = Tracker:FindObjectForCode("goal_type")
    if gobj and goal ~= nil then gobj.CurrentStage = goal end
    local counts = {
        {"goal_boss_count",  "boss_count",  3},
        {"goal_token_count", "token_count", 21},
        {"goal_snack_count", "snack_count", 850},
    }
    for _, pair in ipairs(counts) do
        local v = toStage(slot_data[pair[2]])
        local obj = Tracker:FindObjectForCode(pair[1])
        if obj then obj.AcquiredCount = (v ~= nil and v > 0) and v or pair[3] end
    end
    -- skip/difficulty options are plain on/off toggles
    local toggles = {
        {"op_advanced_on", "advanced_logic"},
        {"op_expert_on",   "expert_logic"},
        {"op_creepy_on",   "creepy_early"},
    }
    for _, pair in ipairs(toggles) do
        local v = toStage(slot_data[pair[2]])
        local obj = Tracker:FindObjectForCode(pair[1])
        if obj and v ~= nil then obj.Active = (v ~= 0) end
    end
end

-- Re-apply once on the next frame too, after the tracker turns BulkUpdate back
-- off, so the change can't be swallowed by the post-clear refresh.
function scheduleApplySettings(slot_data)
    local name = "applySettings_deferred"
    ScriptHost:AddOnFrameHandler(name, function()
        ScriptHost:RemoveOnFrameHandler(name)
        local ok, err = pcall(applySettings, slot_data)
        if not ok then print("applySettings (deferred) error: "..tostring(err)) end
        if forceUpdate then pcall(forceUpdate) end
    end)
end

-- Re-assert settings on the first item/location event after a clear. PopTracker
-- can restore autosaved item state (your manual toggles) *after* onClear runs,
-- which would clobber the auto-applied settings; the replay of items/locations
-- happens after that, so re-applying here makes them stick.
function reapplyPendingSettings()
    if PENDING_SETTINGS ~= nil then
        local sd = PENDING_SETTINGS
        PENDING_SETTINGS = nil
        pcall(applySettings, sd)
        if forceUpdate then pcall(forceUpdate) end
    end
end

function has_value (t, val)
    for i, v in ipairs(t) do
        if v == val then return 1 end
    end
    return 0
end

function dump_table(o, depth)
    if depth == nil then
        depth = 0
    end
    if type(o) == 'table' then
        local tabs = ('\t'):rep(depth)
        local tabs2 = ('\t'):rep(depth + 1)
        local s = '{'
        for k, v in pairs(o) do
            if type(k) ~= 'number' then
                k = '"' .. k .. '"'
            end
            s = s .. tabs2 .. '[' .. k .. '] = ' .. dump_table(v, depth + 1) .. ','
        end
        return s .. tabs .. '}'
    else
        return tostring(o)
    end
end

-- Watch callback re-registered after each clear. PopTracker re-evaluates access
-- rules and section counts on its own; this just needs to exist so the "*" watch
-- has a valid callback (a nil callback errors on every item change).
function StateChange(code)
end

function forceUpdate()
    local update = Tracker:FindObjectForCode("update")
    update.Active = not update.Active
end

function onClearHandler(slot_data)
    local clear_timer = os.clock()
    
    ScriptHost:RemoveWatchForCode("StateChange")
    -- Disable tracker updates.
    Tracker.BulkUpdate = true
    -- Use a protected call so that tracker updates always get enabled again, even if an error occurred.
    local ok, err = pcall(onClear, slot_data)
    -- Enable tracker updates again.
    if ok then
        -- Defer re-enabling tracker updates until the next frame, which doesn't happen until all received items/cleared
        -- locations from AP have been processed.
        local handlerName = "AP onClearHandler"
        local function frameCallback()
            ScriptHost:AddWatchForCode("StateChange", "*", StateChange)
            ScriptHost:RemoveOnFrameHandler(handlerName)
            Tracker.BulkUpdate = false
            forceUpdate()
            print(string.format("Time taken total: %.2f", os.clock() - clear_timer))
        end
        ScriptHost:AddOnFrameHandler(handlerName, frameCallback)
    else
        Tracker.BulkUpdate = false
        print("Error: onClear failed:")
        print(err)
    end
end

function onClear(slot_data)
    --SLOT_DATA = slot_data
    CUR_INDEX = -1
    -- reset locations
    for _, location_array in pairs(LOCATION_MAPPING) do
        for _, location in pairs(location_array) do
            if location then
                local location_obj = Tracker:FindObjectForCode(location)
                if location_obj then
                    if location:sub(1, 1) == "@" then
                        location_obj.AvailableChestCount = location_obj.ChestCount
                    else
                        location_obj.Active = false
                    end
                end
            end
        end
    end
    -- reset items
    for _, item_pair in pairs(ITEM_MAPPING) do
        for _, code_pair in pairs(item_pair) do
            local item_code = code_pair[1]
            local item_obj = Tracker:FindObjectForCode(item_code)
            if item_obj then
                if item_obj.Type == "toggle" then
                    item_obj.Active = false
                elseif item_obj.Type == "progressive" then
                    item_obj.CurrentStage = 0
                    item_obj.Active = false
                elseif item_obj.Type == "consumable" then
                    if item_obj.MinCount then
                        item_obj.AcquiredCount = item_obj.MinCount
                    else
                        item_obj.AcquiredCount = 0
                    end
                elseif item_obj.Type == "progressive_toggle" then
                    item_obj.CurrentStage = 0
                    item_obj.Active = false
                end
            end
        end
    end
    PLAYER_ID = Archipelago.PlayerNumber or -1
    TEAM_NUMBER = Archipelago.TeamNumber or 0
    SLOT_DATA = slot_data
    -- Subscribe to hints + current-scene FIRST, so auto-tab works even if the
    -- settings step below runs into trouble.
    if Archipelago.PlayerNumber > -1 then

        HINTS_ID = "_read_hints_"..TEAM_NUMBER.."_"..PLAYER_ID
        Archipelago:SetNotify({HINTS_ID})
        Archipelago:Get({HINTS_ID})

        SCENE_KEY = "NO100F_current_scene_T"..TEAM_NUMBER.."_P"..PLAYER_ID
        Archipelago:SetNotify({SCENE_KEY})
        Archipelago:Get({SCENE_KEY})
    end
    -- apply the YAML options to the tracker's setting toggles automatically
    PENDING_SETTINGS = slot_data
    local ok, err = pcall(applySettings, slot_data)
    if not ok then print("applySettings error: "..tostring(err)) end
    scheduleApplySettings(slot_data)
    COLLECTED = {}
    markCountersDirty()
end

function onItem(index, item_id, item_name, player_number)
    reapplyPendingSettings()
    -- Snack total (matches the game): Scooby Snack (1495105) & Spare Snack (1495104)
    -- count 1, Snack Box (1495106) counts 5. Keyed by AP index (dedupes re-sends), and
    -- recorded BEFORE the index guard so nothing is skipped.
    if item_id == 1495104 or item_id == 1495105 then
        RECEIVED_SNACKS[index] = 1; markCountersDirty()
    elseif item_id == 1495106 then
        RECEIVED_SNACKS[index] = 5; markCountersDirty()
    end
    -- AP logic uses only "Scooby Snack" items (state.has(ItemNames.Snack, n)),
    -- which is a different number than the game's on-screen snack total above.
    if item_id == 1495105 then
        AP_SNACKS[index] = true; markCountersDirty()
    end
    if index <= CUR_INDEX then
        return
    end
    local is_local = player_number == Archipelago.PlayerNumber
    CUR_INDEX = index;
    local item = ITEM_MAPPING[item_id]
    if not item or not item[1] then
        --print(string.format("onItem: could not find item mapping for id %s", item_id))
        return
    end
    for _, item_pair in pairs(item) do
        item_code = item_pair[1]
        item_type = item_pair[2]
        local item_obj = Tracker:FindObjectForCode(item_code)
        if item_obj then
            if item_obj.Type == "toggle" then
                -- print("toggle")
                item_obj.Active = true
            elseif item_obj.Type == "progressive" then
                -- print("progressive")
                item_obj.Active = true
            elseif item_obj.Type == "consumable" then
                -- print("consumable")
                item_obj.AcquiredCount = item_obj.AcquiredCount + item_obj.Increment * (tonumber(item_pair[3]) or 1)
            elseif item_obj.Type == "progressive_toggle" then
                -- print("progressive_toggle")
                if item_obj.Active then
                    item_obj.CurrentStage = item_obj.CurrentStage + 1
                else
                    item_obj.Active = true
                end
            end
        else
            print(string.format("onItem: could not find object for code %s", item_code[1]))
        end
    end
    markCountersDirty()
end

--called when a location gets cleared
function onLocation(location_id, location_name)
    reapplyPendingSettings()
    local location_array = LOCATION_MAPPING[location_id]
    if not location_array or not location_array[1] then
        print(string.format("onLocation: could not find location mapping for id %s", location_id))
        return
    end

    for _, location in pairs(location_array) do
        local location_obj = Tracker:FindObjectForCode(location)
        -- print(location, location_obj)
        if location_obj then
            if location:sub(1, 1) == "@" then
                location_obj.AvailableChestCount = location_obj.AvailableChestCount - 1
                if location:find(" Snacks/") then COLLECTED[location_id] = true end
            else
                location_obj.Active = true
            end
        else
            print(string.format("onLocation: could not find location_object for code %s", location))
        end
    end
    markCountersDirty()
end

function onEvent(key, value, old_value)
    updateEvents(value)
end

function onEventsLaunch(key, value)
    updateEvents(value)
end

-- this Autofill function is meant as an example on how to do the reading from slotdata and mapping the values to 
-- your own settings
-- function autoFill()
--     if SLOT_DATA == nil  then
--         print("its fucked")
--         return
--     end
--     -- print(dump_table(SLOT_DATA))

--     mapToggle={[0]=0,[1]=1,[2]=1,[3]=1,[4]=1}
--     mapToggleReverse={[0]=1,[1]=0,[2]=0,[3]=0,[4]=0}
--     mapTripleReverse={[0]=2,[1]=1,[2]=0}

--     slotCodes = {
--         map_name = {code="", mapping=mapToggle...}
--     }
--     -- print(dump_table(SLOT_DATA))
--     -- print(Tracker:FindObjectForCode("autofill_settings").Active)
--     if Tracker:FindObjectForCode("autofill_settings").Active == true then
--         for settings_name , settings_value in pairs(SLOT_DATA) do
--             -- print(k, v)
--             if slotCodes[settings_name] then
--                 item = Tracker:FindObjectForCode(slotCodes[settings_name].code)
--                 if item.Type == "toggle" then
--                     item.Active = slotCodes[settings_name].mapping[settings_value]
--                 else 
--                     -- print(k,v,Tracker:FindObjectForCode(slotCodes[k].code).CurrentStage, slotCodes[k].mapping[v])
--                     item.CurrentStage = slotCodes[settings_name].mapping[settings_value]
--                 end
--             end
--         end
--     end
-- end

function onNotify(key, value, old_value)
    print("onNotify", key, value, old_value)
    if value ~= old_value and key == HINTS_ID then
        for _, hint in ipairs(value) do
            if hint.finding_player == Archipelago.PlayerNumber then
                updateHints(hint.location, hint.status)
            end
        end
    elseif key == SCENE_KEY then
        autoTab(value)
        updateScooby(value)
    end
end

function onNotifyLaunch(key, value)
    print("onNotifyLaunch", key, value)
    if key == HINTS_ID then
        for _, hint in ipairs(value) do
            -- print("hint", hint, hint.found)
            -- print(dump_table(hint))
            if hint.finding_player == Archipelago.PlayerNumber then
                updateHints(hint.location, hint.status)
            end
        end
    elseif key == SCENE_KEY then
        autoTab(value)
        updateScooby(value)
    end
end

function updateHints(locationID, status) -->
    local location_table = LOCATION_MAPPING[locationID]
    for _, location in ipairs(location_table) do
        local obj = Tracker:FindObjectForCode(location)
        if obj then
            obj.Highlight = highlight_lvl[status]
        else
            print(string.format("No object found for code: %s", location))
        end
    end
    -- local item_codes = HINTS_MAPPING[locationID]
    -- 
    -- for _, item_table in ipairs(item_codes, clear) do
    --     for _, item_code in ipairs(item_table) do
    --         local obj = Tracker:FindObjectForCode(item_code)
    --         if obj then
    --             if not clear then
    --                 obj.Active = true
    --             else
    --                 obj.Active = false
    --             end
    --         else
    --             print(string.format("No object found for code: %s", item_code))
    --         end
    --     end
    -- end
end


-- ScriptHost:AddWatchForCode("settings autofill handler", "autofill_settings", autoFill)
Archipelago:AddClearHandler("clear handler", onClearHandler)
Archipelago:AddItemHandler("item handler", onItem)
Archipelago:AddLocationHandler("location handler", onLocation)

Archipelago:AddSetReplyHandler("notify handler", onNotify)
Archipelago:AddRetrievedHandler("notify launch handler", onNotifyLaunch)



--doc
--hint layout
-- {
--     ["receiving_player"] = 1,
--     ["class"] = Hint,
--     ["finding_player"] = 1,
--     ["location"] = 67361,
--     ["found"] = false,
--     ["item_flags"] = 2,
--     ["entrance"] = ,
--     ["item"] = 66062,
-- } 
