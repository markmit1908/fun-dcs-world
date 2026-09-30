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
    status_enabled = true,
    status_interval_s = 1,
    critical_hit_fraction = 0.25,
    critical_hit_display_s = 5,
    fuel_leak_per_min = 0.05,
    trend_window_s = 20,
    losing_height_m = 500,
    descent_rate_mps = 10,
    slowing_fraction = 0.7,
    end_mission_when_all_lost = true,
    end_mission_when_out_of_ammo = false,
    end_mission_delay_s = 30,
    end_flag = 9001,
    mission_goals = true,
    score_flag = 9002,
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
    credited = 0,
    kills = {},
    recent = {},
    currentTarget = {},
    statusStopped = false,
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
        hits = 0,
        crits = 0,
    }
    -- Baselines for "losing height" and "slowing".
    local point = try(function() return unit:getPoint() end)
    local v = try(function() return unit:getVelocity() end)
    state.alt0 = point and point.y
    state.speed0 = v and math.sqrt(v.x * v.x + v.y * v.y + v.z * v.z)
    if state.speed0 and state.speed0 < 1 then state.speed0 = nil end  -- parked
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

local function round(x) return math.floor(x + 0.5) end

-- rounds for this kill, total so far, and total / kills so far (the
-- effective rate at the moment of this kill).
local function plural(n, word)
    return string.format("%d %s%s", n, word, n == 1 and "" or "s")
end

-- What the target went through: hits, critical hits and lasting damage states.
local function damageNotes(state)
    if state.hits == 0 then return nil end
    local notes = { plural(state.hits, "hit") }
    if state.crits > 0 then table.insert(notes, string.format("%d critical", state.crits)) end
    if state.engineOut then table.insert(notes, "engine out") end
    if state.fuelLeak then table.insert(notes, "fuel leak") end
    if state.aborting then table.insert(notes, "broke off") end
    return table.concat(notes, ", ")
end

-- " (rounds for this kill, total so far, total / kills so far; damage notes)":
-- the rate is the effective rate at the moment of this kill.
local function detailsText(state)
    local parts = {}
    if state.rounds ~= nil then
        table.insert(parts, string.format("%d rounds, %d fired so far, %d per kill",
            state.rounds, state.roundsTotal, state.rate))
    end
    local notes = damageNotes(state)
    if notes then table.insert(parts, notes) end
    if #parts == 0 then return "" end
    return " (" .. table.concat(parts, "; ") .. ")"
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
    if attacker then
        -- Mission goals turn this count into the debrief's mission result.
        LT.credited = LT.credited + 1
        trigger.action.setUserFlag(cfg.score_flag, LT.credited)
    end
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
        state.rate = round(fired / shooter.kills)
    end
    if attacker then LT.kills[attacker] = (LT.kills[attacker] or 0) + 1 end

    local message = string.format("%s (%s) %s", state.type, state.name, REASON_TEXT[reason] or reason)
    if attacker then
        message = message .. " - credited to " .. attacker .. detailsText(state)
    else
        message = message .. " - no credit" .. detailsText(state)
    end
    tell(message)
    log(message)
    -- The status block replaces the message area, so it repeats this.
    table.insert(LT.recent, { text = message, expires = timer.getTime() + cfg.message_duration_s })
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
            LT.recordHit(state, event.target, attacker)
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

    elseif id == E.S_EVENT_ENGINE_SHUTDOWN then
        local state = trackedState(event.initiator)
        if state and try(function() return event.initiator:inAir() end) then
            state.engineOut = true
            log(state.name .. ": engine out")
        end

    elseif E.S_EVENT_AI_ABORT_MISSION and id == E.S_EVENT_AI_ABORT_MISSION then
        -- The AI judged itself too damaged to carry on (not in older DCS).
        local state = trackedState(event.initiator)
        if state then
            state.aborting = true
            log(state.name .. ": breaking off")
        end

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
            -- Keep damaged targets' fuel/height/speed trends current.
            if state.hits > 0 then LT.conditions(state) end
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
                LT.roundsFired(who), round(shooter.atLastKill / shooter.kills))
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
                if state.creditedTo then status = status .. " - " .. state.creditedTo end
                table.insert(lines, string.format("  %s  %s (%s): %s%s",
                    clock(state.lostAt), state.name, state.type, status, detailsText(state)))
            else
                table.insert(lines, string.format("  %s (%s): still active%s",
                    state.name, state.type, detailsText(state)))
            end
        end
    end
    return table.concat(lines, "\n")
end

-- Full-screen summary. clearview replaces the message stack, which is as close
-- to a splash screen as mission scripting gets. The status block would replace
-- it in turn, so it pauses for as long as the summary shows, or stops for good
-- when final (the mission is ending).
function LT.showSplash(title, subtitle, footer, duration, final)
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
    if final then
        LT.statusStopped = true
    else
        LT.statusResumeAt = timer.getTime() + duration
    end
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
    LT.showSplash(title, player .. " " .. PLAYER_LOSS_TEXT[what], nil, cfg.summary_duration_s, false)
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
    LT.showSplash("MISSION COMPLETE", "All enemy aircraft eliminated", footer, endSplashDuration(), true)
