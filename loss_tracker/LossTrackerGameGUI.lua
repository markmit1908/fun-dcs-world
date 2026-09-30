--[[
LossTrackerGameGUI.lua: DCS GameGUI hook for loss_tracker.lua.

Install into Saved Games\DCS\Scripts\Hooks (install_missions.py does it). It
runs in DCS's GUI Lua state, which can draw windows and write files, unlike
mission scripts. It:

- shows the tracker's MISSION COMPLETE / OUT OF AMMO / MISSION FAILED summary
  in a real centred window (reusing DCS's own ImportantNoticeDialog layout),
  pausing single-player missions until the window is closed,
- writes each loss and every summary line into the mission's debriefing log
  as "comment" events (one per line),
- appends every summary to Saved Games\DCS\Logs\LossTracker.log.

The post-mission debrief is DCS's web-based main menu, which can't be
extended; the mission result there comes from the mission goals that
add_loss_tracker() adds.

The tracker's on-screen messages arrive through onTriggerMessage, so no
unsafe APIs are needed. In single player the hook also tells the tracker it is
installed, so the summary text message only flashes briefly behind the window.
Without this hook, the tracker works as before with text messages only.
]]

local ok, loadError = pcall(function()

local SimAPI = Sim or DCS  -- renamed from DCS.* to Sim.* in newer versions
local SUBSYSTEM = "LossTracker"

local function logInfo(message) log.write(SUBSYSTEM, log.INFO, message) end
local function logError(message) log.write(SUBSYSTEM, log.ERROR, message) end

package.path = package.path .. ";.\\Scripts\\?.lua;.\\Scripts\\UI\\?.lua;"

local hook = {}
local window, templates
local pausedByHook = false
local missionTold, nextTellTime = false, 0

local function lines(text)
    local result = {}
    for line in (text .. "\n"):gmatch("(.-)\r?\n") do table.insert(result, line) end
    return result
end

-- Summary messages start with "==========  TITLE  ==========". Returns the
-- title and the rest split into sections at blank lines.
local function parseSummary(message)
    local title = message:match("^==========  (.-)  ==========")
    if title == nil then return nil end
    local sections, current = {}, {}
    local all = lines(message)
    for i = 2, #all do
        if all[i] == "" then
            if #current > 0 then table.insert(sections, current) end
            current = {}
        else
            table.insert(current, all[i])
        end
    end
    if #current > 0 then table.insert(sections, current) end
    return { title = title, sections = sections }
end

-- Loss messages are one line. The live status block repeats recent losses,
-- so a multi-line message containing one must not count as a loss.
local function isLossMessage(message)
    if message:find("\n") then return false end
    return message:find(" %- credited to ") ~= nil or message:find(" %- no credit$") ~= nil
end

local function createWindow()
    local DialogLoader = require("DialogLoader")
    local dxgui = require("dxgui")
    window = DialogLoader.spawnDialogFromFile("./Scripts/UI/ImportantNoticeDialog.dlg",
        { title = "Mission summary", dontShowUntilNextUpdate = "" })
    templates = window.templateWidgets
    window.cbDontShow:setVisible(false)
    window.onClose = function() hook.closeWindow() end

    local screenW, screenH = dxgui.GetWindowSize()
    local w, h = math.min(1100, screenW - 40), math.min(620, screenH - 40)
    window:setSize(w, h)
    window:setPosition((screenW - w) / 2, (screenH - h) / 2)
    window.contentScroll:setSize(w - 30, h - 80)
end

local function showWindow(summary)
    if window == nil then createWindow() end
    pcall(function() window:setText(summary.title) end)

    local scroll = window.contentScroll
    scroll:removeAllWidgets()
    local y = 10
    local function add(template, text, fit)
        local widget = template:clone()
        scroll:insertWidget(widget)
        widget:setPosition(0, y)
        widget:setText(text)
        if fit then widget:setSize(widget:calcSize()) end
        local _, height = widget:getSize()
        y = y + height + 8
    end

    -- First section (subtitle, time) and a trailing footer as headings; the
    -- tally and the per-aircraft list as plain text.
    local count = #summary.sections
    for i, section in ipairs(summary.sections) do
        local heading = i == 1 or (i == count and count > 2)
        if heading then
            for _, line in ipairs(section) do add(templates.titleText, line, false) end
        else
            add(templates.paragraphText, table.concat(section, "\n"), true)
        end
    end
    window:setVisible(true)
end

-- Pause while the summary is up, single player only: in multiplayer this
-- would freeze the server for everyone.
local function pauseForWindow()
    if SimAPI.isMultiplayer() or SimAPI.getPause() then return end
    SimAPI.setPause(true)
    pausedByHook = true
end

local function hideWindow()
    if window then window:setVisible(false) end
end

-- Close button: hide and resume if the hook paused the simulation.
function hook.closeWindow()
    hideWindow()
    if pausedByHook then
        pausedByHook = false
        SimAPI.setPause(false)
    end
end

local function writeDebriefing(text)
    local okWrite, err = pcall(SimAPI.writeDebriefing, text)
    if not okWrite then logError("writeDebriefing failed: " .. tostring(err)) end
end

-- Each writeDebriefing() call becomes one "comment" event in the debriefing
-- log. Debrief event lists show one line per event, so write one per line.
local function writeDebriefingLines(text)
    for _, line in ipairs(lines(text)) do
        local trimmed = line:match("^%s*(.-)%s*$")
        if trimmed ~= "" then writeDebriefing(trimmed) end
    end
end

local function appendHistory(text)
    local path = lfs.writedir() .. "Logs\\LossTracker.log"
    local file = io.open(path, "a")
    if file == nil then
        logError("can't open " .. path)
        return
    end
    -- The tracker names the mission in the summary; DCS itself only knows the
    -- running copy as "tempMission".
    local mission = text:match("\nMission: ([^\n]+)")
    if mission == nil then
        mission = "?"
        pcall(function() mission = SimAPI.getMissionName() end)
    end
    file:write(os.date("%Y-%m-%d %H:%M:%S"), "  ", mission, "\n", text, "\n\n")
    file:close()
end

-- Tell the tracker (single player only: in multiplayer it runs on the server,
-- whose players may not have this hook) that a window will show summaries.
local function tellMission()
    local code = "if LossTracker then LossTracker.hookPresent = true return 'ok' end return 'no'"
    if type(a_do_script) == "function" then
        local okCall, result = pcall(a_do_script, code)
        if okCall and result == "ok" then return true end
    end
    return false
end

function hook.onTriggerMessage(message, duration, clearView)
    if type(message) ~= "string" then return end
    local summary = parseSummary(message)
    if summary then
        local okShow, err = pcall(showWindow, summary)
        if okShow then
            local okPause, pauseErr = pcall(pauseForWindow)
            if not okPause then logError("pause failed: " .. tostring(pauseErr)) end
        else
            logError("window failed: " .. tostring(err))
        end
        writeDebriefingLines(message)
        appendHistory(message)
    elseif isLossMessage(message) then
        writeDebriefing("LossTracker: " .. message)
    end
end

function hook.onSimulationFrame()
    if missionTold then return end
    local now = SimAPI.getModelTime()
    if now < nextTellTime then return end
    nextTellTime = now + 5
    if SimAPI.isMultiplayer() then
        missionTold = true
        return
    end
    missionTold = tellMission()
end

function hook.onSimulationStart()
    missionTold, nextTellTime = false, 0
end

function hook.onSimulationStop()
    hideWindow()
    pausedByHook = false
end

hook._test = { parseSummary = parseSummary, isLossMessage = isLossMessage }

SimAPI.setUserCallbacks(hook)
logInfo("hook loaded")
LossTrackerGameGUI = hook

end)

if not ok then
    log.write("LossTracker", log.ERROR, "hook failed to load: " .. tostring(loadError))
end
