with SDL.telemetry.model; use SDL.telemetry.model;
with SDL_Runtime; use SDL_Runtime;
with SDL_Filter;
with Ada.Real_Time; use Ada.Real_Time;
with Ada.Text_IO;
procedure Filter_Bench is
   package Vector_Filters is new SDL_Filter.Typed (T_telemetry_Anon_Vector, "telemetry.Vector", Filter_Read);
   package Sample_Filters is new SDL_Filter.Typed (T_telemetry_Anon_Sample, "telemetry.Sample", Filter_Read);
   V : aliased T_telemetry_Anon_Vector := (3.0, 4.0);
   Sample : aliased T_telemetry_Anon_Sample;
   Sink : Integer := 0;
   pragma Volatile (Sink);
   function Measure (Action : not null access procedure; Iterations : Positive) return Long_Float is
      Trials : array (1 .. 5) of Long_Float;
      Start : Time;
      Temp : Long_Float;
   begin
      for I in 1 .. 100 loop Action.all; end loop;
      for Trial in Trials'Range loop
         Start := Clock;
         for I in 1 .. Iterations loop Action.all; end loop;
         Trials (Trial) := Long_Float (To_Duration (Clock - Start)) * 1.0E9 / Long_Float (Iterations);
      end loop;
      for I in Trials'Range loop
         for J in I + 1 .. Trials'Last loop
            if Trials (J) < Trials (I) then Temp := Trials (I); Trials (I) := Trials (J); Trials (J) := Temp; end if;
         end loop;
      end loop;
      return Trials (3);
   end Measure;
   procedure Bench (Label, Catalogue, Root, Expression : String; Is_Sample : Boolean; Iterations : Positive) is
      C : Context := Prepare (Catalogue);
      Wire : constant Bytes := (if Is_Sample then Encode (C, Sample) else Encode (C, V));
      P : SDL_Filter.Predicate := SDL_Filter.Prepare (Catalogue, Root, Expression);
      procedure Enc is
         B : constant Bytes := (if Is_Sample then Encode (C, Sample) else Encode (C, V));
      begin Sink := B'Length; end Enc;
      procedure Dec is
      begin
         if Is_Sample then declare M : constant T_telemetry_Anon_Sample := Decode (C, Wire); begin Sink := Integer (M.F_points.Length); end;
         else declare M : constant T_telemetry_Anon_Vector := Decode (C, Wire); begin Sink := Integer (M.F_x); end;
         end if;
      end Dec;
      procedure Obj is
         R : constant SDL_Filter.Result := (if Is_Sample then Sample_Filters.Evaluate (P, Sample) else Vector_Filters.Evaluate (P, V));
      begin Sink := SDL_Filter.Status'Pos (R.State); end Obj;
      procedure Buf is
         R : constant SDL_Filter.Result := SDL_Filter.Evaluate_Wire (P, Wire);
      begin Sink := SDL_Filter.Status'Pos (R.State); end Buf;
      E, D, O, W : Long_Float;
      use type SDL_Filter.Status;
   begin
      pragma Assert (SDL_Filter.Evaluate_Wire (P, Wire).State = SDL_Filter.Match);
      pragma Assert ((if Is_Sample then Sample_Filters.Evaluate (P, Sample).State else Vector_Filters.Evaluate (P, V).State) = SDL_Filter.Match);
      E := Measure (Enc'Access, Iterations); D := Measure (Dec'Access, Iterations);
      O := Measure (Obj'Access, Iterations); W := Measure (Buf'Access, Iterations);
      Ada.Text_IO.Put_Line ("BENCH Ada " & Label & Long_Float'Image (E) & Long_Float'Image (D) & Long_Float'Image (O) & Long_Float'Image (W));
   end Bench;
   C : constant String := "SDL2|telemetry.Vector{1:fl64:x;2:fl64:y;}";
begin
   Bench ("scalar", C, "telemetry.Vector", "x>0", False, 100000);
   Bench ("arithmetic", C, "telemetry.Vector", "x*x+y*y<=25", False, 100000);
   Sample.F_temperature := 12.34; Sample.F_position := V;
   for I in 1 .. 256 loop Sample.F_points.Append (V); end loop;
   Bench ("collection256", Description, "telemetry.Sample", "all(points,p=>p.x>=0&&p.y>=0)", True, 1000);
end Filter_Bench;
