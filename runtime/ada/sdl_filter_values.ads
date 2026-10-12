with Ada.Finalization;
with Interfaces;
with Interfaces.C;
with System;
with Ada.Strings.Unbounded;
package SDL_Filter_Values is
   type Byte_Slice is record
      Data : System.Address := System.Null_Address;
      Size : Interfaces.C.size_t := 0;
   end record with Convention => C_Pass_By_Copy;
   type Step is record
      Kind, Id : Interfaces.Unsigned_32 := 0;
      Index : Interfaces.C.size_t := 0;
      Owner, Name : Byte_Slice;
   end record with Convention => C;
   type Path is array (Positive range <>) of Step with Convention => C;
   type Value is record
      Kind, Reserved : Interfaces.Unsigned_32 := 0;
      Signed_Value : Interfaces.Integer_64 := 0;
      Unsigned_Value : Interfaces.Unsigned_64 := 0;
      Re, Im : Interfaces.IEEE_Float_64 := 0.0;
      Bytes : Byte_Slice;
      Count : Interfaces.C.size_t := 0;
   end record with Convention => C;
   type Scratch is new Ada.Finalization.Limited_Controlled with private;
   overriding procedure Finalize (S : in out Scratch);
   function Missing return Value;
   function Invalid return Value;
   function Message_Value return Value;
   function Array_Value (N : Interfaces.C.size_t) return Value;
   function Bool_Value (V : Boolean) return Value;
   function Signed (V : Interfaces.Integer_64) return Value;
   function Unsigned (V : Interfaces.Unsigned_64) return Value;
   function Floating (V : Interfaces.IEEE_Float_64) return Value;
   function Complex (Re, Im : Interfaces.IEEE_Float_64) return Value;
   function Text_Value (S : in out Scratch; Slot : Natural;
      V : Ada.Strings.Unbounded.Unbounded_String) return Value;
   function Owner_Is (S : Step; Name : String) return Boolean;
private
   type String_Access is access String;
   type Buffers is array (0 .. 127) of String_Access;
   type Scratch is new Ada.Finalization.Limited_Controlled with record
      Texts : Buffers := (others => null);
   end record;
end SDL_Filter_Values;
