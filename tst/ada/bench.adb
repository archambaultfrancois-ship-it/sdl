with Ada.Text_IO; use Ada.Text_IO;
with Ada.Real_Time; use Ada.Real_Time;
with Ada.Environment_Variables;
with Ada.Strings.Fixed;
with Ada.Strings.Unbounded; use Ada.Strings.Unbounded;
with Interfaces; use Interfaces;
with SDL_Runtime; use SDL_Runtime;
with SDL_Bench_Cases; use SDL_Bench_Cases;
with SDL_Benchmark; use SDL_Benchmark;
procedure Bench is
   package Numbers is new Ada.Text_IO.Float_IO (Long_Float);
   function Image (N : Natural) return String is
   begin return Ada.Strings.Fixed.Trim (Natural'Image (N), Ada.Strings.Both); end;
   procedure Number (N : Long_Float; Decimals : Natural) is
   begin Numbers.Put (N, Fore => 1, Aft => Decimals, Exp => 0); end;
   Iterations : Positive := 200;
   Sink : Unsigned_64 := 0;
   generic
      type Message_Type is private;
      with function Emit (C : Context; M : Message_Type) return Bytes;
      with function Parse (C : Context; B : Bytes) return Message_Type;
   procedure Measure (Label_Text, Catalogue : String; M : Message_Type; Useful : Positive);
   procedure Measure (Label_Text, Catalogue : String; M : Message_Type; Useful : Positive) is
      Start : Time := Clock;
      C : constant Context := SDL_Runtime.Prepare (Catalogue, Catalogue,
         Use_Direct => not (Ada.Environment_Variables.Exists ("SDL_ADA_NO_DIRECT") and then
            Ada.Environment_Variables.Value ("SDL_ADA_NO_DIRECT") = "1"));
      Prepare_Time : constant Duration := To_Duration (Clock - Start);
      Wire : constant Bytes := Emit (C, M);
      Rates : array (1 .. 3) of Long_Float;
      N : Natural;
      Seconds : Duration;
      Median : Long_Float;
   begin
      if Parse (C, Wire) /= M then raise Program_Error with "benchmark round trip failed"; end if;
      for I in 1 .. 100 loop
         declare
            B : constant Bytes := Emit (C, M);
            R : constant Message_Type := Parse (C, Wire);
            pragma Unreferenced (R);
         begin Sink := Sink + Unsigned_64 (B'Length); end;
      end loop;
      Put (Label_Text & " metadata: " & Image (Catalogue'Length) & " description bytes, ");
      Number (Long_Float (Prepare_Time) * 1.0E6, 1); Put_Line (" us prepare");
      for Operation in 1 .. 2 loop
         for Trial in Rates'Range loop
            Start := Clock; N := 0;
            loop
               if Operation = 1 then
                  declare B : constant Bytes := Emit (C, M); begin Sink := Sink + Unsigned_64 (B'Length); end;
               else
                  declare R : constant Message_Type := Parse (C, Wire);
                     pragma Unreferenced (R);
                  begin Sink := Sink + Unsigned_64 (Wire'Length); end;
               end if;
               N := N + 1; Seconds := To_Duration (Clock - Start);
               exit when N >= Iterations and Seconds >= 0.1;
            end loop;
            Rates (Trial) := Long_Float (N) / Long_Float (Seconds);
         end loop;
         Median := Long_Float'Max (Long_Float'Min (Rates (1), Rates (2)),
            Long_Float'Min (Long_Float'Max (Rates (1), Rates (2)), Rates (3)));
         Put (Label_Text & (if Operation = 1 then " encode " else " decode "));
         Number (Median, 1); Put (" msg/s "); Number (Median * Long_Float (Useful) / 1048576.0, 4);
         Put_Line (" MiB/s (" & Image (Wire'Length) & " bytes/message)");
      end loop;
   end;
   procedure Small_Run is new Measure (T_BenchSmall, SDL_Bench_Cases.Encode, SDL_Bench_Cases.Decode);
   procedure Optional_Run is new Measure (T_BenchOptionals, SDL_Bench_Cases.Encode, SDL_Bench_Cases.Decode);
   procedure Variable_Run is new Measure (T_BenchVariable, SDL_Bench_Cases.Encode, SDL_Bench_Cases.Decode);
   procedure Packed_Run is new Measure (T_BenchPayload, SDL_Benchmark.Encode, SDL_Benchmark.Decode);
   procedure Struct_Run is new Measure (T_BenchRecordBatch, SDL_Bench_Cases.Encode, SDL_Bench_Cases.Decode);
   Small : T_BenchSmall := (True, 123, -2);
   Sparse, Dense : T_BenchOptionals;
   Variable : T_BenchVariable;
   Packed : T_BenchPayload;
   Batch : T_BenchRecordBatch;
begin
   if Ada.Environment_Variables.Exists ("SDL_BENCH_ITERATIONS") then
      begin Iterations := Positive'Value (Ada.Environment_Variables.Value ("SDL_BENCH_ITERATIONS"));
      exception when Constraint_Error => null; end;
   end if;
   Sparse.H_a := True; Sparse.F_a := 1;
   Dense := (True, 1, True, 2, True, 3, True, 4, True, 5, True, 6, True, 7, True, 8);
   Variable.F_header := To_Unbounded_String ((1 .. 40 => 'V'));
   for I in 0 .. 7 loop
      Variable.F_entries.Append (T_BenchEntry'(True, To_Unbounded_String ("entry" & Image (I)), Int64 (I)));
   end loop;
   Packed.F_header := To_Unbounded_String ((1 .. 200 => 'H'));
   for I in 0 .. 4999 loop
      Packed.F_samples.Append (Complex32'(Float32 (I) * 0.25, -Float32 (I mod 97) * 0.5));
   end loop;
   for I in 0 .. 999 loop
      declare R : T_BenchRecord; begin
         R.F_id := Int32 (I);
         R.F_pose.F_position.F_values := (Float32 (I) * 0.25, -Float32 (I) * 0.5, Float32 (I mod 97));
         R.F_pose.F_rotation := (0.0, 0.0, 0.0, 1.0);
         R.F_measures := (Float32 (I) * 0.125, -Float32 (I mod 31) * 0.5);
         Batch.F_records.Append (R);
      end;
   end loop;
   Small_Run ("small", SDL_Bench_Cases.Description, Small, 7);
   Optional_Run ("optional_sparse", SDL_Bench_Cases.Description, Sparse, 4);
   Optional_Run ("optional_dense", SDL_Bench_Cases.Description, Dense, 32);
   Variable_Run ("variable", SDL_Bench_Cases.Description, Variable, 152);
   Packed_Run ("packed", SDL_Benchmark.Description, Packed, 40200);
   Struct_Run ("packed_struct", SDL_Bench_Cases.Description, Batch, 40000);
   if Sink = 0 then raise Program_Error with "benchmark did no work"; end if;
end Bench;
