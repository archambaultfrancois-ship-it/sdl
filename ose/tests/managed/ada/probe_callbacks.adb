with Interfaces;
package body Probe_Callbacks is
   use type Interfaces.Unsigned_64;
   use type Interfaces.IEEE_Float_64;
   protected body Received is
      procedure Signal is begin N := N + 1; end;
      function Count return Natural is (N);
   end Received;
   procedure Receive (Message : SDL_measurement.T_Measurement; Info : Chain_Client.Message_Info; User : System.Address) is
   begin
      if Info.Sequence <= 2**63 or Message.F_sequence /= Interfaces.Unsigned_64'Last or Message.F_value /= 3.5 then
         raise Program_Error with "incorrect decoded data";
      end if;
      if Fail then raise Program_Error with "expected callback failure" & Character'Val (0); end if;
      Received.Signal;
   end Receive;
end Probe_Callbacks;
