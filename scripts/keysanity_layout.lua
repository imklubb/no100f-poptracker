-- Show only the key grid that matches the player's Keysanity setting.
--
-- PopTracker has no visibility rules for layout widgets (only locations have
-- those), so the grid cannot be hidden with a rule. What it does have:
-- Tracker:AddLayouts() replaces any layout name it redefines and then fires
-- onLayoutChanged, which makes TrackerView rebuild (keeping the active map tab).
--
-- So "active_keys_grid" is defined in three tiny files and we load whichever one
-- matches the setting:
--
--   off      -> layouts/active_none.json      an empty array, takes no space
--   keys     -> layouts/active_keys.json      pastes keys_grid
--   keyrings -> layouts/active_keyrings.json  pastes keyrings_grid
--
-- Keys and Keyrings are mutually exclusive in the apworld (include_keys is
-- 0 off / 1 keys / 2 keyrings), so only one is ever worth the screen space.

-- must match the file init.lua loads before tracker.json, or the first swap
-- would rebuild the view for no reason
local CURRENT = "none"

local function keyMode()
    -- the "Enable Keysanity" item is progressive: stage 2 sets BOTH
    -- op_keysanity_on_rings and op_keysanity_on, so test rings first
    if Tracker:ProviderCountForCode("op_keysanity_on_rings") > 0 then
        return "keyrings"
    elseif Tracker:ProviderCountForCode("op_keysanity_on") > 0 then
        return "keys"
    end
    return "none"
end

function updateKeyLayout()
    local mode = keyMode()
    if mode == CURRENT then return end   -- rebuilding the view is not cheap
    CURRENT = mode
    Tracker:AddLayouts("layouts/active_" .. mode .. ".json")
end

if ScriptHost and ScriptHost.AddWatchForCode then
    ScriptHost:AddWatchForCode("keysanity_layout_on", "op_keysanity_on",
        function() pcall(updateKeyLayout) end)
    ScriptHost:AddWatchForCode("keysanity_layout_rings", "op_keysanity_on_rings",
        function() pcall(updateKeyLayout) end)
end

-- sync once at load, in case a saved state or slot data already set the toggle
pcall(updateKeyLayout)
