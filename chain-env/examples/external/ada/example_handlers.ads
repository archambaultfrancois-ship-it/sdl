with Chain_Client;
with Chain_Client.Typed;
with SDL_measurement;
with System;
package Example_Handlers is
   package Messages is new Chain_Client.Typed
     (SDL_measurement.T_Measurement, "Measurement", SDL_measurement.Description,
      SDL_measurement.Encode, SDL_measurement.Decode);
   procedure Receive (Message : SDL_measurement.T_Measurement;
                      Info : Chain_Client.Message_Info; User : System.Address);
end Example_Handlers;
