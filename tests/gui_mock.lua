--[[
Minimal stand-in for the DCS GameGUI state, enough to load
LossTrackerGameGUI.lua under plain Lua 5.1. Records what the hook does in
GuiMock. Set GuiMock.writedir before loading the hook.
]]

GuiMock = {
    debriefing = {},
    log = {},
    callbacks = nil,
    modelTime = 0,
    multiplayer = false,
    screen = { 1920, 1080 },
    windows = {},
    doScripts = {},
}

log = { INFO = 1, ERROR = 2 }
function log.write(subsystem, level, message)
    table.insert(GuiMock.log, { level = level, message = message })
end

Sim = {}
function Sim.setUserCallbacks(callbacks) GuiMock.callbacks = callbacks end
function Sim.writeDebriefing(text) table.insert(GuiMock.debriefing, text) end
function Sim.getMissionName() return "channel_drone_gunnery_low" end
function Sim.getModelTime() return GuiMock.modelTime end
function Sim.isMultiplayer() return GuiMock.multiplayer end
GuiMock.paused = false
function Sim.getPause() return GuiMock.paused end
function Sim.setPause(paused) GuiMock.paused = paused end

lfs = {}
function lfs.writedir() return GuiMock.writedir end

-- dxgui widgets ------------------------------------------------------------

local Widget = {}
Widget.__index = Widget

local function newWidget(kind, name)
    return setmetatable({ kind = kind, name = name, text = "", visible = true,
        x = 0, y = 0, w = 100, h = 20, children = {} }, Widget)
end

function Widget:clone()
    local copy = newWidget(self.kind, self.name)
    copy.w, copy.h = self.w, self.h
    return copy
end
function Widget:setText(text) self.text = text end
function Widget:getText() return self.text end
function Widget:setVisible(visible) self.visible = visible end
function Widget:setPosition(x, y) self.x, self.y = x, y end
function Widget:setSize(w, h) self.w, self.h = w, h end
function Widget:getSize() return self.w, self.h end
function Widget:calcSize()
    local count = 1
    for _ in self.text:gmatch("\n") do count = count + 1 end
    return self.w, 18 * count
end
function Widget:insertWidget(widget) table.insert(self.children, widget) end
function Widget:removeAllWidgets() self.children = {} end

package.preload["dxgui"] = function()
    return { GetWindowSize = function() return GuiMock.screen[1], GuiMock.screen[2] end }
end

package.preload["DialogLoader"] = function()
    return {
        spawnDialogFromFile = function(path, cdata)
            local window = newWidget("Window", path)
            window.visible = false
            window.text = cdata.title
            window.w, window.h = 1280, 768
            window.cbDontShow = newWidget("CheckBox", "cbDontShow")
            window.contentScroll = newWidget("ScrollPane", "contentScroll")
            window.templateWidgets = {
                titleText = newWidget("Static", "titleText"),
                paragraphText = newWidget("EditBox", "paragraphText"),
            }
            table.insert(GuiMock.windows, window)
            return window
        end,
    }
end

-- Stands in for the mission scripting state reached through a_do_script.
GuiMock.trackerLoaded = true
function a_do_script(code)
    table.insert(GuiMock.doScripts, code)
    return GuiMock.trackerLoaded and "ok" or "no"
end

-- Extra widget methods used by the debrief panel.
function Widget:setBounds(x, y, w, h) self.x, self.y, self.w, self.h = x, y, w, h end
function Widget:getBounds() return self.x, self.y, self.w, self.h end
function Widget:getSkin() return self.skin end
function Widget:setSkin(skin) self.skin = skin end

package.preload["Panel"] = function()
    return { new = function(text) return newWidget("Panel", text) end }
end
package.preload["Static"] = function()
    return { new = function(text) local w = newWidget("Static"); w.text = text or ""; return w end }
end

-- A stand-in for DCS's 'debriefing' module (Scripts/UI/debriefing.lua):
-- window_ is a local reached only as an upvalue of isVisible(), as in DCS.
function GuiMock.installDebriefing(createNow)
    local window_
    local debriefing = {}
    function debriefing.create()
        window_ = newWidget("Window", "sim_debrief")
        window_.visible = false
        local main = newWidget("Panel", "containerMain")
        main.pTop = newWidget("Panel", "pTop")
        main.pDown = newWidget("Panel", "pDown")
        main.pDown.btnExit = newWidget("Button", "btnExit")
        main.pGrid = newWidget("Panel", "pGrid")
        main.pGrid:setBounds(0, 349, 1280, 378)
        main.pGrid.skin = "gridPanelSkin"
        main.pNoVisible = newWidget("Panel", "pNoVisible")
        main.pNoVisible.staticCell = newWidget("Static", "staticCell")
        main.pNoVisible.staticCell.skin = "cellSkin"
        window_.containerMain = main
        return window_
    end
    function debriefing.isVisible() return window_ and window_.visible end
    function debriefing.show(visible)
        if not window_ then return end
        window_.visible = visible
    end
    function debriefing.window() return window_ end
    if createNow then debriefing.create() end
    package.loaded["debriefing"] = debriefing
    return debriefing
end

-- DCS's per-frame UI updater: functions run each frame until they return true.
GuiMock.updaters = {}
package.preload["UpdateManager"] = function()
    return { add = function(fn) table.insert(GuiMock.updaters, fn) end }
end
function GuiMock.runFrames(count)
    for _ = 1, count do
        local remaining = {}
        for _, fn in ipairs(GuiMock.updaters) do
            if not fn() then table.insert(remaining, fn) end
        end
        GuiMock.updaters = remaining
    end
end

-- Wall clock for the hook's give-up timer.
GuiMock.clock = 1000
os.time = function() return GuiMock.clock end
