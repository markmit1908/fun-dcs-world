--[[
loss_tracker.lua: extended aircraft loss scoring for DCS World missions.

DCS only scores a kill when an aircraft is destroyed. This script also counts
an enemy aircraft as lost, credited to the last player-coalition unit that hit
it, when it crashes, its pilot ejects, or it lands away from an allied base.
Normal DCS scoring is left alone; this adds on-screen messages, a tally, and a
flag that is set once every enemy aircraft is gone.

Set the global LossTrackerConfig before this runs. The Python module in this
package prepends it to the copy of this file embedded in the mission. Each enemy aircraft is resolved exactly once, so
the several events DCS sends for one loss (hit, kill, crash, dead) count once.

Written for the Lua 5.1 mission scripting environment.
]]

local defaults = {
    player_coalition = "blue",
    mission_name = "",
    crash_is_loss = true,
    ejection_is_loss = true,
    emergency_landing_is_loss = true,
    attribution_timeout_s = 900,
    resolve_delay_s = 3,
    allied_base_radius_m = 3000,
    poll_interval_s = 5,
    message_duration_s = 10,
    summary_duration_s = 60,
    end_mission_when_all_lost = true,
    end_mission_when_out_of_ammo = true,
    end_mission_delay_s = 30,
    end_flag = 9001,
}

local cfg = {}
for key, value in pairs(defaults) do cfg[key] = value end
for key, value in pairs(LossTrackerConfig or {}) do cfg[key] = value end

LossTracker = {
    config = cfg,
    aircraft = {},
    order = {},
    playersLost = {},
    shooters = {},
    hadAmmo = {},
    losses = 0,
    allLost = false,
    endScheduled = false,
    startTime = timer.getTime(),
}
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

-- Rounds fired ---------------------------------------------------------------
-- DCS has no per-round event for guns, only SHOOTING_START/END, so rounds are
-- counted from the shooter's ammo: its load at the first trigger pull minus
-- what it has now. Keyed by attacker label so a respawned player's count
-- carries over; an ammo increase (rearm) raises the baseline.

local function ammoCount(unit)
    if not try(function() return unit:isExist() end) then return nil end
    -- getAmmo() returns nil once everything is expended.
    local ammo = try(function() return unit:getAmmo() end) or {}
    local total = 0
    for _, entry in ipairs(ammo) do total = total + (entry.count or 0) end
    return total
end

-- Rounds fired so far by this attacker, or nil if it never opened fire.
function LT.roundsFired(label)
    local shooter = label and LT.shooters[label]
    if shooter == nil then return nil end
    local now = ammoCount(shooter.unit) or shooter.lastAmmo
    if now > shooter.lastAmmo then
        shooter.baseline = shooter.baseline + (now - shooter.lastAmmo)
    end
    shooter.lastAmmo = now
    return shooter.banked + shooter.baseline - now
end

function LT.onShooting(unit)
    local label = attackerLabel(unit)
    if label == nil then return end
    local name = try(function() return unit:getName() end)
    local shooter = LT.shooters[label]
    if shooter and shooter.unitName == name then
        LT.roundsFired(label)
        return
    end
    local ammo = ammoCount(unit)
    if ammo == nil then return end
    local banked = LT.roundsFired(label) or 0
    shooter = shooter or { kills = 0, atLastKill = 0 }
    shooter.unit, shooter.unitName = unit, name
    shooter.banked, shooter.baseline, shooter.lastAmmo = banked, ammo, ammo
    LT.shooters[label] = shooter
end

-- Mission time as m:ss.
local function clock(seconds)
    return string.format("%d:%02d", math.floor(seconds / 60), math.floor(seconds % 60))
end

local function roundsText(state)
    if state.rounds == nil then return "" end
    return string.format(" (%d rounds, %d fired so far)", state.rounds, state.roundsTotal)
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
    LT.losses = LT.losses + 1
    state.lossNumber = LT.losses
    state.lostAt = timer.getTime() - LT.startTime

    local fired = LT.roundsFired(attacker)
    if fired then
        local shooter = LT.shooters[attacker]
        state.rounds = fired - shooter.atLastKill
        state.roundsTotal = fired
        shooter.atLastKill = fired
        shooter.kills = shooter.kills + 1
    end

    local message = string.format("%s (%s) %s", state.type, state.name, REASON_TEXT[reason] or reason)
    if attacker then
        message = message .. " - credited to " .. attacker .. roundsText(state)
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

    elseif id == E.S_EVENT_SHOOTING_START or id == E.S_EVENT_SHOOTING_END
        or id == E.S_EVENT_SHOT then
        LT.onShooting(event.initiator)
        LT.checkOutOfAmmo()

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
        LT.onPlayerLost(event.initiator, "ejection")

    elseif id == E.S_EVENT_CRASH or id == E.S_EVENT_DEAD
        or id == E.S_EVENT_PILOT_DEAD or id == E.S_EVENT_UNIT_LOST then
        local state = trackedState(event.initiator)
        if state then
            LT.queue(state, state.safe and "returned" or "crash", recentAttacker(state))
        end
        local what = "lost"
        if id == E.S_EVENT_CRASH then what = "crash"
        elseif id == E.S_EVENT_PILOT_DEAD then what = "pilot_dead"
        elseif id == E.S_EVENT_DEAD then what = "dead" end
        LT.onPlayerLost(event.initiator, what)

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
    LT.checkOutOfAmmo()
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
        local line = string.format("  %s: %d", who, counts[who])
        local shooter = LT.shooters[who]
        if shooter and shooter.kills > 0 then
            line = line .. string.format(" (%d rounds fired, %d per kill)",
                LT.roundsFired(who), math.floor(shooter.atLastKill / shooter.kills + 0.5))
        end
        table.insert(lines, line)
    end
    if noCredit > 0 then
        table.insert(lines, string.format("  No credit: %d", noCredit))
    end
    if detailed then
        -- Losses in the order they happened, then the survivors.
        local states = {}
        for _, state in ipairs(LT.order) do table.insert(states, state) end
        table.sort(states, function(a, b)
            return (a.lossNumber or math.huge) < (b.lossNumber or math.huge)
        end)
        for _, state in ipairs(states) do
            if state.resolved then
                local status = REASON_TEXT[state.reason] or state.reason
                if state.creditedTo then status = status .. " - " .. state.creditedTo .. roundsText(state) end
                table.insert(lines, string.format("  %s  %s (%s): %s",
                    clock(state.lostAt), state.name, state.type, status))
            else
                table.insert(lines, string.format("  %s (%s): still active", state.name, state.type))
            end
        end
    end
    return table.concat(lines, "\n")
