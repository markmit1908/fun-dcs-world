--[[
loss_tracker.lua: extended aircraft loss scoring for DCS World missions.

DCS only scores a kill when an aircraft is destroyed. This script also counts
an enemy aircraft as lost, credited to the last player-coalition unit that hit
it, when it crashes, its pilot ejects, or it lands away from an allied base.
Normal DCS scoring is left alone; this adds on-screen messages, a tally, and a
flag that is set once every enemy aircraft is gone.

Set the global LossTrackerConfig before running this file (the Python module
in this package does that). Each enemy aircraft is resolved exactly once, so
the several events DCS sends for one loss (hit, kill, crash, dead) count once.

Written for the Lua 5.1 mission scripting environment.
]]

local defaults = {
    player_coalition = "blue",
    crash_is_loss = true,
    ejection_is_loss = true,
    emergency_landing_is_loss = true,
    attribution_timeout_s = 900,
    resolve_delay_s = 3,
    allied_base_radius_m = 3000,
    poll_interval_s = 5,
    message_duration_s = 10,
    end_mission_when_all_lost = true,
    end_mission_delay_s = 30,
    end_flag = 9001,
}

local cfg = {}
for key, value in pairs(defaults) do cfg[key] = value end
for key, value in pairs(LossTrackerConfig or {}) do cfg[key] = value end

LossTracker = { config = cfg, aircraft = {}, order = {}, allLost = false }
local LT = LossTracker

local SIDES = { red = coalition.side.RED, blue = coalition.side.BLUE }
LT.playerSide = SIDES[cfg.player_coalition]
if LT.playerSide == nil then
    error("LossTracker: player_coalition must be 'red' or 'blue', got " .. tostring(cfg.player_coalition))
end
LT.enemySide = LT.playerSide == coalition.side.BLUE and coalition.side.RED or coalition.side.BLUE

local REASON_TEXT = {
    kill = "shot down",
    crash = "crashed",
    lost = "lost",
    ejection = "pilot ejected",
    emergency_landing = "landed away from an allied base",
    returned = "returned to base",
}

-- Which config option decides whether a reason earns the attacker credit.
-- "kill" always does; "returned" never does.
local CREDIT_OPTION = {
    crash = "crash_is_loss",
    lost = "crash_is_loss",
    ejection = "ejection_is_loss",
    emergency_landing = "emergency_landing_is_loss",
}

-- DCS objects in events may already be dead, and their methods can throw.
local function try(fn)
    local ok, result = pcall(fn)
    if ok then return result end
    return nil
end

local function log(message)
    env.info("LossTracker: " .. message)
end

local function tell(message, duration)
    trigger.action.outTextForCoalition(LT.playerSide, message, duration or cfg.message_duration_s)
end

local AIRCRAFT_GROUPS = { Group.Category.AIRPLANE, Group.Category.HELICOPTER }

local function isEnemyAircraft(unit)
    if try(function() return unit:getCoalition() end) ~= LT.enemySide then return false end
    local category = try(function() return unit:getGroup():getCategory() end)
    for _, wanted in ipairs(AIRCRAFT_GROUPS) do
        if category == wanted then return true end
    end
    return false
end

function LT.register(unit)
    local name = try(function() return unit:getName() end)
    if name == nil or LT.aircraft[name] then return end
    local state = {
        name = name,
        type = try(function() return unit:getTypeName() end) or "aircraft",
        coalition = LT.enemySide,
        airborne = try(function() return unit:inAir() end) == true,
        resolved = false,
    }
    LT.aircraft[name] = state
    table.insert(LT.order, state)
end

function LT.registerExisting()
    for _, category in ipairs(AIRCRAFT_GROUPS) do
        for _, group in ipairs(coalition.getGroups(LT.enemySide, category) or {}) do
            for _, unit in ipairs(group:getUnits() or {}) do
                -- Late-activation units are skipped here and added on birth.
                if try(function() return unit:isExist() and unit:isActive() end) then
                    LT.register(unit)
                end
            end
        end
    end
