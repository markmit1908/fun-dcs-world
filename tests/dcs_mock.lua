--[[
Minimal stand-in for the DCS mission scripting environment, enough to run
loss_tracker.lua under plain Lua 5.1 in tests. Only the calls the tracker
makes are implemented. Time only moves when Mock.advance() is called.
]]

Mock = {
    now = 0,
    timers = {},
    flags = {},
    messages = {},
    log = {},
    menus = {},
    handlers = {},
    units = {},
    airbases = {},
}

coalition = { side = { NEUTRAL = 0, RED = 1, BLUE = 2 } }
Group = { Category = { AIRPLANE = 0, HELICOPTER = 1, GROUND = 2, SHIP = 3, TRAIN = 4 } }

world = {
    event = {
        S_EVENT_SHOT = 1, S_EVENT_HIT = 2, S_EVENT_TAKEOFF = 3, S_EVENT_LAND = 4,
        S_EVENT_CRASH = 5, S_EVENT_EJECTION = 6, S_EVENT_DEAD = 8, S_EVENT_PILOT_DEAD = 9,
        S_EVENT_MISSION_START = 11, S_EVENT_MISSION_END = 12, S_EVENT_BIRTH = 15,
        S_EVENT_SHOOTING_START = 23, S_EVENT_SHOOTING_END = 24,
        S_EVENT_KILL = 28, S_EVENT_UNIT_LOST = 30,
    },
}
function world.addEventHandler(handler) table.insert(Mock.handlers, handler) end
function world.getAirbases() return Mock.airbases end

timer = {}
function timer.getTime() return Mock.now end
function timer.scheduleFunction(fn, arg, time)
    table.insert(Mock.timers, { fn = fn, arg = arg, time = time })
end

trigger = { action = {} }
function trigger.action.outText(text, duration)
    table.insert(Mock.messages, { side = "all", text = text })
end
function trigger.action.outTextForCoalition(side, text, duration, clearview)
    table.insert(Mock.messages, { side = side, text = text, duration = duration, clearview = clearview == true })
end
function trigger.action.setUserFlag(flag, value) Mock.flags[flag] = value end

env = {}
function env.info(message) table.insert(Mock.log, message) end

missionCommands = {}
function missionCommands.addCommandForCoalition(side, name, path, fn, arg)
    table.insert(Mock.menus, { side = side, name = name, fn = fn, arg = arg })
end

-- Units ------------------------------------------------------------------

local UnitMethods = {}
UnitMethods.__index = UnitMethods
function UnitMethods:getName() return self.name end
function UnitMethods:getTypeName() return self.type end
function UnitMethods:getCoalition() return self.side end
function UnitMethods:getPlayerName() return self.player end
function UnitMethods:isExist() return self.alive end
function UnitMethods:isActive() return self.active end
function UnitMethods:inAir() return self.in_air end
function UnitMethods:getPoint() return self.point end
function UnitMethods:getVelocity() return { x = self.speed, y = 0, z = 0 } end
function UnitMethods:getGroup() return self.group end
-- Like DCS, nil once all ammo is expended.
function UnitMethods:getAmmo()
    if (self.rounds or 0) <= 0 then return nil end
    return { { count = self.rounds, desc = {} } }
end

local GroupMethods = {}
GroupMethods.__index = GroupMethods
function GroupMethods:getUnits() return { self.unit } end
function GroupMethods:getCategory() return self.category end

Unit = {}
function Unit.getByName(name)
    local unit = Mock.units[name]
    if unit and unit.alive then return unit end
    return nil
end

-- Create a unit in its own group. opts: side, type, category, player,
-- in_air (default true), active (default true), x, z, speed, rounds.
function Mock.addUnit(name, opts)
    opts = opts or {}
    local unit = setmetatable({
        name = name,
        type = opts.type or "Ju-88A4",
        side = opts.side or coalition.side.RED,
        player = opts.player,
        alive = true,
        active = opts.active ~= false,
        in_air = opts.in_air ~= false,
        point = { x = opts.x or 0, y = 0, z = opts.z or 0 },
        speed = opts.speed or 100,
        rounds = opts.rounds,
    }, UnitMethods)
    unit.group = setmetatable({ unit = unit, category = opts.category or Group.Category.AIRPLANE }, GroupMethods)
    Mock.units[name] = unit
    return unit
end

function coalition.getGroups(side, category)
    local groups = {}
    for _, unit in pairs(Mock.units) do
        if unit.side == side and unit.group.category == category then
            table.insert(groups, unit.group)
        end
    end
    return groups
end

function coalition.getPlayers(side)
    local players = {}
    for _, unit in pairs(Mock.units) do
        if unit.side == side and unit.player and unit.alive then
            table.insert(players, unit)
        end
    end
    return players
end

local BaseMethods = {}
BaseMethods.__index = BaseMethods
function BaseMethods:getCoalition() return self.side end
function BaseMethods:getPoint() return self.point end
function BaseMethods:getName() return self.name end

function Mock.addAirbase(name, side, x, z)
    local base = setmetatable({ name = name, side = side, point = { x = x, y = 0, z = z } }, BaseMethods)
    table.insert(Mock.airbases, base)
    return base
end

-- Driving the simulation ---------------------------------------------------

function Mock.fire(event)
    for _, handler in ipairs(Mock.handlers) do
        handler:onEvent(event)
    end
end

-- Move time forward, running scheduled functions in time order. A function
-- that returns a number is rescheduled for that time, as in DCS.
function Mock.advance(seconds)
    local target = Mock.now + seconds
    while true do
        local nextIndex
        for i, t in ipairs(Mock.timers) do
            if t.time <= target and (nextIndex == nil or t.time < Mock.timers[nextIndex].time) then
                nextIndex = i
            end
        end
        if nextIndex == nil then break end
        local t = table.remove(Mock.timers, nextIndex)
        Mock.now = math.max(Mock.now, t.time)
        local again = t.fn(t.arg, Mock.now)
        if type(again) == "number" then
            table.insert(Mock.timers, { fn = t.fn, arg = t.arg, time = again })
        end
    end
    Mock.now = target
end
