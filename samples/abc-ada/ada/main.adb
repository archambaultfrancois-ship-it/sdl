with Ada.Command_Line;
with Ada.Exceptions;
with Ada.Numerics.Generic_Elementary_Functions;
with Ada.Strings.Fixed;
with Ada.Text_IO; use Ada.Text_IO;
with GNAT.Sockets; use GNAT.Sockets;
with Interfaces; use Interfaces;
with Framing;
with SDL_Runtime; use SDL_Runtime;
with SDL_ABC; use SDL_ABC;
procedure Main is
   package Reals is new Ada.Text_IO.Float_IO (Float64);
   package Math is new Ada.Numerics.Generic_Elementary_Functions (Float64);
   Input_Out, Input_In, Result_Out, Result_In : Socket_Type := No_Socket;

   protected Status is
      procedure Fail;
      function Failed return Boolean;
   private
      Has_Failed : Boolean := False;
   end Status;
   protected body Status is
      procedure Fail is
      begin
         Has_Failed := True;
      end Fail;
      function Failed return Boolean is (Has_Failed);
   end Status;

   procedure Report_Failure (Stage, Reason : String) is
   begin
      Status.Fail;
      Put_Line (Standard_Error, Stage & ": " & Reason);
   end Report_Failure;

   procedure Close (Socket : Socket_Type) is
   begin
      if Socket /= No_Socket then Close_Socket (Socket); end if;
   exception when E : others => Report_Failure ("Closing socket", Ada.Exceptions.Exception_Message (E));
   end Close;

   function Image (V : Float64) return String is
      Text : String (1 .. 32);
   begin
      Reals.Put (Text, V, Aft => 12, Exp => 3);
      return Ada.Strings.Fixed.Trim (Text, Ada.Strings.Both);
   end Image;

   function Kind_Name (Kind : T_EquationKind) return String is
   begin
      case Kind is
         when E_EquationKind_TWO_REAL => return "TWO_REAL";
         when E_EquationKind_ONE_REAL => return "ONE_REAL";
         when E_EquationKind_COMPLEX => return "COMPLEX";
         when E_EquationKind_INFINITE_SOLUTIONS => return "INFINITE_SOLUTIONS";
         when E_EquationKind_NO_SOLUTION => return "NO_SOLUTION";
         when others => raise Codec_Error with "unknown equation result";
      end case;
   end Kind_Name;

   procedure Show (M : T_EquationInput) is
   begin
      Put_Line ("Solver received:");
      Put_Line ("EquationInput {");
      Put_Line ("   a: " & Image (M.F_a));
      Put_Line ("   b: " & Image (M.F_b));
      Put_Line ("   c: " & Image (M.F_c));
      Put_Line ("}");
   end Show;

   procedure Show (M : T_EquationResult) is
   begin
      Put_Line ("Solver sending:");
      Put_Line ("EquationResult {");
      Put_Line ("   kind: " & Kind_Name (M.F_kind));
      if M.H_x1 then Put_Line ("   x1: " & Image (M.F_x1)); end if;
      if M.H_x2 then Put_Line ("   x2: " & Image (M.F_x2)); end if;
      if M.H_real_part then Put_Line ("   real_part: " & Image (M.F_real_part)); end if;
      if M.H_imaginary_part then Put_Line ("   imaginary_part: " & Image (M.F_imaginary_part)); end if;
      Put_Line ("}");
   end Show;

   function Solve (M : T_EquationInput) return T_EquationResult is
      R : T_EquationResult;
      D, Root : Float64;
   begin
      if M.F_a = 0.0 then
         if M.F_b = 0.0 then
            R.F_kind := (if M.F_c = 0.0 then E_EquationKind_INFINITE_SOLUTIONS else E_EquationKind_NO_SOLUTION);
         else
            R.F_kind := E_EquationKind_ONE_REAL; R.H_x1 := True; R.F_x1 := -M.F_c / M.F_b;
         end if;
      else
         D := M.F_b * M.F_b - 4.0 * M.F_a * M.F_c;
         if D > 0.0 then
            Root := Math.Sqrt (D);
            R.F_kind := E_EquationKind_TWO_REAL; R.H_x1 := True; R.H_x2 := True;
            R.F_x1 := (-M.F_b - Root) / (2.0 * M.F_a);
            R.F_x2 := (-M.F_b + Root) / (2.0 * M.F_a);
         elsif D = 0.0 then
            R.F_kind := E_EquationKind_ONE_REAL; R.H_x1 := True;
            R.F_x1 := -M.F_b / (2.0 * M.F_a);
         else
            R.F_kind := E_EquationKind_COMPLEX; R.H_real_part := True; R.H_imaginary_part := True;
            R.F_real_part := -M.F_b / (2.0 * M.F_a);
            R.F_imaginary_part := Math.Sqrt (-D) / abs (2.0 * M.F_a);
         end if;
      end if;
      return R;
   end Solve;

   task type Input_Worker;
   task type Solver_Worker;
   task type Display_Worker;

   task body Input_Worker is
      Socket : constant Socket_Type := Input_Out;
   begin
      begin
         declare
            M : T_EquationInput;
            C : constant Context := Prepare;
         begin
            Put ("Enter coefficients a, b, c for a*x^2 + b*x + c = 0: "); Flush;
            Reals.Get (M.F_a); Reals.Get (M.F_b); Reals.Get (M.F_c);
            if not (M.F_a'Valid and M.F_b'Valid and M.F_c'Valid) then
               raise Constraint_Error with "Please enter three finite real numbers.";
            end if;
            Framing.Send (Socket, Framing.To_Bytes (Description));
            Framing.Send (Socket, Encode (C, M));
         end;
      exception when E : others => Report_Failure ("Input", Ada.Exceptions.Exception_Message (E));
      end;
      Close (Socket);
   end Input_Worker;

   task body Solver_Worker is
      Incoming : constant Socket_Type := Input_In;
      Outgoing : constant Socket_Type := Result_Out;
   begin
      begin
         declare
            Catalogue : constant String := Framing.To_Text (Framing.Receive (Incoming));
            Input_Context : constant Context := Prepare (Catalogue);
            M : constant T_EquationInput := Decode (Input_Context, Framing.Receive (Incoming));
            R : constant T_EquationResult := Solve (M);
            Output_Context : constant Context := Prepare;
         begin
            Show (M); Show (R);
            Framing.Send (Outgoing, Framing.To_Bytes (Description));
            Framing.Send (Outgoing, Encode (Output_Context, R));
         end;
      exception when E : others => Report_Failure ("Solver", Ada.Exceptions.Exception_Message (E));
      end;
      Close (Incoming); Close (Outgoing);
   end Solver_Worker;

   task body Display_Worker is
      Socket : constant Socket_Type := Result_In;
   begin
      begin
         declare
            Catalogue : constant String := Framing.To_Text (Framing.Receive (Socket));
            C : constant Context := Prepare (Catalogue);
            R : constant T_EquationResult := Decode (C, Framing.Receive (Socket));
         begin
            case R.F_kind is
               when E_EquationKind_TWO_REAL =>
                  if not (R.H_x1 and R.H_x2) then raise Codec_Error with "missing roots"; end if;
                  Put_Line ("Two real roots: x1 = " & Image (R.F_x1) & ", x2 = " & Image (R.F_x2));
               when E_EquationKind_ONE_REAL =>
                  if not R.H_x1 then raise Codec_Error with "missing root"; end if;
                  Put_Line ("One real root: x = " & Image (R.F_x1));
               when E_EquationKind_COMPLEX =>
                  if not (R.H_real_part and R.H_imaginary_part) then raise Codec_Error with "missing complex roots"; end if;
                  Put_Line ("Complex roots: x = " & Image (R.F_real_part) & " +/- " & Image (R.F_imaginary_part) & "i");
               when E_EquationKind_INFINITE_SOLUTIONS => Put_Line ("Every real number is a solution.");
               when E_EquationKind_NO_SOLUTION => Put_Line ("There is no solution.");
               when others => raise Codec_Error with "unknown equation result";
            end case;
         end;
      exception when E : others => Report_Failure ("Display", Ada.Exceptions.Exception_Message (E));
      end;
      Close (Socket);
   end Display_Worker;
begin
   Initialize;
   Create_Socket_Pair (Input_Out, Input_In, Family_Unix);
   begin
      Create_Socket_Pair (Result_Out, Result_In, Family_Unix);
   exception when others => Close (Input_Out); Close (Input_In); raise;
   end;
   declare
      Input : Input_Worker;
      Solver : Solver_Worker;
      Display : Display_Worker;
   begin
      null; -- Leaving this scope waits for all three tasks to terminate.
   end;
   if Status.Failed then Ada.Command_Line.Set_Exit_Status (Ada.Command_Line.Failure); end if;
exception when E : others =>
   Put_Line (Standard_Error, Ada.Exceptions.Exception_Information (E));
   Ada.Command_Line.Set_Exit_Status (Ada.Command_Line.Failure);
end Main;
