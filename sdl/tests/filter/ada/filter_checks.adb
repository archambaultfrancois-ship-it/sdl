with SDL.telemetry.model; use SDL.telemetry.model;
with SDL_Filter;
with SDL_Runtime; use SDL_Runtime;
with Ada.Strings.Unbounded; use Ada.Strings.Unbounded;
with Interfaces; use Interfaces;
with Ada.Text_IO;
with Filter_Cases;
procedure Filter_Checks is
   use type SDL_Filter.Status;
   package Sample_Filters is new SDL_Filter.Typed (T_telemetry_Anon_Sample,
      "telemetry.Sample", Filter_Read);
   V : aliased T_telemetry_Anon_Sample;
   Context : SDL_Runtime.Context := Prepare;
   function State (Code : Integer) return SDL_Filter.Status is
      (case Code is when 1 => SDL_Filter.Match, when 0 => SDL_Filter.No_Match,
       when 2 => SDL_Filter.Indeterminate, when others => SDL_Filter.Error);
begin
   V.F_sequence := Unsigned_64'Last; V.F_temperature := 12.34; V.F_active := True;
   V.H_detail := True; V.F_detail.F_code := 0; V.F_detail.F_label := To_Unbounded_String ("ok");
   V.F_position := (3.0, 4.0);
   V.F_points.Append (T_telemetry_Anon_Vector'(1.0, 2.0));
   V.F_points.Append (T_telemetry_Anon_Vector'(10.0, 20.0));
   V.F_points.Append (T_telemetry_Anon_Vector'(-1.0, 0.5));
   V.F_matrix := ((1, 2, 3), (4, 5, 6));
   V.F_label := To_Unbounded_String ("caf" & Character'Val (195) & Character'Val (169));
   V.F_counts.Append (-2); V.F_counts.Append (0); V.F_counts.Append (7);
   V.F_samples.Append (Complex32'(1.0, 2.0)); V.F_samples.Append (Complex32'(-3.0, 0.5));
   V.F_state := E_telemetry_Anon_State_ON;
   declare
      Wire : constant Bytes := Encode (Context, V);
   begin
      for I in Filter_Cases.Expressions'Range loop
         declare
            P : SDL_Filter.Predicate := SDL_Filter.Prepare (Description, "telemetry.Sample", Filter_Cases.Expressions (I).all);
            A : constant SDL_Filter.Result := Sample_Filters.Evaluate (P, V);
            B : constant SDL_Filter.Result := SDL_Filter.Evaluate_Wire (P, Wire);
         begin
            if A.State /= State (Filter_Cases.Object_States (I)) or B.State /= State (Filter_Cases.Wire_States (I)) then
               Ada.Text_IO.Put_Line (Filter_Cases.Expressions (I).all & " object=" & A.State'Image & " wire=" & B.State'Image & " " & To_String (A.Diagnostic) & " " & To_String (B.Diagnostic));
               raise Program_Error;
            end if;
         end;
      end loop;
   end;
   Ada.Text_IO.Put_Line ("Ada filters passed.");
end Filter_Checks;
