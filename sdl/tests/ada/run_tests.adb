with Ada.Text_IO;
with Ada.Strings.Fixed;
with Ada.Unchecked_Conversion;
with Ada.Streams.Stream_IO;
with Ada.Streams;
with Ada.Strings.Unbounded; use Ada.Strings.Unbounded;
with Interfaces; use Interfaces;
with SDL_Runtime; use SDL_Runtime;
with SDL_Wire_Example; use SDL_Wire_Example;
with SDL_Codec_Cases; use SDL_Codec_Cases;
with SDL_Schema; use SDL_Schema;
with SDL_Empty_Message; use SDL_Empty_Message;
with SDL_Packed_Validation; use SDL_Packed_Validation;
with SDL_Benchmark;
with SDL_Bench_Cases;
with SDL_Auto_Fixed; use SDL_Auto_Fixed;
procedure Run_Tests is
   use type Ada.Streams.Stream_Element;
   Passed : Natural := 0;
   procedure Check (Condition : Boolean; Label_Text : String) is
   begin
      if not Condition then raise Program_Error with Label_Text; end if;
      Passed := Passed + 1;
   end;
   function Load (Path : String) return Bytes is
      F : Ada.Streams.Stream_IO.File_Type;
   begin
      Ada.Streams.Stream_IO.Open (F, Ada.Streams.Stream_IO.In_File, Path);
      declare
         S : Ada.Streams.Stream_Element_Array (1 .. Ada.Streams.Stream_Element_Offset (Ada.Streams.Stream_IO.Size (F)));
         Last : Ada.Streams.Stream_Element_Offset;
         B : Bytes (1 .. S'Length);
      begin
         Ada.Streams.Stream_IO.Read (F, S, Last);
         Ada.Streams.Stream_IO.Close (F);
         for I in B'Range loop B (I) := Unsigned_8 (S (Ada.Streams.Stream_Element_Offset (I))); end loop;
         return B;
      end;
   end;
   function Catalogue (Path : String) return String is
      Data : constant Bytes := Load (Path);
      Result : String (1 .. Data'Length);
   begin
      for I in Result'Range loop Result (I) := Character'Val (Data (I)); end loop;
      return Result;
   end;
   procedure Compatibility_Cases is
      File : Ada.Text_IO.File_Type;
      type Columns is array (1 .. 12) of Text;
      function Plain (S : Text) return String is
      begin if To_String (S) = "-" then return ""; end if; return To_String (S); end;
   begin
      Ada.Text_IO.Open (File, Ada.Text_IO.In_File, "tests/fixtures/compatibility/cases.tsv");
      while not Ada.Text_IO.End_Of_File (File) loop
         declare
            Line : constant String := Ada.Text_IO.Get_Line (File);
            Col : Columns;
            Start : Positive := 1;
            Index : Positive := 1;
         begin
            for I in Line'Range loop
               if Line (I) = Character'Val (9) then
                  Col (Index) := To_Unbounded_String (Line (Start .. I - 1)); Start := I + 1; Index := Index + 1;
               end if;
            end loop;
            Col (Index) := To_Unbounded_String (Line (Start .. Line'Last));
            declare
               D : constant Compatibility_Diagnostic := Compare_Catalogues (
                  Catalogue ("tests/fixtures/compatibility/" & To_String (Col (2))),
                  Catalogue ("tests/fixtures/compatibility/" & To_String (Col (3))),
                  Plain (Col (4)), To_String (Col (5)) = "1");
            begin
               Check (D.Code = Compatibility_Code'Value (To_String (Col (6))), "compatibility code: " & To_String (Col (1)));
               Check (To_String (D.Message) = Plain (Col (7)) and then To_String (D.Path) = Plain (Col (8)), "diagnostic path");
               Check (D.Field_Id = Unsigned_32'Value (To_String (Col (9))), "diagnostic field ID");
               Check (To_String (D.Publisher_Type) = Plain (Col (10)) and then To_String (D.Consumer_Type) = Plain (Col (11)), "diagnostic types");
               Check (D.Enum_Value = Int32'Value (To_String (Col (12))), "diagnostic enum value");
            end;
         end;
      end loop;
      Ada.Text_IO.Close (File);
   end Compatibility_Cases;
   C : constant Context := SDL_Wire_Example.Prepare;
   P : T_Packet;
   procedure Reject (Data : Bytes) is
   begin
      declare R : constant T_Packet := Decode (C, Data); begin
         raise Program_Error with "malformed Packet accepted";
      end;
   exception when Codec_Error => Passed := Passed + 1;
   end;
   generic
      type Message_Type is private;
      with function Emit (C : Context; M : Message_Type) return Bytes;
      with function Parse (C : Context; B : Bytes) return Message_Type;
   procedure Oracle (Catalogue : String; M : Message_Type);
   procedure Oracle (Catalogue : String; M : Message_Type) is
      Fast : constant Context := SDL_Runtime.Prepare (Catalogue, Catalogue);
      Slow : constant Context := SDL_Runtime.Prepare (Catalogue, Catalogue, Use_Direct => False);
      Wire : constant Bytes := Emit (Slow, M);
      function Accepts (C : Context; Data : Bytes) return Boolean is
      begin
         declare R : constant Message_Type := Parse (C, Data); begin return True; end;
      exception when Codec_Error => return False;
      end Accepts;
   begin
      Check (Emit (Fast, M) = Wire, "direct encode matches scalar wire oracle");
      Check (Emit (Slow, Parse (Fast, Wire)) = Wire, "direct decode matches scalar wire oracle");
      Check (Emit (Fast, Parse (Slow, Wire)) = Wire, "generic decode matches direct encoder");
      for I in Wire'Range loop
         for B of Bytes'(0, 1, 2, 127, 128, 255) loop
            declare Bad : Bytes := Wire; begin
               Bad (I) := B;
               Check (Accepts (Fast, Bad) = Accepts (Slow, Bad), "direct/generic validity agrees for mutated bytes");
            end;
         end loop;
      end loop;
      for I in 0 .. Wire'Length - 1 loop
         begin declare R : constant Message_Type := Parse (Fast, Wire (1 .. I)); begin
            raise Program_Error with "direct truncated message accepted";
         end; exception when Codec_Error => Passed := Passed + 1; end;
      end loop;
      begin declare R : constant Message_Type := Parse (Fast, Wire & Unsigned_8'(0)); begin
         raise Program_Error with "direct trailing bytes accepted";
      end; exception when Codec_Error => Passed := Passed + 1; end;
   end Oracle;
   procedure Packet_Oracle is new Oracle (T_Packet, SDL_Wire_Example.Encode, SDL_Wire_Example.Decode);
   procedure Cases_Oracle is new Oracle (T_CodecCases, SDL_Codec_Cases.Encode, SDL_Codec_Cases.Decode);
   procedure Board_Oracle is new Oracle (T_FixedBoard, SDL_Schema.Encode, SDL_Schema.Decode);
   procedure Packed_Oracle is new Oracle (T_PackedBatch, SDL_Packed_Validation.Encode, SDL_Packed_Validation.Decode);
   procedure Primitive_Oracle is new Oracle (T_PrimitiveBatch, SDL_Packed_Validation.Encode, SDL_Packed_Validation.Decode);
   procedure Payload_Oracle is new Oracle (SDL_Benchmark.T_BenchPayload, SDL_Benchmark.Encode, SDL_Benchmark.Decode);
   procedure Optional_Oracle is new Oracle (SDL_Bench_Cases.T_BenchOptionals, SDL_Bench_Cases.Encode, SDL_Bench_Cases.Decode);
   procedure Small_Oracle is new Oracle (SDL_Bench_Cases.T_BenchSmall, SDL_Bench_Cases.Encode, SDL_Bench_Cases.Decode);
   procedure Auto_Record_Oracle is new Oracle (T_AutoRecord, SDL_Auto_Fixed.Encode, SDL_Auto_Fixed.Decode);
   procedure Auto_Batch_Oracle is new Oracle (T_AutoBatch, SDL_Auto_Fixed.Encode, SDL_Auto_Fixed.Decode);
   procedure Auto_Optional_Oracle is new Oracle (T_AutoOptional, SDL_Auto_Fixed.Encode, SDL_Auto_Fixed.Decode);
   function Float_From_Bits is new Ada.Unchecked_Conversion (Unsigned_32, Float32);
   function Packet_Catalogue (Fields : String) return String is
   begin return "SDL2|Packet{" & Fields & "}"; end;
begin
   declare
      C : constant Context := SDL_Auto_Fixed.Prepare;
      R : constant T_AutoRecord := (16#01020304#, ((1.25, -2.5), -123, -7, True), ((-1,2,-3),(4,-5,6)));
      M : T_AutoBatch;
      Wire : constant Bytes := Load ("tests/fixtures/auto_fixed.bin");
      Root : Bytes (1 .. 32);
   begin
      Check (Direct_Layout (C, "AutoRecord"), "fixed root selected without Packed");
      Check (Direct_Layout (C, "AutoBatch"), "repeated fixed records selected without Packed");
      Check (Direct_Layout (C, "AutoOptional"), "optional fixed descendant selects parent");
      M.F_label := To_Unbounded_String ("x"); M.F_records.Append (R); M.F_records.Append (R);
      M.F_samples.Append (-1); M.F_samples.Append (2); M.F_samples.Append (-3);
      Check (SDL_Auto_Fixed.Encode (C, M) = Wire, "automatic fixed shared wire oracle");
      Root (1) := 4; Root (2 .. 32) := Wire (5 .. 35);
      Check (SDL_Auto_Fixed.Encode (C, R) = Root, "automatic fixed root wire oracle");
      Auto_Record_Oracle (SDL_Auto_Fixed.Description, R);
      Auto_Batch_Oracle (SDL_Auto_Fixed.Description, M);
      Auto_Optional_Oracle (SDL_Auto_Fixed.Description, T_AutoOptional'(True, R, 9));
      Auto_Optional_Oracle (SDL_Auto_Fixed.Description, T_AutoOptional'(False, R, 9));
   end;
   Small_Oracle (SDL_Bench_Cases.Description, SDL_Bench_Cases.T_BenchSmall'(True, 123, -2));
   for Mask in Unsigned_16 range 0 .. 255 loop
      declare
         M : constant SDL_Bench_Cases.T_BenchOptionals :=
           ((Mask and 1) /= 0, 1, (Mask and 2) /= 0, 2,
            (Mask and 4) /= 0, 3, (Mask and 8) /= 0, 4,
            (Mask and 16) /= 0, 5, (Mask and 32) /= 0, 6,
            (Mask and 64) /= 0, 7, (Mask and 128) /= 0, 8);
      begin
         Optional_Oracle (SDL_Bench_Cases.Description, M);
      end;
   end loop;
   declare
      File : Ada.Text_IO.File_Type;
   begin
      Ada.Text_IO.Open (File, Ada.Text_IO.In_File, "tests/fixtures/catalogue_ids.tsv");
      while not Ada.Text_IO.End_Of_File (File) loop
         declare
            Line : constant String := Ada.Text_IO.Get_Line (File);
            Tab : constant Natural := Ada.Strings.Fixed.Index (Line, String'(1 => Character'Val (9)));
            Text : String (1 .. (Tab - 1) / 2);
            Expected : constant Unsigned_32 := Unsigned_32'Value ("16#" & Line (Tab + 1 .. Line'Last) & "#");
         begin
            for I in Text'Range loop
               Text (I) := Character'Val (Integer'Value ("16#" & Line (2 * I - 1 .. 2 * I) & "#"));
            end loop;
            Check (Catalogue_Id (Text) = Expected, "shared catalogue identity fixture");
         end;
      end loop;
      Ada.Text_IO.Close (File);
   end;
   Check (Message_Count (C) = 1 and then Message_Name (C, 1) = "Packet", "public message inspection");
   declare
      Ids : constant array (1 .. 3) of Unsigned_32 := (0, 16#89ABCDEF#, 16#FFFFFFFF#);
   begin
      for Id of Ids loop
         declare
            Wire : constant Bytes := Pack_Envelope (Id, (1, 0));
            E : constant Envelope := Unpack_Envelope (Wire);
         begin
            Check (Wire (1 .. 5) = Bytes'(83, 50, 80, 83, 2), "envelope magic and version");
            Check (Wire (6) = Unsigned_8 (Shift_Right (Id, 24)) and then
                   Wire (7) = Unsigned_8 (Shift_Right (Id, 16) and 255) and then
                   Wire (8) = Unsigned_8 (Shift_Right (Id, 8) and 255) and then
                   Wire (9) = Unsigned_8 (Id and 255), "envelope big endian ID");
            Check (Wire'Length = 11 and then E.Catalogue = Id and then E.Payload = Bytes'(1, 0), "envelope roundtrip");
            for N in 0 .. 9 loop
               begin
                  declare Bad : constant Envelope := Unpack_Envelope (Wire (1 .. N));
                  begin Check (False, "envelope truncation accepted"); end;
               exception when Codec_Error => Check (True, "envelope truncation"); end;
            end loop;
            for I in 1 .. 5 loop
               declare Bad_Wire : Bytes := Wire;
               begin
                  Bad_Wire (I) := Bad_Wire (I) xor 1;
                  begin
                     declare Bad : constant Envelope := Unpack_Envelope (Bad_Wire);
                     begin Check (False, "envelope header accepted"); end;
                  exception when Codec_Error => Check (True, "envelope header"); end;
               end;
            end loop;
         end;
      end loop;
   end;
   Compatibility_Cases;
   P.F_active := True; P.H_code := True; P.F_code := -2;
   P.F_label := To_Unbounded_String (Character'Val (195) & Character'Val (169) & "t" & Character'Val (195) & Character'Val (169));
   P.F_samples.Append (300); P.F_samples.Append (-1);
   declare B : constant Bytes := Encode (C, P); begin
      Check (Direct_Layout (C, "Packet"), "direct Packet selected");
      Packet_Oracle (SDL_Wire_Example.Description, P);
      Check (B = Load ("tests/fixtures/packet.bin"), "shared SDL2 fixture");
      Check (T_Packet'(Decode (C, B)) = P, "Packet round trip");
      for I in 0 .. B'Length - 1 loop Reject (B (1 .. I)); end loop;
      Reject (B & Unsigned_8'(0));
      declare Bad : Bytes := B; begin Bad (2) := 2; Reject (Bad); end;
      declare Bad : Bytes := B; begin Bad (3) := 2; Reject (Bad); end;
      Reject ((128, 0) & B (2 .. B'Last));
      declare Bad : Bytes := B; begin Bad (7) := 255; Reject (Bad); end;
      declare Offset : Bytes (10 .. B'Length + 9) := B; begin
         Check (T_Packet'(Decode (C, Offset)) = P, "non-one input lower bound");
      end;
   end;
   P.H_code := False; P.F_code := 0;
   P.F_label := To_Unbounded_String ("caf" & Character'Val (195) & Character'Val (169) & Character'Val (0));
   P.F_samples.Clear;
   Check (T_Packet'(Decode (C, Encode (C, P))) = P, "UTF8, embedded NUL, absent optional, empty packed");
   P.F_label := To_Unbounded_String ((1 => Character'Val (255)));
   begin
      declare B : constant Bytes := Encode (C, P); begin raise Program_Error with "invalid UTF8 accepted"; end;
   exception when Codec_Error => Passed := Passed + 1; end;
   declare
      Copy : Context;
   begin
      declare Temporary : constant Context := SDL_Wire_Example.Prepare; begin Copy := Temporary; end;
      P.F_label := Null_Unbounded_String;
      Check (T_Packet'(Decode (Copy, Encode (Copy, P))) = P, "context survives source finalization");
   end;
   declare
      CC : constant Context := SDL_Codec_Cases.Prepare;
      M : T_CodecCases;
   begin
      M.H_tiny := True; M.F_tiny := Int8'First;
      M.H_small := True; M.F_small := Int16'Last;
      M.H_signed_value := True; M.F_signed_value := Int32'First;
      M.H_wide := True; M.F_wide := Int64'First;
      M.H_ratio := True; M.F_ratio := -0.25;
      M.H_precise := True; M.F_precise := 1.125;
      M.H_point := True; M.F_point := (-2.5, 3.25);
      M.H_position := True; M.F_position := (1.25, -7.5);
      M.H_state := True; M.F_state := E_State_MINIMUM;
      M.H_empty_text := True;
      M.F_samples.Append (Int16'First); M.F_samples.Append (Int16'Last);
      M.F_measurements.Append (1.5); M.F_labels.Append (To_Unbounded_String ("ok"));
      M.F_points.Append (Complex32'(1.0, -1.0));
      M.F_required_enabled := True; M.H_optional_enabled := True;
      M.F_bool_flags.Append (True); M.F_bool_flags.Append (False);
      M.F_fixed_states := (E_State_MINIMUM, E_State_MAXIMUM, E_State_NEGATIVE);
      M.F_packed_states.Append (E_State_NEGATIVE); M.F_packed_flags.Append (True);
      M.H_high_id_value := True; M.F_high_id_value := Int32'Last;
      Check (T_CodecCases'(Decode (CC, Encode (CC, M))) = M, "all primitives and field ID 4294967295");
      Cases_Oracle (SDL_Codec_Cases.Description, M);
      M.F_state := 2;
      begin declare B : constant Bytes := Encode (CC, M); begin raise Program_Error with "invalid enum accepted"; end;
      exception when Codec_Error => Passed := Passed + 1; end;
      declare
         E : T_EnumRecordBatch;
         B : Buffer;
      begin
         E.F_records.Append (T_EnumRecord'(E_State_NEGATIVE, 123));
         Check (T_EnumRecordBatch'(Decode (CC, Encode (CC, E))) = E, "packed enums in records");
         Put_Count (B, Message_Id (CC, "EnumRecordBatch")); Put_Count (B, 1);
         Write (B, Int32'(2)); Write (B, Int32'(123));
         begin declare R : constant T_EnumRecordBatch := Decode (CC, To_Bytes (B)); begin raise Program_Error with "wire enum accepted"; end;
         exception when Codec_Error => Passed := Passed + 1; end;
      end;
   end;
   declare
      SC : constant Context := SDL_Schema.Prepare;
      Board : T_FixedBoard;
      A : T_AnonymousEnvelope;
      Root : T_RootPayload;
   begin
      Board.F_rows (2).F_vectors (1).F_grid (2)(3) := -123;
      Board.F_rows (1).F_vectors (2).F_coords := (1.5, -2.0);
      Board.F_packed_rows.Append (Board.F_rows (2));
      Check (T_FixedBoard'(Decode (SC, Encode (SC, Board))) = Board, "nested multidimensional arrays");
      Board_Oracle (SDL_Schema.Description, Board);
      A.F_metadata.F_code := 123; A.F_metadata.H_detail := True;
      A.F_metadata.F_detail.F_text := To_Unbounded_String ("anonymous");
      A.F_points.Append (T_AnonymousEnvelope_Anon_3'(1.25, 2.5));
      Check (T_AnonymousEnvelope'(Decode (SC, Encode (SC, A))) = A, "anonymous structs");
      Root.F_fixed_array.Append (T_FixedItem'(1.25, -2.5));
      Root.F_var_array.Append (T_VarItem'(True, To_Unbounded_String ("name"), 42));
      Check (T_RootPayload'(Decode (SC, Encode (SC, Root))) = Root, "variable nested records");
   end;
   declare
      PC : constant Context := SDL_Packed_Validation.Prepare;
      M : T_PackedBatch;
      E : T_PackedRecord;
      Primitive : T_PrimitiveBatch;
   begin
      E.F_leaf.F_enabled := True; E.F_leaf.F_tiny := Int8'First;
      E.F_leaf.F_small := Int16'Last; E.F_leaf.F_signed_value := Int32'First;
      E.F_leaf.F_wide := Int64'Last; E.F_leaf.F_ratio := 1.5;
      E.F_leaf.F_precise := -2.25; E.F_leaf.F_point := (3.5, -4.5);
      E.F_leaf.F_position := (2.5, -7.5); E.F_leaf.F_state := E_PackedState_NEGATIVE;
      E.F_grid (2)(3) := -1.25; M.F_records.Append (E);
      Check (T_PackedBatch'(Decode (PC, Encode (PC, M))) = M, "complex Packed nested records");
      declare B : constant Bytes := Encode (PC, M); begin
         for I in 0 .. B'Length - 1 loop
            begin declare R : constant T_PackedBatch := Decode (PC, B (1 .. I)); begin
               raise Program_Error with "truncated Packed record accepted";
            end; exception when Codec_Error => Passed := Passed + 1; end;
         end loop;
         declare Bad : Bytes := B; begin
            Bad (3) := 2;
            begin declare R : constant T_PackedBatch := Decode (PC, Bad); begin
               raise Program_Error with "Packed bool accepted";
            end; exception when Codec_Error => Passed := Passed + 1; end;
         end;
      end;
      Packed_Oracle (SDL_Packed_Validation.Description, M);
      Primitive.F_flags.Append (True); Primitive.F_tiny.Append (Int8'Last);
      Primitive.F_small.Append (Int16'First); Primitive.F_signed_values.Append (Int32'Last);
      Primitive.F_wide.Append (Int64'First); Primitive.F_ratios.Append (-1.5);
      Primitive.F_precise.Append (2.25); Primitive.F_positions.Append (Complex64'(3.5, -4.5));
      Check (T_PrimitiveBatch'(Decode (PC, Encode (PC, Primitive))) = Primitive, "Packed primitive vectors");
      Primitive_Oracle (SDL_Packed_Validation.Description, Primitive);
   end;
   declare
      EC : constant Context := SDL_Empty_Message.Prepare;
      E : T_EmptyMessage;
   begin Check (T_EmptyMessage'(Decode (EC, Encode (EC, E))) = E, "empty message"); end;
   declare
      RC : constant Context := SDL_Wire_Example.Prepare (Packet_Catalogue (
         "1:bool:renamed;3:string:title;4*:int16:values;5:int32:extra;"));
      B : Buffer;
   begin
      Put_Count (B, Message_Id (RC, "Packet")); Write (B, True);
      Write (B, To_Unbounded_String ("remote")); Put_Count (B, 1); Write (B, Int16'(-2)); Write (B, Int32'(77));
      declare R : constant T_Packet := Decode (RC, To_Bytes (B)); begin
         Check (R.F_active and not R.H_code and R.F_label = To_Unbounded_String ("remote") and R.F_samples (1) = -2,
                "evolution by ID, renamed, removed, unknown, repeated/Packed");
      end;
      begin declare Data : constant Bytes := Encode (RC, P); begin raise Program_Error with "incompatible encode accepted"; end;
      exception when Codec_Error => Passed := Passed + 1; end;
   end;
   begin
      declare Bad : constant Context := SDL_Wire_Example.Prepare (Packet_Catalogue ("1:int32:active;"));
      begin raise Program_Error with "incompatible type accepted"; end;
   exception when Codec_Error => Passed := Passed + 1; end;
   declare
      RC : constant Context := SDL_Codec_Cases.Prepare (
         "SDL2|#State{FUTURE=2;}|EnumRecord{1:State:state;2:int32:code;}");
      B : Buffer;
   begin
      Put_Count (B, Message_Id (RC, "EnumRecord")); Write (B, Int32'(2)); Write (B, Int32'(42));
      begin declare R : constant T_EnumRecord := Decode (RC, To_Bytes (B)); begin
         raise Program_Error with "unknown local enum value accepted";
      end; exception when Codec_Error => Passed := Passed + 1; end;
      begin declare Data : constant Bytes := Encode (RC, T_EnumRecord'(E_State_READY, 42)); begin
         raise Program_Error with "unknown remote enum value accepted";
      end; exception when Codec_Error => Passed := Passed + 1; end;
   end;
   declare
      RC : constant Context := SDL_Wire_Example.Prepare (Packet_Catalogue (
         "1:bool:renamed;2?:int16:alias;3:string:title;4*:int16:values;"));
   begin
      Check (Direct_Layout (RC, "Packet"), "field labels and Packed/repeated interchange retain direct layout");
      Check (T_Packet'(Decode (RC, Encode (RC, P))) = P, "direct renamed fields round trip");
   end;
   declare
      RC : constant Context := SDL_Schema.Prepare (
         "SDL2|FixedRow{1:FixedVector[2]:vectors;}|FixedRowBatch{1+:FixedRow:rows;}|FixedVector{1:fl32[2]:renamed;2:int16[2][3]:grid;3:int32:extra;}");
      B : Buffer;
   begin
      Check (not Direct_Layout (RC, "FixedRowBatch"), "nested layout change disables direct parent");
      Put_Count (B, Message_Id (RC, "FixedRowBatch")); Put_Count (B, 1);
      for I in 1 .. 2 loop
         Write (B, Float32'(1.25)); Write (B, Float32'(-2.5));
         for J in 1 .. 6 loop Write (B, Int16'(-7)); end loop;
         Write (B, Int32'(123));
      end loop;
      declare R : constant T_FixedRowBatch := Decode (RC, To_Bytes (B)); begin
         Check (Natural (R.F_rows.Length) = 1 and then R.F_rows.Element (1).F_vectors (2).F_coords = A_FixedVector_coords_0'(1.25, -2.5),
            "generic fallback reads nested evolved Packed records");
      end;
   end;
   declare
      RC : constant Context := SDL_Codec_Cases.Prepare (
         "SDL2|#State{FUTURE=2;}|EnumRecord{1:State:state;2:int32:code;}|EnumRecordBatch{1+:EnumRecord:records;}");
      B : Buffer;
   begin
      Check (not Direct_Layout (RC, "EnumRecordBatch"), "nested enum set change disables direct parent");
      Put_Count (B, Message_Id (RC, "EnumRecordBatch")); Put_Count (B, 1);
      Write (B, Int32'(2)); Write (B, Int32'(42));
      begin declare R : constant T_EnumRecordBatch := Decode (RC, To_Bytes (B)); begin
         raise Program_Error with "future nested enum accepted";
      end; exception when Codec_Error => Passed := Passed + 1; end;
   end;
   begin
      declare Bad : constant Context := SDL_Codec_Cases.Prepare (
         "SDL2|State{}");
      begin raise Program_Error with "enum changed to message accepted"; end;
   exception when Codec_Error => Passed := Passed + 1; end;
   begin
      declare Bad : constant Context := SDL_Runtime.Prepare ("garbage", SDL_Wire_Example.Description);
      begin raise Program_Error with "invalid catalogue accepted"; end;
   exception when Codec_Error => Passed := Passed + 1; end;
   declare
      M : SDL_Benchmark.T_BenchPayload;
      C : constant Context := SDL_Benchmark.Prepare;
   begin
      M.F_header := To_Unbounded_String ("IEEE" & Character'Val (0));
      M.F_samples.Append (Complex32'(Float_From_Bits (16#7FC12345#), Float_From_Bits (16#80000000#)));
      M.F_samples.Append (Complex32'(Float_From_Bits (16#7F800000#), Float_From_Bits (16#FF800000#)));
      Payload_Oracle (SDL_Benchmark.Description, M);
      Check (SDL_Benchmark.Encode (C, SDL_Benchmark.T_BenchPayload'(SDL_Benchmark.Decode (C, SDL_Benchmark.Encode (C, M)))) = SDL_Benchmark.Encode (C, M), "IEEE bits preserved by direct decode");
      M.F_samples.Clear;
      Payload_Oracle (SDL_Benchmark.Description, M);
   end;
   Ada.Text_IO.Put_Line ("Ada: " & Natural'Image (Passed) & " checks passed");
end Run_Tests;