end

-- Shows the OUT OF AMMO summary once every human pilot on the player's
-- coalition has emptied their aircraft (pilots who never had ammo don't
-- count). The mission carries on and later losses still count, unless
-- end_mission_when_out_of_ammo is set. Shown again only after someone has
-- ammo again (rearmed) and runs dry once more.
function LT.checkOutOfAmmo()
    if LT.allLost then return end
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
    if flying == 0 or #empty < flying then
        LT.outOfAmmoShown = false
        return
    end
    if LT.outOfAmmoShown then return end
    LT.outOfAmmoShown = true

    local who = table.concat(empty, ", ") .. (#empty == 1 and " is" or " are") .. " out of ammunition"
    if cfg.end_mission_when_out_of_ammo then
        local footer = LT.scheduleEnd()
        if footer then
            LT.showSplash("OUT OF AMMO", who, footer, endSplashDuration(), true)
            return
        end
    end
    LT.showSplash("OUT OF AMMO", who, "Mission continues: losses from here on still count.",
        cfg.summary_duration_s, false)
end

-- Live status ------------------------------------------------------------
-- A block in the top-right message area for each human pilot, refreshed every
-- status_interval_s: rounds fired, kills, rounds per kill, the side's score and
-- recent losses. clearview replaces the previous block (a plain message would
-- stack up), which would also wipe kill messages, so the block repeats them.

-- Damage to enemy aircraft hit by the player's side; each pilot's current
-- target is the one they hit last.
-- Damage comes from Unit.getLife() / getLife0(). Aircraft with a detailed
-- damage model may keep full life until destroyed; then only the hit count
-- shows. Each hit's life values go to dcs.log to confirm which applies.

local function currentLife(state)
    local unit = Unit.getByName(state.name)
    if unit == nil then return state.life end
    local life = try(function() return unit:getLife() end)
    if life then state.life = life end
    return state.life
end

-- 0-100, or nil when life hasn't dropped below full (or isn't available).
local function damagePercent(state)
    local life, life0 = currentLife(state), state.life0
    if life == nil or life0 == nil or life0 <= 0 or life >= life0 then return nil end
    return math.max(0, math.min(100, round(100 * (1 - life / life0))))
end

-- A hit that removes more than critical_hit_fraction of the target's starting
-- life at once counts as critical (ordinary gun hits take a few percent).
function LT.recordHit(state, unit, attacker)
    state.hits = state.hits + 1
    state.life0 = state.life0 or try(function() return unit:getLife0() end)
    local before = state.life or state.life0
    state.life = try(function() return unit:getLife() end) or state.life
    LT.currentTarget[attacker] = state
    local critical = before and state.life and state.life0 and state.life0 > 0
        and (before - state.life) >= cfg.critical_hit_fraction * state.life0
    if critical then
        state.crits = state.crits + 1
        state.critUntil = timer.getTime() + cfg.critical_hit_display_s
    end
    log(string.format("hit %d on %s by %s: life %s of %s, fuel %s%s", state.hits, state.name, attacker,
        tostring(state.life), tostring(state.life0),
        tostring(try(function() return unit:getFuel() end)), critical and " CRITICAL" or ""))
end

