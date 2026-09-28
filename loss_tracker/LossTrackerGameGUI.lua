--[[
LossTrackerGameGUI.lua: DCS GameGUI hook for loss_tracker.lua.

Install into Saved Games\DCS\Scripts\Hooks (install_missions.py does it). It
runs in DCS's GUI Lua state, which can draw windows and write files, unlike
mission scripts. It:

- shows the tracker's MISSION COMPLETE / OUT OF AMMO / MISSION FAILED summary
  in a real centred window (reusing DCS's own ImportantNoticeDialog layout),
  pausing single-player missions until the window is closed,
- writes each loss and every summary line into the mission's debriefing file,
  where they appear as "comment" rows in the debrief screen's event list,
- appends every summary to Saved Games\DCS\Logs\LossTracker.log,
- adds a LOSS TRACKER report to the post-mission debrief screen.

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

local function isLossMessage(message)
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

-- Each writeDebriefing() call becomes a "comment" row in the debrief
-- screen's event list, whose rows are one line high, so write one per line.
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

-- Debrief screen panel --------------------------------------------------------
-- DCS draws the post-mission debrief with Scripts/UI/debriefing.lua (the
-- 'debriefing' module in this Lua state). There's no API for adding to it, so
-- this reaches into that module: a LOSS TRACKER button in the empty right-hand
-- part of the top bar swaps the event grid for the report. All of it is
-- guarded; if a DCS update changes the module, the debrief opens as normal and
-- the error goes to dcs.log.

local report = { summary = nil, losses = {} }
local panel = {}
local REPORT_LINE_HEIGHT = 20

local function reportLines()
    if report.summary then return lines(report.summary) end
    if #report.losses == 0 then return nil end
    local result = { "Loss tracker", "No end-of-mission summary: the mission ended early.", "", "Losses:" }
    for _, loss in ipairs(report.losses) do table.insert(result, "  " .. loss) end
    return result
end

local function debriefWindow(debriefing)
    if type(debriefing.isVisible) ~= "function" then return nil end
    for i = 1, 50 do
        local name, value = debug.getupvalue(debriefing.isVisible, i)
        if name == nil then return nil end
        if name == "window_" then return value end
    end
    return nil
end

local function setReportVisible(visible)
    panel.report:setVisible(visible)
    panel.grid:setVisible(not visible)
    panel.button:setText(visible and "EVENT LOG" or "LOSS TRACKER")
    panel.showingReport = visible
end

local function buildPanel(window)
    local Panel = require("Panel")
    local main = window.containerMain
    local button = main.pDown.btnExit:clone()
    main.pTop:insertWidget(button)
    button:setBounds(1050, 10, 180, 30)
    button.onChange = function() setReportVisible(not panel.showingReport) end

    local reportPanel = Panel.new()
    reportPanel:setSkin(main.pGrid:getSkin())
    reportPanel:setBounds(main.pGrid:getBounds())
    main:insertWidget(reportPanel)

    panel = {
        window = window,
        button = button,
        report = reportPanel,
        grid = main.pGrid,
        cellSkin = main.pNoVisible.staticCell:getSkin(),
        showingReport = false,
    }
end

local function updateDebriefPanel(debriefing)
    local window = debriefWindow(debriefing)
    if window == nil then return false end
    if panel.window ~= window then buildPanel(window) end

    local content = reportLines()
    panel.button:setVisible(content ~= nil)
    if content == nil then
        setReportVisible(false)
        return true
    end

    local Static = require("Static")
    local _, _, w, h = panel.report:getBounds()
    local maxLines = math.floor((h - 16) / REPORT_LINE_HEIGHT)
    if #content > maxLines then
        local shown = {}
        for i = 1, maxLines - 1 do shown[i] = content[i] end
        shown[maxLines] = "... the rest is in the event log (filter the Event column by comment)"
        content = shown
    end

    panel.report:removeAllWidgets()
    for i, line in ipairs(content) do
        local cell = Static.new(line)
        cell:setSkin(panel.cellSkin)
        cell:setBounds(15, 8 + (i - 1) * REPORT_LINE_HEIGHT, w - 30, REPORT_LINE_HEIGHT)
        panel.report:insertWidget(cell)
    end
    setReportVisible(true)
    return true
end

local function refreshDebrief(debriefing)
    local okUpdate, err = pcall(updateDebriefPanel, debriefing)
    if not okUpdate then logError("debrief panel failed: " .. tostring(err)) end
end

local function patchDebriefing()
    local debriefing = package.loaded["debriefing"]
    if type(debriefing) ~= "table" then return nil end
    if debriefing.lossTrackerPatched then return debriefing end

    local originalShow, originalCreate = debriefing.show, debriefing.create
    if type(originalShow) ~= "function" then return nil end
    debriefing.show = function(visible, ...)
        local result = originalShow(visible, ...)
        if visible then refreshDebrief(debriefing) end
        return result
    end
    if type(originalCreate) == "function" then
        debriefing.create = function(...)
            local window = originalCreate(...)
            refreshDebrief(debriefing)
            return window
        end
    end
    debriefing.lossTrackerPatched = true
    logInfo("debrief panel installed")
    return debriefing
end

-- Called whenever there's something new to show, in case DCS calls the
-- debrief module's functions directly rather than through the patched table.
local function syncDebrief()
    local okPatch, debriefing = pcall(patchDebriefing)
    if not okPatch then
        logError("debrief patch failed: " .. tostring(debriefing))
    elseif debriefing then
        refreshDebrief(debriefing)
    end
end

-- DCS loads and opens the debrief after the simulation stops, when no hook
-- callback runs any more. So poll on DCS's UI update loop until the debrief
-- is visible, fill it in, then stop. Gives up (and logs what it saw) after
-- DEBRIEF_WAIT_S.
local DEBRIEF_WAIT_S = 300

local function debriefVisible()
    local debriefing = package.loaded["debriefing"]
    if type(debriefing) ~= "table" or type(debriefing.isVisible) ~= "function" then return false end
    local okVisible, visible = pcall(debriefing.isVisible)
    return okVisible and visible and true or false
end

local function watchForDebrief()
    if reportLines() == nil then return end
    local okRequire, UpdateManager = pcall(require, "UpdateManager")
    if not okRequire or type(UpdateManager) ~= "table" or type(UpdateManager.add) ~= "function" then
        logError("debrief watch unavailable: " .. tostring(UpdateManager))
        return
    end
    local deadline = os.time() + DEBRIEF_WAIT_S
    UpdateManager.add(function()
        local okStep, done = pcall(function()
            if debriefVisible() then
                syncDebrief()
                logInfo("debrief panel filled")
                return true
            end
            if os.time() > deadline then
                local debriefing = package.loaded["debriefing"]
                logError(string.format(
                    "debrief screen not found after %d s: package.loaded.debriefing is %s, isVisible is %s",
                    DEBRIEF_WAIT_S, type(debriefing),
                    type(debriefing) == "table" and type(debriefing.isVisible) or "n/a"))
                return true
            end
            return false
        end)
        if not okStep then
            logError("debrief watch failed: " .. tostring(done))
            return true
        end
        return done
    end)
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
        report.summary = message
        syncDebrief()
    elseif isLossMessage(message) then
        writeDebriefing("LossTracker: " .. message)
        table.insert(report.losses, message)
        syncDebrief()
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
    report = { summary = nil, losses = {} }
    syncDebrief()
end

function hook.onSimulationStop()
    hideWindow()
    pausedByHook = false
    syncDebrief()
    watchForDebrief()
end

hook._test = { parseSummary = parseSummary, isLossMessage = isLossMessage }

SimAPI.setUserCallbacks(hook)
logInfo("hook loaded (debriefing module: " .. type(package.loaded["debriefing"]) .. ")")
LossTrackerGameGUI = hook

end)

if not ok then
    log.write("LossTracker", log.ERROR, "hook failed to load: " .. tostring(loadError))
end
