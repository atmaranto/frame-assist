MESSAGE_BASE = 0x30
local tx = 0
local rx = 0
function send(...)
    local args = {...}
    for i=1,#args do
        args[i] = tostring(args[i])
    end
    args[#args + 1] = "\n"
    local data = table.concat(args, " ")

    -- Break into frame.bluetooth.max_length() - 1 chunks
    local max_length = frame.bluetooth.max_length() - 1
    for i=1,math.ceil(#data / max_length) do
        local start = (i - 1) * max_length + 1
        local chunk = data:sub(start, start + max_length - 1)
        
        for i=1,400 do
            local status, err = pcall(frame.bluetooth.send, string.char(MESSAGE_BASE + 1) .. chunk)
            if not status then
                print("Error sending data: " .. err)
                frame.sleep(0.001)
            else
                tx = tx + #chunk + 1
                return
            end
        end
    end
end

local last_tap = nil
function tap_callback()
    local time = frame.time.utc()
    if calibration.calibrating then
        -- Ignore tap when calibrating
        return
    end
    print("raw tap at " .. time)
    if last_tap ~= nil and time - last_tap < 0.5 and time - last_tap > 0.03 then
        double_tap_callback()
        last_tap = nil
    else
        last_tap = time
    end
end

function print_table(t, indent)
    indent = indent or 1
    local result = {}
    local prefix = string.rep("    ", indent)
    for k, v in pairs(t) do
        if type(v) == "table" then
            table.insert(result, prefix .. tostring(k) .. "=\n" .. print_table(v, indent + 1))
        else
            table.insert(result, prefix .. tostring(k) .. "=" .. tostring(v))
        end
    end
    return "{\n" .. table.concat(result, ",\n") .. "\n" .. string.rep("    ", indent - 1) .. "}"
end

local calibrating = false
-- local calibration
function reset_calibration()
    calibration = {
        minX = math.huge,
        maxX = -math.huge,
        minY = math.huge,
        maxY = -math.huge,
        minZ = math.huge,
        maxZ = -math.huge,
        nPoints = 0,
        calibrating = true
    }
    send("Calibration reset. Start moving the device to calibrate the magnetometer.")
end

function load_calibration()
    local status, cal_data = pcall(require, "calibration_result")
    if status then
        calibration = cal_data
        if calibration.calibrating then
            send("Calibration loaded. Start moving the device to calibrate the magnetometer.")
        else
            send("Calibration loaded. Offsets: " .. calibration.offsetX .. ", " .. calibration.offsetY .. ", " .. calibration.offsetZ)
        end
    else
        reset_calibration()
    end
end

function save_calibration()
    local file = frame.file.open("calibration_result.lua", "write")
    if file then
        file:write("return " .. print_table(calibration))
        file:close()
        send("Calibration saved.")
    else
        send("Failed to save calibration.")
    end
end

load_calibration()

function calculateTiltCompensatedHeading(imu)
    if calibration.calibrating then
        if calibration.nPoints >= 200 then
            calibration.calibrating = false
            calibration.offsetX = -(calibration.minX + calibration.maxX) / 2
            calibration.offsetY = -(calibration.minY + calibration.maxY) / 2
            calibration.offsetZ = -(calibration.minZ + calibration.maxZ) / 2
            send("Calibration complete. Offsets: " .. calibration.offsetX .. ", " .. calibration.offsetY .. ", " .. calibration.offsetZ)
            save_calibration()
        else
            calibration.minX = math.min(imu.compass.x, calibration.minX)
            calibration.maxX = math.max(imu.compass.x, calibration.maxX)
            calibration.minY = math.min(imu.compass.y, calibration.minY)
            calibration.maxY = math.max(imu.compass.y, calibration.maxY)
            calibration.minZ = math.min(imu.compass.z, calibration.minZ)
            calibration.maxZ = math.max(imu.compass.z, calibration.maxZ)
            calibration.nPoints = calibration.nPoints + 1

            send(string.format("Calibrating step %d, minX: %.2f, maxX: %.2f, minY: %.2f, maxY: %.2f, minZ: %.2f, maxZ: %.2f",
                 calibration.nPoints,
                 calibration.minX, calibration.maxX,
                 calibration.minY, calibration.maxY,
                 calibration.minZ, calibration.maxZ))
            return 0
        end
    end
    imu = imu or frame.imu.raw()
    -- Calibrate magnetometer readings
    local magX = imu.compass.x + calibration.offsetX
    local magY = imu.compass.y + calibration.offsetY
    local magZ = imu.compass.z + calibration.offsetZ

    -- Normalize accelerometer readings (assuming ±2g maps to ±8192)
    local accelFactor = 4096;
    local normAccelX = imu.accelerometer.x / accelFactor
    local normAccelY = imu.accelerometer.y / accelFactor
    local normAccelZ = imu.accelerometer.z / accelFactor

    -- Normalize to magnitude of 1g
    local norm = math.sqrt(normAccelX^2 + normAccelY^2 + normAccelZ^2)
    normAccelX = normAccelX / norm
    normAccelY = normAccelY / norm
    normAccelZ = normAccelZ / norm

    -- Calculate tilt-compensated heading
    local magDotGrav = magX * normAccelX + magY * normAccelY + magZ * normAccelZ
    local hMagX = magX - magDotGrav * normAccelX
    local hMagY = magY - magDotGrav * normAccelY

    local heading = math.atan(hMagY, hMagX) - math.pi / 2
    local degrees = heading * 180 / math.pi
    if degrees < 0 then
        degrees = degrees + 360
    end

    return degrees
end

tickruns = {}

local try_for = 5
function send_safe(code, data)
    local start = frame.time.utc()
    data = string.char(code) .. data
    while frame.time.utc() - start < try_for do
        local status, err = pcall(frame.bluetooth.send, data)
        if status then
            tx = tx + #data + 1
            return start
        end
        -- print("Error sending data: " .. err)
        frame.sleep(0.001)
    end
end

resolution = 512

function camera_click()
    frame.camera.capture({resolution=resolution, quality="VERY_HIGH"})
    print("Camera captured.")

    tickruns.capture = function()
        if not frame.camera.image_ready() then return end

        local data
        while true do
            data = frame.camera.read(frame.bluetooth.max_length() - 1)
            if data == nil then
                print("Done sending data")
                send_safe(MESSAGE_BASE + 3, "")
                tickruns.capture = nil
                collectgarbage('collect')
                return
            else
                -- print("Sending " .. #data .. " bytes of data")
                if send_safe(MESSAGE_BASE + 2, data) == nil then
                    print("Failed to send data")
                    while data ~= nil do
                        data = frame.camera.read(frame.bluetooth.max_length() - 1)
                    end

                    return
                else
                    tx = tx + #data + 1
                end
            end
        end
    end
end

function single_tap_callback()
    menu = {
        items={
            {text="Cancel", callback=function()
                send("Menu cancelled.")
                menu = nil
            end},
            {text="Capture image", callback=camera_click},
            {text="Reset calibration", callback=reset_calibration},
            {text="Load calibration", callback=load_calibration},
            {text="Save calibration", callback=save_calibration},
            {text="Run auto exposure", callback=auto_exposure},
            {text="Sleep Frame (tap to wake)", callback=function()
                send("Sleeping Frame...")
                frame.sleep()
            end},
            {text="Toggle microphone", callback=function()
                send_mic = not send_mic
                if send_mic then
                    send("Microphone enabled.")
                else
                    send("Microphone disabled.")
                end
            end},
            {text="Edit Display Settings", callback=function()
                local m = {items={
                    {text="Cancel", callback=function()
                        send("Display settings menu cancelled.")
                        menu = nil
                    end}
                }}
                for k,v in pairs(display_settings) do
                    if v then
                        what = "Disable " .. k
                    else
                        what = "Enable " .. k
                    end
                    m.items[#m.items + 1] = {
                        text=what .. " " .. tostring(v),
                        callback=function()
                            display_settings[k] = not v
                            send("Display setting " .. k .. " set to " .. tostring(display_settings[k]))

                            local settings_file = frame.file.open("display_settings.lua", "write")
                            if settings_file then
                                settings_file:write("return " .. print_table(display_settings))
                                settings_file:close()
                            end
                        end
                    }
                end
                menu = m
            end},
        },
        cursor=0.01
    }
end

exposure_settings = {
    exposure=0.1
}

function double_tap_callback()

end

function auto_exposure()
    send("Double tap detected - last tap was at " .. (frame.time.utc() - last_tap))
    local run = 0
    local last_expose = frame.time.utc()
    tickruns.auto = function()
        local time = frame.time.utc()
        if time - last_expose > 0.1 then
            run = run + 1
            frame.camera.auto(exposure_settings)
            last_expose = time

            if run > 30 then
                tickruns.auto = nil
                send("Auto exposure run complete.")
            else
                send("Auto exposure run " .. run .. " of " .. 30)
            end
        end
    end
end

function req(f)
    local stat, mod = pcall(require, f)
    if not stat then
        return require(f .. ".min")
    end
    return mod
end

function main()
local data = req("data")
function handle_data(data)
    local err2
    local comp, err = load("return " .. data)
    if comp == nil then
        comp, err = load(data)
        if comp == nil then
            send("Failed to compile ".. err)
            return
        end    
    end

    local results = {pcall(comp)}
    local success = table.remove(results, 1)
    if not success then
        send("Failed to run: " .. results[1])
    else
        if #results == 0 then
            send("")
        else
            --[[for i=1,#results do
                print(tostring(results[i]))
            end]]
            send(table.unpack(results))
        end
    end
end

data.parsers[MESSAGE_BASE] = function(data) return data end
data.parsers[MESSAGE_BASE + 1] = function(data)
    local parts = {}
    for part in data:gmatch("[^\n]+") do
        table.insert(parts, part)
    end
    return {tonumber(parts[1]), parts[2]}
end

function process_bluetooth()
    local ready = data.process_raw_items()
    if ready then
        if data.app_data[MESSAGE_BASE] ~= nil then
            local item = data.app_data[MESSAGE_BASE]
            handle_data(item)
            data.app_data[MESSAGE_BASE] = nil
            rx = rx + #item + 1
            collectgarbage('collect')
        end
        if data.app_data[MESSAGE_BASE + 1] ~= nil then
            local utc, zone = table.unpack(data.app_data[MESSAGE_BASE + 1])
            frame.time.utc(math.floor(utc))
            frame.time.zone(zone)
            data.app_data[MESSAGE_BASE + 1] = nil
            rx = rx + 40
            collectgarbage('collect')
        end
    end
end

menu = nil

function display_menu(imu)
    local roll = imu.roll
    local time = frame.time.utc()

    if math.abs(roll) < 10 then
        roll = 0
    elseif roll < 0 then
        roll = -1
    else
        roll = 1
    end
    local line = string.rep("\xFF", math.ceil(600 / 8))
    
    menu.cursor = menu.cursor or #menu.items
    menu.cursor = menu.cursor + roll * 0.45
    local fpart = menu.cursor - math.floor(menu.cursor + 0.5)
    
    for i=-2,5 do
        local index = math.floor(menu.cursor + i + 0.5) % #menu.items
        local item = menu.items[index + 1]
        local color = "WHITE"
        if i == 0 then
            color = "YELLOW"
        end
        local off = 51 + math.floor((i + 2 - fpart) * 50)
        if off > 400 then
            break
        end

        if item then
            frame.display.text(item.text, 20, off, {color=color})
        end

        frame.display.bitmap(1, off, 600, 2, 12, line)
    end

    frame.display.text(">", 1, 151, {color="YELLOW"})
end

display_settings = {
    compass=true,
    battery=true,
    date=true,
    tx_rx=true
}

local status, display_settings_saved = pcall(require, "display_settings")
if status then
    for k,v in pairs(display_settings_saved) do
        if display_settings[k] ~= nil then
            display_settings[k] = v
        end
    end
    display_settings_saved = nil
end

function display_screen(last_bat, imu)
    local bat = frame.battery_level()
    -- frame.display.bitmap(1, yOffset, width, 4, 0, bitmap)
    local screenDegrees = 20
    local offsetY = 0
    if display_settings.compass then
        if not calibration.calibrating then
            for off=0,screenDegrees do
                local char = nil
                local color = "WHITE"
                local deg = math.floor(imu.heading + off - screenDegrees / 2)
                if deg % 10 == 0 then
                    color = "RED"
                    if deg == 0 then
                        char = "N"
                    elseif deg == 90 then
                        char = "E"
                    elseif deg == 180 then
                        char = "S"
                    elseif deg == 270 then
                        char = "W"
                    else
                        char = tostring(deg)
                        color = "YELLOW"
                    end
                end
                if char ~= nil then
                    frame.display.text(char, 1 + off * 20, 1 + offsetY, {color=color})
                end
            end
            offsetY = offsetY + 50
        else
            if calibration.nPoints > 0 then
                frame.display.text("Calibrating... " .. calibration.nPoints .. " points", 1, 1 + offsetY)
                offsetY = offsetY + 50
            end
            frame.display.text("Please rotate your frame around", 1, 1 + offsetY)
            frame.display.text("the X, Y and Z axes.", 1, 51 + offsetY)
            offsetY = offsetY + 100
        end
    end

    if mic_started then
        frame.display.text("REC", 500, 50, {color="RED"})
    end

    if display_settings.battery then
        frame.display.text("Battery: [", 1, 1 + offsetY)
        frame.display.text(string.rep("|", last_bat // 5), 180, 1 + offsetY, {color="RED"})
        frame.display.text("] " .. string.format("%d", last_bat), 360, 1 + offsetY)
        offsetY = offsetY + 50
    end

    if display_settings.date then
        local frame_now = frame.time.date()
        local weekday = ({
            "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"
        })[frame_now.weekday + 1]
        local suffix = "AM"
        local hour = frame_now.hour
        if hour >= 12 then
            suffix = "PM"
            if hour > 12 then
                hour = hour - 12
            end
        elseif hour == 0 then
            hour = 12
        end
        frame.display.text(string.format("%02d:%02d:%02d %s %s", hour, frame_now.minute, frame_now.second, suffix, weekday), 1, offsetY + 1)
        offsetY = offsetY + 50
    end

    if display_settings.tx_rx then
        frame.display.text("TX: " .. math.ceil(tx / 1024) .. " RX: " .. math.ceil(rx / 1024), 1, offsetY + 1)
        offsetY = offsetY + 50
    end
    collectgarbage('collect')
end

print("Loaded.")
local last_bat = frame.battery_level()

local width = 50
local height = 50
collectgarbage('collect')

local width = 500
local yOffset = 80

collectgarbage('collect')

frame.imu.tap_callback(tap_callback)
send_mic = send_mic or true
mic_started = mic_started or false

mic_settings = {sample_rate=16000, bit_depth=16}

fps = 30

mic_fail = 0

local i = 0
while true do
    i = i + 1
    local _start = frame.time.utc()
    process_bluetooth()
    local imu
    if (calibration.calibrating and i % 20 == 0) or i % 30 == 0 then
        imu = frame.imu.direction()
        imu.heading = calculateTiltCompensatedHeading(frame.imu.raw())
    end
    if i % fps == 0 then
        if menu ~= nil then
            display_menu(imu)
        else
            display_screen(last_bat, imu)
        end
        frame.display.show()
    end
    if i % (fps * 10) == 0 then
        last_bat = frame.battery_level()
    end

    if last_tap ~= nil and menu ~= nil then
        local idx = math.floor((menu.cursor or #menu.items) + 0.5) % #menu.items + 1
        item = menu.items[idx]
        menu = nil
        item:callback()
        last_tap = nil
    end

    if last_tap ~= nil and frame.time.utc() - last_tap > 0.5 then
        single_tap_callback()
        last_tap = nil
    end

    for k,v in pairs(tickruns) do
        if v then
            local ok, err = xpcall(v, debug.traceback)
            if not ok then
                send("Error in tickrun " .. k .. ": " .. err .. "\n\n" .. debug.traceback())
                tickruns[k] = nil
            end
        end
    end

    if send_mic and not mic_started then
        mic_started = true
        local success = pcall(frame.microphone.start, mic_settings)
    elseif not send_mic and mic_started then
        mic_started = false
        pcall(frame.microphone.stop)
    elseif send_mic then
        while true do
            local success, data = pcall(frame.microphone.read, frame.bluetooth.max_length() - 1)
            if not success then
                mic_fail = mic_fail + 1
                print("Error reading microphone: " .. tostring(data))
                collectgarbage("collect")
                break
            end

            if data == "" then
                break
            else
                success = pcall(frame.bluetooth.send, string.char(MESSAGE_BASE + 4) .. data)
                if success then
                    mic_fail = mic_fail + 1
                else
                    tx = tx + #data + 1
                end
            end
            data = nil
            collectgarbage("collect")
        end
    end
    
    duration = frame.time.utc() - _start

    frame.sleep(0.001)
end
end

while true do
local status, glError = xpcall(main, debug.traceback)

local errF = frame.file.open("lua-repl-error.log", "append")
if errF then
    if glError then
        errF:write(glError .. "\n")
    end
    errF:close()
end

frame.imu.tap_callback(nil) -- disable tap callback

if glError then
    local redraw = true
    glError = glError
    print(glError)
    local errorLines = {}
    local lines = {}
    for word in glError:gmatch("%S+") do
        local last = lines[#lines]
        if not last or #last + #word + 1 > 25 then
            table.insert(lines, word)
        else
            lines[#lines] = last .. " " .. word
        end
    end
    for s=10,1,-1 do
        if redraw then
            for i=1,#lines do
                frame.display.text(lines[i] or "", 1, (i - 1) * 40 + 1, {color="WHITE"})
            end
            -- frame.display.text("Error in lua-repl.lua", 1, 1, {color="WHITE"})
            frame.display.text(tostring(s), 1, 1, {color="RED"})
            frame.display.show()
        end

        
        frame.sleep(1)
    end
end
end