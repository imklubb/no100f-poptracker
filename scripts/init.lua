--Items
Tracker:AddItems("items/items.json")
-- Logic
ScriptHost:LoadScript("scripts/utils.lua")
ScriptHost:LoadScript("scripts/logic/logic.lua")

-- Maps
Tracker:AddMaps("maps/maps.json")
-- Locations
Tracker:AddLocations("locations/locations.json")

-- Layout
Tracker:AddLayouts("layouts/keys.json")
Tracker:AddLayouts("layouts/settings.json")
Tracker:AddLayouts("layouts/items.json")
Tracker:AddLayouts("layouts/tabs.json")
Tracker:AddLayouts("layouts/tokens.json")
Tracker:AddLayouts("layouts/snacks.json")
Tracker:AddLayouts("layouts/keyrings.json")
Tracker:AddLayouts("layouts/warps.json")
Tracker:AddLayouts("layouts/tracker.json")
Tracker:AddLayouts("layouts/broadcast.json")

-- AutoTracking for Poptracker
if PopVersion and PopVersion >= "0.18.0" then
	ScriptHost:LoadScript("scripts/autotracking.lua")
end
