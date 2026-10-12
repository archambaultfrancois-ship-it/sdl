with Ada.Text_IO; use Ada.Text_IO;
with Ada.Real_Time; use Ada.Real_Time;
with Ada.Strings.Unbounded; use Ada.Strings.Unbounded;
with Interfaces; use Interfaces;
with SDL_Runtime; use SDL_Runtime;
with SDL_Auto_Fixed; use SDL_Auto_Fixed;
procedure Bench_Fixed is
   package Numbers is new Ada.Text_IO.Float_IO (Long_Float);
   Sink : Unsigned_64 := 0;
   generic
      type Message_Type is private;
      with function Emit (C : Context; M : Message_Type) return Bytes;
      with function Parse (C : Context; B : Bytes) return Message_Type;
   procedure Measure (Label_Text : String; M : Message_Type);
   procedure Measure (Label_Text : String; M : Message_Type) is
      Baseline : constant Context := Prepare (Use_Direct => False);
      Fast : constant Context := Prepare;
      Wire : constant Bytes := Emit (Baseline, M);
      Rates : array (1 .. 2) of Long_Float;
      Samples : array (1 .. 3) of Long_Float;
      Start : Time;
      N : Natural;
      Seconds : Duration;
      procedure Run (C : Context; Operation : Positive) is
      begin
         if Operation = 1 then
            declare B : constant Bytes := Emit (C, M); begin Sink := Sink + Unsigned_64 (B'Length); end;
         else
            declare R : constant Message_Type := Parse (C, Wire); pragma Unreferenced (R);
            begin Sink := Sink + Unsigned_64 (Wire'Length); end;
         end if;
      end;
   begin
      if Emit (Fast, M) /= Wire or else Parse (Fast, Wire) /= M then
         raise Program_Error with "fixed codec comparison failed";
      end if;
      Put_Line (Label_Text);
      for Operation in 1 .. 2 loop
         for Mode in 1 .. 2 loop
            for Trial in Samples'Range loop
               Start := Clock; N := 0;
               loop
                  for Item in 1 .. 256 loop
                     if Mode = 1 then Run (Baseline, Operation); else Run (Fast, Operation); end if;
                  end loop;
                  N := N + 256; Seconds := To_Duration (Clock - Start);
                  exit when Seconds >= 0.2;
               end loop;
               Samples (Trial) := Long_Float (N) / Long_Float (Seconds);
            end loop;
            Rates (Mode) := Long_Float'Max (Long_Float'Min (Samples (1), Samples (2)),
               Long_Float'Min (Long_Float'Max (Samples (1), Samples (2)), Samples (3)));
         end loop;
         Put ("  " & (if Operation = 1 then "encode: " else "decode: "));
         Numbers.Put (Rates (1), Fore => 1, Aft => 0, Exp => 0); Put (" -> ");
         Numbers.Put (Rates (2), Fore => 1, Aft => 0, Exp => 0); Put (" msg/s (");
         Numbers.Put (Rates (2) / Rates (1), Fore => 1, Aft => 2, Exp => 0); Put_Line ("x)");
      end loop;
   end;
   procedure Record_Run is new Measure (T_AutoRecord, Encode, Decode);
   procedure Batch_Run is new Measure (T_AutoBatch, Encode, Decode);
   R : constant T_AutoRecord := (7, ((1.0, 2.0), -3, 0, True), ((-1,2,-3),(4,-5,6)));
   M : T_AutoBatch;
begin
   Record_Run ("AutoRecord", R);
   M.F_label := To_Unbounded_String ("samples");
   for I in 1 .. 32 loop M.F_records.Append (R); M.F_samples.Append (1); end loop;
   Batch_Run ("AutoBatch", M);
   if Sink = 0 then raise Program_Error; end if;
end Bench_Fixed;
