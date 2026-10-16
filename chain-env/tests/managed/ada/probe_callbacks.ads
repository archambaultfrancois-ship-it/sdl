with Chain_Client;
with Chain_Client.Typed;
with SDL_measurement;
with System;
package Probe_Callbacks is
   package Messages is new Chain_Client.Typed (SDL_measurement.T_Measurement,
      "Measurement", SDL_measurement.Description, SDL_measurement.Encode, SDL_measurement.Decode);
   Fail : Boolean := False with Atomic;
   procedure Receive (Message : SDL_measurement.T_Measurement; Info : Chain_Client.Message_Info; User : System.Address);
   protected Received is
      procedure Signal;
      function Count return Natural;
   private
      N : Natural := 0;
   end Received;
end Probe_Callbacks;
