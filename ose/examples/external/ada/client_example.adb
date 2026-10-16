with Ada.Command_Line;
with Chain_Client;
with Example_Handlers; use Example_Handlers;
with Interfaces;
procedure Client_Example is
   C : Chain_Client.Client;
   P : aliased Messages.Publisher;
   S : Messages.Subscription;
   Q : Chain_Client.Options;
begin
   Chain_Client.Connect (C, "127.0.0.1", Interfaces.Unsigned_16'Value (Ada.Command_Line.Argument (1)), "ada-example");
   Messages.Open (P, C, "ada.result");
   Q.Ack_Received := True; Q.Max_Retries := 3;
   Q.UDP := Ada.Command_Line.Argument_Count > 1 and then Ada.Command_Line.Argument (2) = "udp";
   Messages.Subscribe (S, C, "external.input", Receive'Access, Q, P'Address);
   Chain_Client.Run (C);
   -- S finalizes before P and C, joining callbacks before their state is released.
end Client_Example;
