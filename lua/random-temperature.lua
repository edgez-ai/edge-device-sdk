local list = {}

math.randomseed((os.time() % 100000) + math.floor((os.clock() or 0) * 1000))

local value = math.random(180, 320) / 10
local online = (math.random(0, 1) == 1)


table.insert(list, {
	object = 3303,
	instance = 0,
	resource = 5700,
	value = value,
})

table.insert(list, {
	object = 3303,
	instance = 0,
	resource = 5850,
	value = online,
})

return list