with Ada.Unchecked_Conversion;
with Interfaces.C;
with Interfaces.C.Strings;
with Ada.Strings.Unbounded; use Ada.Strings.Unbounded;
package body SDL_Runtime is
   use Interfaces;
   use type System.Address;
   use type Interfaces.C.int;
   use type Interfaces.C.size_t;
   function C_Prepare (R : System.Address; RN : Interfaces.C.size_t; L : System.Address; LN : Interfaces.C.size_t) return System.Address
     with Import, Convention => C, External_Name => "sdl_ada_prepare";
   procedure C_Retain (P : System.Address) with Import, Convention => C, External_Name => "sdl_ada_retain";
   procedure C_Release (P : System.Address) with Import, Convention => C, External_Name => "sdl_ada_release";
   function C_Id (P : System.Address; N : Interfaces.C.char_array) return Unsigned_32 with Import, Convention => C, External_Name => "sdl_ada_id";
   function C_Exact (P : System.Address; N : Interfaces.C.char_array) return Interfaces.C.int with Import, Convention => C, External_Name => "sdl_ada_exact";
   function C_Direct (P : System.Address; N : Interfaces.C.char_array) return Interfaces.C.int
     with Import, Convention => C, External_Name => "sdl_ada_direct";
   function C_Validate (P, D : System.Address; N : Interfaces.C.size_t) return Interfaces.C.int with Import, Convention => C, External_Name => "sdl_ada_validate";
   function C_Decode (P, D : System.Address; N : Interfaces.C.size_t) return Value with Import, Convention => C, External_Name => "sdl_ada_decode";
   function C_Name (Tree : Value) return Interfaces.C.Strings.chars_ptr with Import, Convention => C, External_Name => "sdl_ada_name";
   function C_Field (P, T : System.Address; Id : Unsigned_32) return Value with Import, Convention => C, External_Name => "sdl_ada_field";
   function C_Kind (V : Value) return Interfaces.C.int with Import, Convention => C, External_Name => "sdl_ada_kind";
   function C_Integer (V : Value) return Int64 with Import, Convention => C, External_Name => "sdl_ada_integer";
   function C_Float (V : Value) return Float64 with Import, Convention => C, External_Name => "sdl_ada_float";
   function C_Re (V : Value) return Float64 with Import, Convention => C, External_Name => "sdl_ada_real";
   function C_Im (V : Value) return Float64 with Import, Convention => C, External_Name => "sdl_ada_imag";
   function C_Bool (V : Value) return Interfaces.C.int with Import, Convention => C, External_Name => "sdl_ada_bool";
   function C_String (V : Value) return System.Address with Import, Convention => C, External_Name => "sdl_ada_string";
   function C_String_Size (V : Value) return Interfaces.C.size_t with Import, Convention => C, External_Name => "sdl_ada_string_size";
   function C_Length (V : Value) return Interfaces.C.size_t with Import, Convention => C, External_Name => "sdl_ada_length";
   function C_Item (V : Value; N : Interfaces.C.size_t) return Value with Import, Convention => C, External_Name => "sdl_ada_item";
   function C_Message (V : Value) return Value with Import, Convention => C, External_Name => "sdl_ada_message";
   function C_UTF8 (P : System.Address; N : Interfaces.C.size_t) return Interfaces.C.int with Import, Convention => C, External_Name => "sdl_ada_utf8";
   function Bits8 is new Ada.Unchecked_Conversion (Int8, Unsigned_8);
   function Bits16 is new Ada.Unchecked_Conversion (Int16, Unsigned_16);
   function Bits32 is new Ada.Unchecked_Conversion (Int32, Unsigned_32);
   function Bits64 is new Ada.Unchecked_Conversion (Int64, Unsigned_64);
   function Float_Bits32 is new Ada.Unchecked_Conversion (Float32, Unsigned_32);
   function Float_Bits64 is new Ada.Unchecked_Conversion (Float64, Unsigned_64);
   function Signed8 is new Ada.Unchecked_Conversion (Unsigned_8, Int8);
   function Signed16 is new Ada.Unchecked_Conversion (Unsigned_16, Int16);
   function Signed32 is new Ada.Unchecked_Conversion (Unsigned_32, Int32);
   function Signed64 is new Ada.Unchecked_Conversion (Unsigned_64, Int64);
   function Floating32 is new Ada.Unchecked_Conversion (Unsigned_32, Float32);
   function Floating64 is new Ada.Unchecked_Conversion (Unsigned_64, Float64);
   overriding procedure Adjust (C : in out Context) is begin C_Retain (C.Handle); end;
   overriding procedure Finalize (C : in out Context) is begin C_Release (C.Handle); C.Handle := System.Null_Address; end;
   function Prepare (Remote, Local : String; Use_Direct : Boolean := True) return Context is
   begin
      return Result : Context do
         Result.Direct_Enabled := Use_Direct;
         Result.Handle := C_Prepare (Remote'Address, Remote'Length, Local'Address, Local'Length);
         if Result.Handle = System.Null_Address then raise Codec_Error with "invalid or incompatible catalogue"; end if;
      end return;
   end;
   function Message_Id (C : Context; Name : String) return Unsigned_32 is
      Id : constant Unsigned_32 := C_Id (C.Handle, Interfaces.C.To_C (Name));
   begin if Id = 0 then raise Codec_Error with "unknown message"; end if; return Id; end;
   procedure Check_Encode (C : Context; Name : String) is
   begin if C_Exact (C.Handle, Interfaces.C.To_C (Name)) = 0 then raise Codec_Error with "encoding requires matching fields"; end if; end;
   procedure Check_Wire (C : Context; Data : Bytes) is
   begin if C_Validate (C.Handle, Data'Address, Data'Length) = 0 then raise Codec_Error with "invalid message"; end if; end;
   function Decode_Tree (C : Context; Data : Bytes) return Value is
      Tree : constant Value := C_Decode (C.Handle, Data'Address, Data'Length);
   begin if Tree = System.Null_Address then raise Codec_Error with "invalid message"; end if; return Tree; end;
   function Name_Of (Tree : Value) return String is begin return Interfaces.C.Strings.Value (C_Name (Tree)); end;
   function Field (C : Context; Tree : Value; Id : Unsigned_32) return Value is begin return C_Field (C.Handle, Tree, Id); end;
   function Present (V : Value) return Boolean is begin return C_Kind (V) /= 0; end;
   procedure Expect (V : Value; Kind : Interfaces.C.int) is
   begin if C_Kind (V) /= Kind then raise Codec_Error with "wrong dynamic value kind"; end if; end;
   function Length (V : Value) return Natural is
   begin Expect (V, 7); if C_Length (V) > Interfaces.C.size_t (Natural'Last) then raise Codec_Error with "array too large"; end if; return Natural (C_Length (V)); end;
   function Item (V : Value; Index : Natural) return Value is
   begin if Index >= Length (V) then raise Codec_Error with "array index out of bounds"; end if; return C_Item (V, Interfaces.C.size_t (Index)); end;
   function Message (V : Value) return Value is begin Expect (V, 8); return C_Message (V); end;
   function Read (V : Value) return Boolean is begin Expect (V, 1); return C_Bool (V) /= 0; end;
   function Read (V : Value) return Int64 is
   begin if C_Kind (V) /= 2 and C_Kind (V) /= 6 then raise Codec_Error; end if; return C_Integer (V); end;
   function Read (V : Value) return Int8 is begin return Int8 (Int64'(Read (V))); end;
   function Read (V : Value) return Int16 is begin return Int16 (Int64'(Read (V))); end;
   function Read (V : Value) return Int32 is begin return Int32 (Int64'(Read (V))); end;
   function Read (V : Value) return Float64 is begin Expect (V, 3); return C_Float (V); end;
   function Read (V : Value) return Float32 is begin return Float32 (Float64'(Read (V))); end;
   function Read (V : Value) return Complex32 is begin Expect (V, 4); return (Float32 (C_Re (V)), Float32 (C_Im (V))); end;
   function Read (V : Value) return Complex64 is begin Expect (V, 4); return (C_Re (V), C_Im (V)); end;
   function Read (V : Value) return Text is
      N : Interfaces.C.size_t;
   begin Expect (V, 5); N := C_String_Size (V); if N = 0 then return Null_Unbounded_String; end if;
      declare
         S : Interfaces.C.char_array (0 .. N - 1) with Import, Address => C_String (V);
      begin
         return To_Unbounded_String (Interfaces.C.To_Ada (S, Trim_Nul => False));
      end;
   end;
   function To_Bytes (B : Buffer) return Bytes is
      Result : Bytes (1 .. Natural (B.Length));
   begin for I in Result'Range loop Result (I) := B (I); end loop; return Result; end;
   procedure Put_Count (B : in out Buffer; N : Unsigned_32) is
      X : Unsigned_32 := N;
   begin while X >= 128 loop B.Append (Unsigned_8 (X and 127) or 128); X := Shift_Right (X, 7); end loop; B.Append (Unsigned_8 (X)); end;
   procedure Word (B : in out Buffer; X : Unsigned_64; Width : Positive) is
   begin for I in reverse 0 .. Width - 1 loop B.Append (Unsigned_8 (Shift_Right (X, I * 8) and 255)); end loop; end;
   procedure Write (B : in out Buffer; V : Boolean) is begin B.Append (if V then 1 else 0); end;
   procedure Write (B : in out Buffer; V : Int8) is begin Word (B, Unsigned_64 (Bits8 (V)), 1); end;
   procedure Write (B : in out Buffer; V : Int16) is begin Word (B, Unsigned_64 (Bits16 (V)), 2); end;
   procedure Write (B : in out Buffer; V : Int32) is begin Word (B, Unsigned_64 (Bits32 (V)), 4); end;
   procedure Write (B : in out Buffer; V : Int64) is begin Word (B, Bits64 (V), 8); end;
   procedure Write (B : in out Buffer; V : Float32) is begin Word (B, Unsigned_64 (Float_Bits32 (V)), 4); end;
   procedure Write (B : in out Buffer; V : Float64) is begin Word (B, Float_Bits64 (V), 8); end;
   procedure Write (B : in out Buffer; V : Complex32) is begin Write (B, V.Re); Write (B, V.Im); end;
   procedure Write (B : in out Buffer; V : Complex64) is begin Write (B, V.Re); Write (B, V.Im); end;
   procedure Write (B : in out Buffer; V : Text) is
      S : constant String := To_String (V);
   begin if C_UTF8 (S'Address, S'Length) = 0 then raise Codec_Error with "invalid UTF-8"; end if;
      Put_Count (B, Unsigned_32 (S'Length)); for Ch of S loop B.Append (Character'Pos (Ch)); end loop;
   end;
   function Direct_Layout (C : Context; Name : String) return Boolean is
   begin return C.Direct_Enabled and then C_Direct (C.Handle, Interfaces.C.To_C (Name)) /= 0; end;
   function Count_Size (N : Unsigned_32) return Positive is
      X : Unsigned_32 := N;
      Size : Positive := 1;
   begin
      while X >= 128 loop Size := Size + 1; X := Shift_Right (X, 7); end loop;
      return Size;
   end;
   procedure Put_Count (Data : in out Bytes; Position : in out Natural; N : Unsigned_32) is
      X : Unsigned_32 := N;
   begin
      while X >= 128 loop
         Data (Position) := Unsigned_8 (X and 127) or 128;
         Position := Position + 1; X := Shift_Right (X, 7);
      end loop;
      Data (Position) := Unsigned_8 (X); Position := Position + 1;
   end;
   function Get_Count (Data : Bytes; Position : in out Natural) return Unsigned_32 is
      N : Unsigned_32 := 0;
      B : Unsigned_8;
   begin
      for I in 0 .. 4 loop
         B := Data (Position); Position := Position + 1;
         if I = 4 and B > 15 then raise Codec_Error with "counter overflow"; end if;
         N := N or Shift_Left (Unsigned_32 (B and 127), I * 7);
         if B < 128 then
            if I > 0 and B = 0 then raise Codec_Error with "noncanonical counter"; end if;
            return N;
         end if;
      end loop;
      raise Codec_Error with "invalid counter";
   end;
   procedure Put_Word (Data : in out Bytes; Position : in out Natural; N : Unsigned_64; Width : Positive)
     with Inline_Always;
   procedure Put_Word (Data : in out Bytes; Position : in out Natural; N : Unsigned_64; Width : Positive) is
   begin
      for I in reverse 0 .. Width - 1 loop
         Data (Position) := Unsigned_8 (Shift_Right (N, I * 8) and 255);
         Position := Position + 1;
      end loop;
   end;
   function Get_Word (Data : Bytes; Position : in out Natural; Width : Positive) return Unsigned_64
     with Inline_Always;
   function Get_Word (Data : Bytes; Position : in out Natural; Width : Positive) return Unsigned_64 is
      N : Unsigned_64 := 0;
   begin
      for I in 1 .. Width loop
         N := Shift_Left (N, 8) or Unsigned_64 (Data (Position)); Position := Position + 1;
      end loop;
      return N;
   end;
   procedure Put (Data : in out Bytes; Position : in out Natural; V : Boolean) is
   begin Data (Position) := (if V then 1 else 0); Position := Position + 1; end;
   procedure Get (Data : Bytes; Position : in out Natural; V : out Boolean) is
      B : constant Unsigned_8 := Data (Position);
   begin
      if B > 1 then raise Codec_Error with "invalid bool"; end if;
      V := B = 1; Position := Position + 1;
   end;
   procedure Put (Data : in out Bytes; Position : in out Natural; V : Int8) is
   begin Put_Word (Data, Position, Unsigned_64 (Bits8 (V)), 1); end;
   procedure Get (Data : Bytes; Position : in out Natural; V : out Int8) is
   begin V := Signed8 (Unsigned_8 (Get_Word (Data, Position, 1))); end;
   procedure Put (Data : in out Bytes; Position : in out Natural; V : Int16) is
   begin Put_Word (Data, Position, Unsigned_64 (Bits16 (V)), 2); end;
   procedure Get (Data : Bytes; Position : in out Natural; V : out Int16) is
   begin V := Signed16 (Unsigned_16 (Get_Word (Data, Position, 2))); end;
   procedure Put (Data : in out Bytes; Position : in out Natural; V : Int32) is
   begin Put_Word (Data, Position, Unsigned_64 (Bits32 (V)), 4); end;
   procedure Get (Data : Bytes; Position : in out Natural; V : out Int32) is
   begin V := Signed32 (Unsigned_32 (Get_Word (Data, Position, 4))); end;
   procedure Put (Data : in out Bytes; Position : in out Natural; V : Int64) is
   begin Put_Word (Data, Position, Unsigned_64 (Bits64 (V)), 8); end;
   procedure Get (Data : Bytes; Position : in out Natural; V : out Int64) is
   begin V := Signed64 (Unsigned_64 (Get_Word (Data, Position, 8))); end;
   procedure Put (Data : in out Bytes; Position : in out Natural; V : Float32) is
   begin Put_Word (Data, Position, Unsigned_64 (Float_Bits32 (V)), 4); end;
   procedure Get (Data : Bytes; Position : in out Natural; V : out Float32) is
   begin V := Floating32 (Unsigned_32 (Get_Word (Data, Position, 4))); end;
   procedure Put (Data : in out Bytes; Position : in out Natural; V : Complex32) is
   begin Put (Data, Position, V.Re); Put (Data, Position, V.Im); end;
   procedure Get (Data : Bytes; Position : in out Natural; V : out Complex32) is
   begin Get (Data, Position, V.Re); Get (Data, Position, V.Im); end;
   procedure Put (Data : in out Bytes; Position : in out Natural; V : Float64) is
   begin Put_Word (Data, Position, Unsigned_64 (Float_Bits64 (V)), 8); end;
   procedure Get (Data : Bytes; Position : in out Natural; V : out Float64) is
   begin V := Floating64 (Unsigned_64 (Get_Word (Data, Position, 8))); end;
   procedure Put (Data : in out Bytes; Position : in out Natural; V : Complex64) is
   begin Put (Data, Position, V.Re); Put (Data, Position, V.Im); end;
   procedure Get (Data : Bytes; Position : in out Natural; V : out Complex64) is
   begin Get (Data, Position, V.Re); Get (Data, Position, V.Im); end;
   procedure Put (Data : in out Bytes; Position : in out Natural; V : Text) is
      S : constant String := To_String (V);
   begin
      if C_UTF8 (S'Address, S'Length) = 0 then raise Codec_Error with "invalid UTF-8"; end if;
      Put_Count (Data, Position, Unsigned_32 (S'Length));
      for Ch of S loop Data (Position) := Character'Pos (Ch); Position := Position + 1; end loop;
   end;
   procedure Get (Data : Bytes; Position : in out Natural; V : out Text) is
      N : constant Natural := Natural (Get_Count (Data, Position));
   begin
      if N > Data'Length or else Position - Data'First > Data'Length - N then
         raise Codec_Error with "truncated string";
      end if;
      declare S : String (1 .. N); begin
         for I in S'Range loop S (I) := Character'Val (Data (Position)); Position := Position + 1; end loop;
         if C_UTF8 (S'Address, S'Length) = 0 then raise Codec_Error with "invalid UTF-8"; end if;
         V := To_Unbounded_String (S);
      end;
   end;
end SDL_Runtime;