end

-- Label for a unit on the player's coalition, or nil for anything else
-- (enemy friendly fire, neutrals, unknown shooters).
local function attackerLabel(unit)
    if unit == nil then return nil end
    if try(function() return unit:getCoalition() end) ~= LT.playerSide then return nil end
    return try(function() return unit:getPlayerName() end)
        or try(function() return unit:getName() end)
        or "unknown"
end

local function trackedState(unit)
    if unit == nil then return nil end
    local name = try(function() return unit:getName() end)
    local state = name and LT.aircraft[name]
    if state and not state.resolved then return state end
    return nil
end

local function recentAttacker(state)
    if state.attacker and timer.getTime() - state.hitTime <= cfg.attribution_timeout_s then
        return state.attacker
    end
    return nil
end

function LT.nearAlliedBase(side, point)
    local radius2 = cfg.allied_base_radius_m * cfg.allied_base_radius_m
    for _, base in ipairs(world.getAirbases() or {}) do
        if try(function() return base:getCoalition() end) == side then
            local p = try(function() return base:getPoint() end)
            if p then
                local dx, dz = point.x - p.x, point.z - p.z
                if dx * dx + dz * dz <= radius2 then return true end
            end
        end
    end
    return false
end

function LT.resolve(state, reason, attacker)
    if state.resolved then return end
    state.resolved = true
    state.pending = nil
    state.reason = reason

    local option = CREDIT_OPTION[reason]
    if reason ~= "kill" and not (option and cfg[option]) then
        attacker = nil
    end
    state.creditedTo = attacker

    local message = string.format("%s (%s) %s", state.type, state.name, REASON_TEXT[reason] or reason)
    if attacker then
        message = message .. " - credited to " .. attacker
    else
        message = message .. " - no credit"
    end
    tell(message)
    log(message)
    LT.checkAllLost()
end

-- Resolve after a short delay, so a KILL event that arrives just after the
-- crash or ejection can still claim it as "shot down". The first reason wins.
function LT.queue(state, reason, attacker)
    if state.resolved or state.pending then return end
    state.pending = { reason = reason, attacker = attacker }
    timer.scheduleFunction(function()
        if state.pending then
            LT.resolve(state, state.pending.reason, state.pending.attacker)
        end
        return nil
    end, nil, timer.getTime() + cfg.resolve_delay_s)
end

function LT.onLanded(state, unit, place)
    local allied = place ~= nil and try(function() return place:getCoalition() end) == state.coalition
    if not allied then
        local point = try(function() return unit:getPoint() end)
        allied = point ~= nil and LT.nearAlliedBase(state.coalition, point)
    end
    if allied then
        state.safe = true
    elseif cfg.emergency_landing_is_loss then
        LT.queue(state, "emergency_landing", recentAttacker(state))
    end
end

function LT.onEvent(event)
    local E = world.event
    local id = event.id

    if id == E.S_EVENT_BIRTH then
        if isEnemyAircraft(event.initiator) then LT.register(event.initiator) end

    elseif id == E.S_EVENT_HIT then
        local state = trackedState(event.target)
        local attacker = attackerLabel(event.initiator)
        if state and attacker then
            state.attacker = attacker
            state.hitTime = timer.getTime()
        end

    elseif id == E.S_EVENT_KILL then
        local state = trackedState(event.target)
        if state then
            LT.resolve(state, "kill", attackerLabel(event.initiator) or recentAttacker(state))
        end

    elseif id == E.S_EVENT_EJECTION then
        local state = trackedState(event.initiator)
        if state and cfg.ejection_is_loss then
            LT.queue(state, "ejection", recentAttacker(state))
        end

    elseif id == E.S_EVENT_CRASH or id == E.S_EVENT_DEAD
        or id == E.S_EVENT_PILOT_DEAD or id == E.S_EVENT_UNIT_LOST then
        local state = trackedState(event.initiator)
        if state then
            LT.queue(state, state.safe and "returned" or "crash", recentAttacker(state))
        end

    elseif id == E.S_EVENT_LAND then
        local state = trackedState(event.initiator)
        if state then LT.onLanded(state, event.initiator, event.place) end

    elseif id == E.S_EVENT_TAKEOFF then
        local state = trackedState(event.initiator)
        if state then
            state.safe = false
            state.airborne = true
        end

    elseif id == E.S_EVENT_MISSION_END then
        log(LT.summary(true))
    end