-- Fuel, altitude and speed samples over the last trend_window_s, taken at
-- most once a second (from the status refresh and the poll).
local function sample(state)
    local unit = Unit.getByName(state.name)
    if unit == nil then return nil end
    local now = timer.getTime()
    local last = state.samples and state.samples[#state.samples]
    if last and now - last.t < 0.9 then return last end
    local point = try(function() return unit:getPoint() end)
    local v = try(function() return unit:getVelocity() end)
    local current = {
        t = now,
        fuel = try(function() return unit:getFuel() end),
        alt = point and point.y,
        vy = v and v.y,
        speed = v and math.sqrt(v.x * v.x + v.y * v.y + v.z * v.z),
    }
    state.samples = state.samples or {}
    table.insert(state.samples, current)
    while now - state.samples[1].t > cfg.trend_window_s do table.remove(state.samples, 1) end
    return current
end

-- Damage indicators shown on the target line. Critical hit flashes for a few
-- seconds; engine out, breaking off and a detected fuel leak stay; losing
-- height and slowing reflect the moment.
function LT.conditions(state)
    local tags = {}
    if state.critUntil and timer.getTime() < state.critUntil then table.insert(tags, "CRITICAL HIT") end
    if state.engineOut then table.insert(tags, "ENGINE OUT") end
    if state.aborting then table.insert(tags, "BREAKING OFF") end

    local current = sample(state)
    if current then
        local first = state.samples[1]
        local span = current.t - first.t
        if current.fuel and first.fuel and span >= cfg.trend_window_s / 2
            and (first.fuel - current.fuel) / span * 60 >= cfg.fuel_leak_per_min then
            if not state.fuelLeak then log(state.name .. ": fuel leak") end
            state.fuelLeak = true
        end
        if state.fuelLeak then table.insert(tags, "LEAKING FUEL") end
        if (current.vy and current.vy < -cfg.descent_rate_mps)
            or (current.alt and state.alt0 and current.alt < state.alt0 - cfg.losing_height_m) then
            table.insert(tags, "LOSING HEIGHT")
        end
        if current.speed and state.speed0 and current.speed < state.speed0 * cfg.slowing_fraction then
            table.insert(tags, "SLOWING")
        end
    end
    return tags
end

-- One aircraft's line: damage bar and %, hits and criticals, indicators; or
-- how it was lost.
function LT.aircraftLine(state)
    local label = string.format("%s (%s)", state.type, state.name)
    local hits = plural(state.hits, "hit")
    if state.crits > 0 then hits = hits .. string.format(", %d critical", state.crits) end
    if state.resolved then
        return string.format("%s %s (%s)", label, REASON_TEXT[state.reason] or state.reason, hits)
    end

    local line
    local damage = damagePercent(state)
    if damage == nil then
        line = string.format("%s (%s)", label, hits)
    else
        local filled = math.floor(damage / 10 + 0.5)
        line = string.format("%s [%s%s] %d%% (%s)", label,
            string.rep("#", filled), string.rep("-", 10 - filled), damage, hits)
    end
    local tags = LT.conditions(state)
    if #tags > 0 then line = line .. "  " .. table.concat(tags, ", ") end
    return line
end

-- Every enemy aircraft the player's side has hit: this pilot's current
-- target first (marked ">"), then the other survivors, most recently hit
-- first, then the losses in the order they happened.
function LT.damageLines(label)
    local current = LT.currentTarget[label]
    local flying, lost = {}, {}
    for _, state in ipairs(LT.order) do
        if state.hits > 0 and state ~= current then
            table.insert(state.resolved and lost or flying, state)
        end
    end
    table.sort(flying, function(a, b) return (a.hitTime or 0) > (b.hitTime or 0) end)
    table.sort(lost, function(a, b) return (a.lossNumber or 0) < (b.lossNumber or 0) end)

    if current == nil and #flying == 0 and #lost == 0 then return nil end
    local lines = { "Aircraft hit:" }
    if current then table.insert(lines, "> " .. LT.aircraftLine(current)) end
    for _, state in ipairs(flying) do table.insert(lines, "  " .. LT.aircraftLine(state)) end
    for _, state in ipairs(lost) do table.insert(lines, "  " .. LT.aircraftLine(state)) end
    return lines
end

function LT.statusText(label)
    local fired = LT.roundsFired(label) or 0
    local kills = LT.kills[label] or 0
    local lines = {
        string.format("Rounds fired: %d", fired),
        string.format("Kills: %d", kills),
    }
    if kills > 0 then
        table.insert(lines, string.format("Rate: %d rounds/kill", round(fired / kills)))
    end
    local total = #LT.order
    local percent = total > 0 and round(100 * LT.credited / total) or 0
    table.insert(lines, string.format("Score: %d of %d enemy aircraft (%d%%)", LT.credited, total, percent))
    for _, line in ipairs(LT.damageLines(label) or {}) do table.insert(lines, line) end

    local now = timer.getTime()
    local recent = {}
    for _, event in ipairs(LT.recent) do
        if event.expires > now then table.insert(recent, event) end
    end
    LT.recent = recent
    if #recent > 0 then
        table.insert(lines, "")
        for _, event in ipairs(recent) do table.insert(lines, event.text) end
    end
    return table.concat(lines, "\n")
end

function LT.updateStatus(_, now)
    if LT.statusStopped then return nil end
    if LT.statusResumeAt and now < LT.statusResumeAt then  -- a summary is showing
        return now + cfg.status_interval_s
    end
    for _, unit in ipairs(coalition.getPlayers(LT.playerSide) or {}) do
        local label = try(function() return unit:getPlayerName() end)
        local groupId = try(function() return unit:getGroup():getID() end)
        if label and groupId and try(function() return unit:isExist() end) then
            trigger.action.outTextForGroup(groupId, LT.statusText(label), cfg.status_interval_s + 1, true)
        end
    end
    return now + cfg.status_interval_s
end

local handler = {}
function handler:onEvent(event)
    local ok, err = pcall(LT.onEvent, event)
    if not ok then log("event error: " .. tostring(err)) end
end

LT.registerExisting()
world.addEventHandler(handler)
timer.scheduleFunction(LT.poll, nil, timer.getTime() + cfg.poll_interval_s)
if cfg.status_enabled then
    timer.scheduleFunction(LT.updateStatus, nil, timer.getTime() + cfg.status_interval_s)
end
missionCommands.addCommandForCoalition(LT.playerSide, "Enemy loss tally", nil, function()
    tell(LT.summary(), 20)
end)
log(string.format("tracking %d enemy aircraft", #LT.order))
