-- SDL2: a catalogue announcement followed by positional data datagrams.
local sdl = Proto("sdl", "SDL2 Wire")
local f_type = ProtoField.string("sdl.type", "Message type")
local f_id = ProtoField.uint32("sdl.type_id", "Message type ID", base.DEC)
local f_description = ProtoField.string("sdl.description", "Catalogue")
local f_value = ProtoField.string("sdl.value", "Value")
local f_error = ProtoField.string("sdl.error", "Decode error")
sdl.fields = {f_type, f_id, f_description, f_value, f_error}
local catalogues = {}
function sdl.init() catalogues = {} end
local function fail(text) error(text, 0) end
local function utf8(text)
   local i = 1
   while i <= #text do
      local b = text:byte(i); i = i + 1
      if b >= 128 then
         local n, value, minimum
         if b >= 194 and b <= 223 then n, value, minimum = 1, b % 32, 128
         elseif b >= 224 and b <= 239 then n, value, minimum = 2, b % 16, 2048
         elseif b >= 240 and b <= 244 then n, value, minimum = 3, b % 8, 65536
         else fail("invalid UTF-8") end
         for unused = 1, n do
            local c = text:byte(i); i = i + 1
            if not c or c < 128 or c > 191 then fail("invalid UTF-8 continuation") end
            value = value * 64 + c % 64
         end
         if value < minimum or value > 1114111 or (value >= 55296 and value <= 57343) then fail("invalid UTF-8 scalar") end
      end
   end
