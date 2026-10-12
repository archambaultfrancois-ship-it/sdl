-- Generic SDL wire dissector for Wireshark.
-- Supports complete SDL frames carried in one UDP datagram.

local sdl = Proto("sdl", "SDL Wire")
sdl.prefs.wire_endian = Pref.enum("Wire byte order", 0,
   "Byte order selected by the SDL sender.",
   { [0] = "Big endian", [1] = "Little endian" })

local f_type = ProtoField.string("sdl.type", "Root message")
local f_hash = ProtoField.uint32("sdl.schema_hash", "Schema hash", base.HEX)
local f_field_id = ProtoField.uint32("sdl.field_id", "Field ID", base.DEC)
local f_length = ProtoField.uint32("sdl.field_length", "Field payload length", base.DEC)
local f_value = ProtoField.string("sdl.value", "Value")
local f_error = ProtoField.string("sdl.error", "Decode error")
sdl.fields = { f_type, f_hash, f_field_id, f_length, f_value, f_error }

local function fail(message)
   error(message, 0)
end

local function u16(data, pos)
   if pos + 1 > #data then fail("truncated u16") end
   return data:byte(pos) * 256 + data:byte(pos + 1), pos + 2
end

local function u32(data, pos, little)
   if pos + 3 > #data then fail("truncated u32") end
   local a, b, c, d = data:byte(pos, pos + 3)
   if little then return d * 16777216 + c * 65536 + b * 256 + a, pos + 4 end
   return a * 16777216 + b * 65536 + c * 256 + d, pos + 4
end

local function text(data, pos)
   local size
   size, pos = u16(data, pos)
   if size > #data - pos + 1 then fail("truncated descriptor string") end
   local value = data:sub(pos, pos + size - 1)
   if value:find("\0", 1, true) then fail("NUL in descriptor string") end
   return value, pos + size
end

local function descriptor(data)
   local pos = 1
   if data:sub(1, 4) ~= "SDD1" then fail("unknown descriptor version") end
   pos = 5
   local schema = { messages = {}, enums = {} }
   schema.root, pos = text(data, pos)
   local count
   count, pos = u16(data, pos)
   for unused = 1, count do
      local message = { fields = {}, by_id = {} }
      message.name, pos = text(data, pos)
      local field_count
      field_count, pos = u16(data, pos)
      for index = 1, field_count do
         local field = {}
         field.id, pos = u32(data, pos, false)
         field.name, pos = text(data, pos)
         field.modifier = data:byte(pos)
         if not field.modifier then fail("truncated field modifier") end
         pos = pos + 1
         field.type, pos = text(data, pos)
         local dimensions = data:byte(pos)
         if not dimensions then fail("truncated dimensions") end
         pos = pos + 1
         field.dimensions = {}
         for dimension = 1, dimensions do
            field.dimensions[dimension], pos = u32(data, pos, false)
         end
         message.fields[index] = field
         message.by_id[field.id] = field
      end
      schema.messages[message.name] = message
   end
   count, pos = u16(data, pos)
   for unused = 1, count do
      local enum = {}
      enum.name, pos = text(data, pos)
      local item_count
      item_count, pos = u16(data, pos)
      enum.items = {}
      for index = 1, item_count do
         local name, value
         name, pos = text(data, pos)
         value, pos = u32(data, pos, false)
         enum.items[value] = name
      end
      schema.enums[enum.name] = enum
   end
   if pos ~= #data + 1 then fail("trailing descriptor data") end
   if not schema.messages[schema.root] then fail("root message is missing") end
   return schema
end

local function hash32(data)
   local bitops = bit32 or bit
   local hash = 2166136261
   for index = 1, #data do
      hash = bitops.bxor(hash, data:byte(index))
      -- FNV prime is 2^24 + 403; keep the arithmetic exactly within 32 bits.
      hash = (hash * 403 + (hash % 65536) * 65536) % 4294967296
   end
   return hash
end

local function read_number(data, pos, size, little)
   if pos + size - 1 > #data then fail("truncated numeric value") end
   local result = 0
   if little then
      for index = size - 1, 0, -1 do result = result * 256 + data:byte(pos + index) end
   else
      for index = 0, size - 1 do result = result * 256 + data:byte(pos + index) end
   end
   return result
end

local primitive_sizes = {
   bool = 1, int8 = 1, int16 = 2, int32 = 4, int64 = 8,
   fl32 = 4, fl64 = 8, c32 = 8, c64 = 16
}

local function proto_node(parent, data, label)
   local bytes = ByteArray.new(data, true)
   local tvb = bytes:tvb("SDL value")
   return parent:add(sdl, tvb(), label)
end

local function fixed_size(type_name, schema, depth)
   local size = primitive_sizes[type_name]
   if size then return size end
   if schema.enums[type_name] then return 4 end
   if depth > 32 then fail("message nesting is too deep") end
   local message = schema.messages[type_name]
   if not message then return nil end
   local total = 0
   for _, field in ipairs(message.fields) do
      if field.modifier ~= 0 or field.type == "string" then return nil end
      local item_size = fixed_size(field.type, schema, depth + 1)
      if not item_size then return nil end
      local count = 1
      for _, dimension in ipairs(field.dimensions) do count = count * dimension end
      total = total + 8 + item_size * count
   end
   return total
end

