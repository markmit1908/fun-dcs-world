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
