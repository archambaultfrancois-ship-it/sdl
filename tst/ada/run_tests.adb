with Ada.Text_IO;
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
   function Float_From_Bits is new Ada.Unchecked_Conversion (Unsigned_32, Float32);
   LF : constant String := (1 => Character'Val (10));
   function Packet_Catalogue (Fields : String) return String is
   begin return "SDL2" & LF & "message Packet {" & LF & Fields & "}" & LF; end;
begin
   P.F_active := True; P.H_code := True; P.F_code := -2;
   P.F_label := To_Unbounded_String (Character'Val (195) & Character'Val (169) & "t" & Character'Val (195) & Character'Val (169));
   P.F_samples.Append (300); P.F_samples.Append (-1);
   declare B : constant Bytes := Encode (C, P); begin
      Check (Direct_Layout (C, "Packet"), "direct Packet selected");
      Packet_Oracle (SDL_Wire_Example.Description, P);
      Check (B = Load ("tst/fixtures/packet.bin"), "shared SDL2 fixture");
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
         "  1: required bool renamed;" & LF & "  3: required string title;" & LF &
         "  4: repeated int16 values;" & LF & "  5: required int32 extra;" & LF));
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
      declare Bad : constant Context := SDL_Wire_Example.Prepare (Packet_Catalogue ("  1: required int32 active;" & LF));
      begin raise Program_Error with "incompatible type accepted"; end;
   exception when Codec_Error => Passed := Passed + 1; end;
   declare
      RC : constant Context := SDL_Codec_Cases.Prepare (
         "SDL2" & LF & "enum State {" & LF & " FUTURE = 2;" & LF & "}" & LF &
         "message EnumRecord {" & LF & " 1: required State state;" & LF &
         " 2: required int32 code;" & LF & "}" & LF);
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
         " 1: required bool renamed;" & LF & " 2: optional int16 alias;" & LF &
         " 3: required string title;" & LF & " 4: repeated int16 values;" & LF));
   begin
      Check (Direct_Layout (RC, "Packet"), "field labels and Packed/repeated interchange retain direct layout");
      Check (T_Packet'(Decode (RC, Encode (RC, P))) = P, "direct renamed fields round trip");
   end;
   declare
      RC : constant Context := SDL_Schema.Prepare (
         "SDL2" & LF & "message FixedRow {" & LF & " 1: required FixedVector[2] vectors;" & LF & "}" & LF &
         "message FixedRowBatch {" & LF & " 1: packed FixedRow rows;" & LF & "}" & LF &
         "message FixedVector {" & LF & " 1: required fl32[2] renamed;" & LF &
         " 2: required int16[2][3] grid;" & LF & " 3: required int32 extra;" & LF & "}" & LF);
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
         "SDL2" & LF & "enum State {" & LF & " FUTURE = 2;" & LF & "}" & LF &
         "message EnumRecord {" & LF & " 1: required State state;" & LF & " 2: required int32 code;" & LF & "}" & LF &
         "message EnumRecordBatch {" & LF & " 1: packed EnumRecord records;" & LF & "}" & LF);
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
         "SDL2" & LF & "message State {" & LF & "}" & LF);
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