local function signed_decimal(value, bits)
   local half = 2 ^ (bits - 1)
   if value >= half then return string.format("%.0f", value - 2 ^ bits) end
   return string.format("%.0f", value)
end

local function render_value(type_name, data, schema, little)
   local size = primitive_sizes[type_name]
   if type_name == "string" then return string.format("%q", data) end
   if schema.enums[type_name] then
      local value = read_number(data, 1, 4, little)
      local enum_name = schema.enums[type_name].items[value]
      return enum_name and (enum_name .. " (" .. value .. ")") or tostring(value)
   end
   if type_name == "bool" then
      if #data ~= 1 or (data:byte(1) ~= 0 and data:byte(1) ~= 1) then
         fail("invalid bool value")
      end
      return data:byte(1) == 1 and "true" or "false"
   end
   if type_name == "fl32" or type_name == "fl64" or
      type_name == "c32" or type_name == "c64" then
      local width = (type_name == "fl32" or type_name == "c32") and 4 or 8
      local tvb = ByteArray.new(data, true):tvb("SDL value")
      local function float_at(offset)
         local range = tvb(offset, width)
         if little then
            if width == 4 then return range:le_float() end
            return range:le_double()
         end
         if width == 4 then return range:float() end
         return range:double()
      end
      if #data ~= size then fail("invalid floating point width") end
      if type_name == "c32" or type_name == "c64" then
         return "(" .. float_at(0) .. ", " .. float_at(width) .. ")"
      end
      return tostring(float_at(0))
   end
   if not size or #data ~= size then fail("invalid primitive width for " .. type_name) end
   if size == 8 then
      local bytes = ByteArray.new(data, true)
      local tvb = bytes:tvb("SDL int64")
      local range = tvb(0, 8)
      return tostring(little and range:le_int64() or range:int64())
   end
   local value = read_number(data, 1, size, little)
   if type_name:sub(1, 3) == "int" then return signed_decimal(value, size * 8) end
   return tostring(value)
end

local decode_body
local function add_field_tree(parent, field, payload, schema, little, depth)
   if depth > 32 then fail("message nesting is too deep") end
   local label = field.name .. " (" .. field.type .. ")"
   local node = proto_node(parent, payload, label)
   node:add(f_field_id, field.id)
   node:add(f_length, #payload)
   local message = schema.messages[field.type]
   if #field.dimensions > 0 or field.modifier == 3 then
      local item_size = fixed_size(field.type, schema, depth + 1)
      if not item_size or item_size == 0 or #payload % item_size ~= 0 then
         fail("invalid fixed array size")
      end
      local count = #payload / item_size
      if #field.dimensions > 0 then
         local expected = 1
         for _, dimension in ipairs(field.dimensions) do expected = expected * dimension end
         if count ~= expected then fail("fixed array element count mismatch") end
      end
      local array = proto_node(node, payload, "Elements (" .. count .. ")")
      for index = 0, count - 1 do
         local start = index * item_size + 1
         local item = payload:sub(start, start + item_size - 1)
         if message then
            local element = proto_node(array, item, "Element " .. (index + 1))
            decode_body(element, item, message, schema, little, depth + 1)
         else
            array:add(f_value, render_value(field.type, item, schema, little))
         end
      end
   elseif message then
      decode_body(node, payload, message, schema, little, depth + 1)
   else
      node:add(f_value, render_value(field.type, payload, schema, little))
   end
end

decode_body = function(parent, data, message, schema, little, depth)
   if depth > 32 then fail("message nesting is too deep") end
   local pos = 1
   while pos <= #data do
      if #data - pos + 1 < 8 then fail("truncated field header") end
      local id, next_pos = u32(data, pos, little)
      local length
      length, pos = u32(data, next_pos, little)
      if length > #data - pos + 1 then fail("truncated field payload") end
      local payload = data:sub(pos, pos + length - 1)
      local field = message.by_id[id]
      if field then
         add_field_tree(parent, field, payload, schema, little, depth)
      else
         parent:add(f_field_id, id):append_text(" (unknown; skipped " .. length .. " bytes)")
      end
      pos = pos + length
   end
end

function sdl.dissector(tvb, pinfo, tree)
   local data = tvb:raw()
   pinfo.cols.protocol = "SDL"
   local root = tree:add(sdl, tvb(), "SDL Wire")
   local ok, message = pcall(function()
      local little = sdl.prefs.wire_endian == 1
      if #data < 8 then fail("truncated SDL frame") end
      local descriptor_size, pos = u32(data, 1, little)
      if descriptor_size > #data - 8 then fail("truncated SDL descriptor") end
      local descriptor_bytes = data:sub(5, 4 + descriptor_size)
      local schema = descriptor(descriptor_bytes)
      pos = 5 + descriptor_size
      local expected_hash
      expected_hash, pos = u32(data, pos, little)
      if expected_hash ~= hash32(descriptor_bytes) then fail("SDL descriptor hash mismatch") end
      root:add(f_type, schema.root)
      root:add(f_hash, expected_hash)
      decode_body(root, data:sub(pos), schema.messages[schema.root], schema, little, 0)
      pinfo.cols.info = schema.root
   end)
   if not ok then
      root:add(f_error, tostring(message))
      pinfo.cols.info = "Malformed SDL"
   end
end

DissectorTable.get("udp.port"):add_for_decode_as(sdl)
