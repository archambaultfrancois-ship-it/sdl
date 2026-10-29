with Ada.Command_Line;
with Ada.Text_IO;
with Ada.Exceptions;
with Ada.Strings.Fixed;
with Ose_Client;
with Interfaces;
with SDL_measurement;
with Probe_Callbacks; use Probe_Callbacks;
procedure External_Probe is
   C : Ose_Client.Client;
   P : Messages.Publisher;
   Q : Ose_Client.Options;
   M : SDL_measurement.T_Measurement;
begin
   Ose_Client.Connect (C, "127.0.0.1", Interfaces.Unsigned_16'Value (Ada.Command_Line.Argument (1)), "ada-probe");
   Messages.Open (P, C, "ada.topic");
   M.F_sequence := Interfaces.Unsigned_64'Last; M.F_value := 3.5;
   for I in 1 .. 2 loop
      declare
         S : Messages.Subscription;
         Before : constant Natural := Received.Count;
      begin
         if I = 2 then Q := Ose_Client.Latest_Options; Q.UDP := True; end if;
         Q.Ack_Received := True; Q.Max_Retries := 2;
         Messages.Subscribe (S, C, "ada.topic", Receive'Access, Q);
         Ada.Text_IO.Put_Line (Messages.Publish (P, M));
         for J in 1 .. 500 loop
            exit when Received.Count > Before;
            delay 0.01;
         end loop;
         if Received.Count = Before then raise Program_Error with "delivery timed out"; end if;
         Ada.Text_IO.Put_Line (Messages.Stats (S));
         Messages.Unsubscribe (S);
         if not Messages.Wait_Closed (S, 3000) then raise Program_Error with "workers did not stop"; end if;
      end;
   end loop;
   Ada.Text_IO.Put_Line (Ose_Client.List_Topics (C));
   declare
      B : Ose_Client.Client;
      S : Messages.Subscription;
   begin
      Fail := True;
      Ose_Client.Connect (B, "127.0.0.1", Interfaces.Unsigned_16'Value (Ada.Command_Line.Argument (1)), "ada-failure");
      Messages.Subscribe (S, B, "ada.topic", Receive'Access);
      Ada.Text_IO.Put_Line (Messages.Publish (P, M));
      begin
         Ose_Client.Run (B);
         raise Program_Error with "callback failure was not propagated";
      exception when Error : Ose_Client.Client_Error =>
         if Ada.Strings.Fixed.Index (Ada.Exceptions.Exception_Message (Error), "expected callback failure") = 0 then raise; end if;
      end;
      Messages.Unsubscribe (S);
      if not Messages.Wait_Closed (S, 3000) then raise Program_Error with "failed subscription did not stop"; end if;
   end;
   Messages.Close (P); Ose_Client.Close (C); Ose_Client.Close (C);
   Ada.Text_IO.Put_Line ("ada external OK");
end External_Probe;