end

-- Full-screen summary. clearview replaces the message stack, which is as close
-- to a splash screen as mission scripting gets.
function LT.showSplash(title, subtitle, footer, duration)
    local elapsed = timer.getTime() - LT.startTime
    local lines = { "==========  " .. title .. "  ==========", subtitle }
    if cfg.mission_name ~= "" then
        table.insert(lines, "Mission: " .. cfg.mission_name)
    end
    table.insert(lines, string.format("Time: %d min %02d s", math.floor(elapsed / 60), math.floor(elapsed % 60)))
    table.insert(lines, "")
    table.insert(lines, LT.summary(true))
    if footer then
        table.insert(lines, "")
        table.insert(lines, footer)
    end
    local text = table.concat(lines, "\n")
    -- With LossTrackerGameGUI.lua installed the summary appears in a window,
    -- so the text only needs to reach the hook (via onTriggerMessage).
    if LT.hookPresent then duration = 2 end
    trigger.action.outTextForCoalition(LT.playerSide, text, duration, true)
    log(text)
end

local PLAYER_LOSS_TEXT = {
    ejection = "ejected",
    crash = "crashed",
    pilot_dead = "was killed",
    dead = "was shot down",
    lost = "was lost",
}

local function humanPlayer(unit)
    if unit == nil then return nil end
    if try(function() return unit:getCoalition() end) ~= LT.playerSide then return nil end
    return try(function() return unit:getPlayerName() end)
end

local function otherPlayersFlying(lostName)
    for _, unit in ipairs(coalition.getPlayers(LT.playerSide) or {}) do
        local name = try(function() return unit:getName() end)
        if name and name ~= lostName and not LT.playersLost[name]
            and try(function() return unit:isExist() end) then
            return true
        end
    end
    return false
end

-- A human on the player's coalition lost their aircraft. Several events
-- arrive for one loss (ejection, crash, pilot dead), so show it once per unit.
function LT.onPlayerLost(unit, what)
    local player = humanPlayer(unit)
    if player == nil or LT.allLost then return end
    local name = try(function() return unit:getName() end) or player
    if LT.playersLost[name] then return end
    LT.playersLost[name] = true

    local title = otherPlayersFlying(name) and (player .. " DOWN") or "MISSION FAILED"
    LT.showSplash(title, player .. " " .. PLAYER_LOSS_TEXT[what], nil, cfg.summary_duration_s)
end

-- Set end_flag after end_mission_delay_s (the EndMission trigger watches it).
-- Returns the footer for the splash, or nil if the end was already scheduled.
function LT.scheduleEnd()
    if LT.endScheduled then return nil end
    LT.endScheduled = true
    timer.scheduleFunction(function()
        trigger.action.setUserFlag(cfg.end_flag, true)
        return nil
    end, nil, timer.getTime() + cfg.end_mission_delay_s)
    return string.format("Mission ends in %d seconds", cfg.end_mission_delay_s)
end

local function endSplashDuration()
    return math.max(cfg.end_mission_delay_s, cfg.message_duration_s)
end

function LT.checkAllLost()
    if LT.allLost or #LT.order == 0 then return end
    for _, state in ipairs(LT.order) do
        if not state.resolved then return end
    end
    LT.allLost = true

    local footer
    if cfg.end_mission_when_all_lost then footer = LT.scheduleEnd() end
    LT.showSplash("MISSION COMPLETE", "All enemy aircraft eliminated", footer, endSplashDuration())
end

-- Ends the mission once every human pilot on the player's coalition has
-- emptied their aircraft. Pilots who never had ammo don't count as empty.
function LT.checkOutOfAmmo()
    if not cfg.end_mission_when_out_of_ammo or LT.endScheduled then return end
    local flying, empty = 0, {}
    for _, unit in ipairs(coalition.getPlayers(LT.playerSide) or {}) do
        local name = try(function() return unit:getName() end)
        local ammo = ammoCount(unit)
        if name and ammo and not LT.playersLost[name] then
            flying = flying + 1
            if ammo > 0 then
                LT.hadAmmo[name] = true
            elseif LT.hadAmmo[name] then
                table.insert(empty, try(function() return unit:getPlayerName() end) or name)
            end
        end
    end
    if flying == 0 or #empty < flying then return end

    local who = table.concat(empty, ", ") .. (#empty == 1 and " is" or " are") .. " out of ammunition"
    LT.showSplash("OUT OF AMMO", who, LT.scheduleEnd(), endSplashDuration())
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