end
local widths = {bool=1, int8=1, int16=2, int32=4, int64=8, fl32=4, fl64=8, c32=8, c64=16}
local function descriptor(text)
   utf8(text)
   if #text > 1048576 or text:sub(1,5) ~= "SDL2\n" or text:sub(-1) ~= "\n" or text:find("\0",1,true) then fail("expected SDL2 catalogue") end
   local schema = {messages={}, enums={}, names={}, sizes={}, heights={}}
   local current, kind, previous_message, previous_enum = nil, nil, "", ""
   for line in text:sub(6):gmatch("([^\n]*)\n") do
      if not current then
         local k, name = line:match("^(%w+) ([%w_$]+) {$")
         if not k or (k ~= "message" and k ~= "enum") or widths[name] or name == "string" then fail("invalid declaration") end
         kind = k
         if k == "message" then
            if name <= previous_message then fail("message order") end; previous_message = name
            current = {name=name, fields={}, ids={}, field_names={}}; schema.messages[name] = current
            schema.names[#schema.names+1] = name
         else
            if name <= previous_enum then fail("enum order") end; previous_enum = name
            current = {name=name, values={}, names={}, count=0}; schema.enums[name] = current
         end
      elseif line == "}" then
         if kind == "enum" and current.count == 0 then fail("empty enum") end; current = nil
      elseif kind == "message" then
         local id, modifier, name, dimensions, field = line:match("^  (%d+): (%w+) ([%w_$]+)([%[%]%d]*) ([%w_$]+);$")
         id = tonumber(id)
         if not id or id < 1 or id > 4294967295 or (modifier ~= "required" and modifier ~= "optional" and modifier ~= "repeated" and modifier ~= "packed") then fail("invalid field") end
         local dims = {}; local rebuilt = ""
         for n in dimensions:gmatch("%[(%d+)%]") do
            rebuilt = rebuilt .. "[" .. n .. "]"; n = tonumber(n)
            if n < 1 or n > 4294967295 or #dims >= 255 then fail("invalid dimension") end; dims[#dims+1] = n
         end
         if rebuilt ~= dimensions or (#dims > 0 and modifier ~= "required") or current.field_names[field] or (#current.fields > 0 and id <= current.fields[#current.fields].id) then fail("invalid field metadata") end
         current.field_names[field] = true; current.fields[#current.fields+1] = {id=id, modifier=modifier, type=name, dims=dims, name=field}
      else
         local name, n = line:match("^  ([%w_$]+) = (%-?%d+);$"); n = tonumber(n)
         if not n or n < -2147483648 or n > 2147483647 or current.values[n] or current.names[name] then fail("invalid enum") end
         current.values[n] = name; current.names[name] = true; current.count = current.count + 1
      end
   end
   if current or #schema.names == 0 then fail("unclosed or empty catalogue") end
   local active = {}
   local function size(name, depth)
      if depth > 64 or active[name] then fail("recursive or deep schema") end
      if widths[name] then return widths[name] end
      if schema.enums[name] then return 4 end
      if name == "string" then return false end
      if schema.sizes[name] ~= nil then
         if depth+schema.heights[name]>64 then fail("deep schema") end
         return schema.sizes[name]
      end
      local m = schema.messages[name]; if not m then fail("unknown type") end
      active[name] = true; local total, fixed, height = 0, #m.fields > 0, 0
      for _, f in ipairs(m.fields) do
         local n = size(f.type, depth+1)
         if schema.messages[f.type] then height=math.max(height,1+schema.heights[f.type]) end
         if #f.dims > 0 or f.modifier == "packed" then
            if not n then fail("variable fixed element") end
         end
         if not n or f.modifier ~= "required" then fixed = false end
         if n then for _, d in ipairs(f.dims) do n = n*d; if n > 4294967295 then fail("array overflow") end end; total = total+n end
      end
      if depth+height>64 or (fixed and total>4294967295) then fail("deep or oversized schema") end
      active[name] = nil; schema.heights[name]=height;schema.sizes[name] = fixed and total or false; return schema.sizes[name]
   end
   for _, name in ipairs(schema.names) do
      if schema.enums[name] then fail("ambiguous type") end; size(name,0)
   end
   return schema
end
local function channel(pinfo)
   return tostring(pinfo.src) .. ":" .. tostring(pinfo.src_port) .. ">" .. tostring(pinfo.dst) .. ":" .. tostring(pinfo.dst_port)
end
local function decode(tvb, schema, tree)
   local pos, length = 0, tvb:len()
   local function take(n)
      if n > length-pos then fail("truncated payload") end
      local range = tvb(pos,n); pos = pos+n; return range
   end
   local function count()
      local n=0
      for i=0,4 do
         local b=take(1):uint(); if i==4 and b>15 then fail("counter overflow") end
         n=n+(b%128)*2^(7*i)
         if b<128 then if i>0 and b==0 then fail("noncanonical counter") end; return n end
      end
      fail("invalid counter")
   end
   local message, value
   value = function(name, dims, node, label, depth)
      if depth>64 then fail("nesting too deep") end
      if #dims>0 then
         local rest={};for i=2,#dims do rest[#rest+1]=dims[i] end
         for i=1,dims[1] do value(name,rest,node,label.."["..(i-1).."]",depth+1) end
      elseif schema.messages[name] then
         local child=node:add(f_value,label.." ("..name..")"); message(name,child,depth+1)
      elseif name=="string" then
         local bytes=take(count()); local text=bytes:raw();utf8(text);node:add(f_value,bytes,label.." = "..string.format("%q",text))
      else
         local bytes=take(widths[name] or 4);local text
         if schema.enums[name] then local n=bytes:int();text=schema.enums[name].values[n];if not text then fail("invalid enum") end
         elseif name=="bool" then local n=bytes:uint();if n>1 then fail("invalid bool") end;text=n==1 and "true" or "false"
         elseif name=="fl32" or name=="fl64" then text=tostring(bytes:float())
         elseif name=="c32" then text=tostring(bytes:range(0,4):float())..", "..tostring(bytes:range(4,4):float())
         elseif name=="c64" then text=tostring(bytes:range(0,8):float())..", "..tostring(bytes:range(8,8):float())
         elseif name=="int64" then text=tostring(bytes:int64())
         else text=tostring(bytes:int()) end
         node:add(f_value,bytes,label.." = "..text)
      end
   end
   message = function(name,node,depth)
      for _, f in ipairs(schema.messages[name].fields) do
         local n=f.modifier=="required" and 1 or count()
         if f.modifier=="optional" and n>1 then fail("optional count exceeds one") end
         local unit=widths[f.type] or (schema.enums[f.type] and 4) or schema.sizes[f.type]
         if unit then for _, d in ipairs(f.dims) do unit=unit*d end;if n>math.floor((length-pos)/unit) then fail("truncated array") end
         elseif n>1048576 then fail("variable array too large") end
         if n==0 then node:add(f_value,f.name.." = "..(f.modifier=="optional" and "absent" or "[]")) end
         for i=1,n do value(f.type,f.dims,node,f.name..(n>1 and "["..(i-1).."]" or ""),depth+1) end
      end
   end
   local id=count();local name=schema.names[id];if not name then fail("unknown type ID") end
   tree:add(f_id,tvb(0,pos),id);tree:add(f_type,name);message(name,tree,0)
   if pos~=length then fail("trailing bytes") end;return name
end
local function catalogue_at(key, frame)
   local latest, selected = 0, nil
   for number, schema in pairs(catalogues[key] or {}) do
      if number <= frame and number > latest then latest, selected = number, schema end
   end
   return selected
end
function sdl.dissector(tvb,pinfo,tree)
   pinfo.cols.protocol="SDL2";local root=tree:add(sdl,tvb());local key=channel(pinfo)
   local ok,result=pcall(function()
      if tvb:len()>=5 and tvb(0,5):raw()=="SDL2\n" then
         local text=tvb():raw();local schema=descriptor(text)
         if not catalogues[key] then catalogues[key]={} end
         catalogues[key][pinfo.number]=schema;root:add(f_description,text);return "Catalogue announcement"
      end
      local schema=catalogue_at(key,pinfo.number)
      if not schema then fail("catalogue announcement missing from capture") end
      return decode(tvb,schema,root)
   end)
   if ok then pinfo.cols.info=result else root:add(f_error,tostring(result));pinfo.cols.info="SDL2 decode error" end
end
sdl:register_heuristic("udp",function(tvb,pinfo,tree)
   if (tvb:len()>=5 and tvb(0,5):raw()=="SDL2\n") or catalogues[channel(pinfo)] then sdl.dissector(tvb,pinfo,tree);return true end
   return false
end)
DissectorTable.get("udp.port"):add_for_decode_as(sdl)