end

-- Catches what events miss: units that vanish without a death event (AI
-- despawning after landing) and aircraft stopped on the ground with no LAND
-- event.
function LT.poll(_, now)
    for _, state in ipairs(LT.order) do
        if not state.resolved and not state.pending then
            local unit = Unit.getByName(state.name)
            if unit == nil or not try(function() return unit:isExist() end) then
                LT.queue(state, state.safe and "returned" or "lost", recentAttacker(state))
            elseif try(function() return unit:inAir() end) then
                state.airborne = true
            elseif state.airborne and not state.safe then
                local v = try(function() return unit:getVelocity() end)
                if v and math.sqrt(v.x * v.x + v.y * v.y + v.z * v.z) < 1 then
                    LT.onLanded(state, unit, nil)
                end
            end
        end
    end
    return now + cfg.poll_interval_s
end

function LT.summary(detailed)
    local lost, noCredit = 0, 0
    local counts, attackers = {}, {}
    for _, state in ipairs(LT.order) do
        if state.resolved then
            lost = lost + 1
            local who = state.creditedTo
            if who then
                if counts[who] == nil then
                    counts[who] = 0
                    table.insert(attackers, who)
                end
                counts[who] = counts[who] + 1
            else
                noCredit = noCredit + 1
            end
        end
    end
    table.sort(attackers)

    local lines = { string.format("Enemy aircraft lost: %d of %d", lost, #LT.order) }
    for _, who in ipairs(attackers) do
        table.insert(lines, string.format("  %s: %d", who, counts[who]))
    end
    if noCredit > 0 then
        table.insert(lines, string.format("  No credit: %d", noCredit))
    end
    if detailed then
        for _, state in ipairs(LT.order) do
            local status = state.resolved and (REASON_TEXT[state.reason] or state.reason) or "still active"
            if state.creditedTo then status = status .. " by " .. state.creditedTo end
            table.insert(lines, string.format("  %s (%s): %s", state.name, state.type, status))
        end
    end
    return table.concat(lines, "\n")
end

function LT.checkAllLost()
    if LT.allLost or #LT.order == 0 then return end
    for _, state in ipairs(LT.order) do
        if not state.resolved then return end
    end
    LT.allLost = true

    local message = "All enemy aircraft eliminated.\n\n" .. LT.summary()
    if cfg.end_mission_when_all_lost then
        message = message .. string.format("\n\nMission ends in %d seconds.", cfg.end_mission_delay_s)
    end
    trigger.action.outText(message, math.max(cfg.end_mission_delay_s, cfg.message_duration_s))
    log(LT.summary(true))
    timer.scheduleFunction(function()
        trigger.action.setUserFlag(cfg.end_flag, true)
        return nil
    end, nil, timer.getTime() + cfg.end_mission_delay_s)
end

local handler = {}
function handler:onEvent(event)
    local ok, err = pcall(LT.onEvent, event)
    if not ok then log("event error: " .. tostring(err)) end
end

LT.registerExisting()
world.addEventHandler(handler)
timer.scheduleFunction(LT.poll, nil, timer.getTime() + cfg.poll_interval_s)
missionCommands.addCommandForCoalition(LT.playerSide, "Enemy loss tally", nil, function()
    tell(LT.summary(), 20)
end)
log(string.format("tracking %d enemy aircraft", #LT.order))
