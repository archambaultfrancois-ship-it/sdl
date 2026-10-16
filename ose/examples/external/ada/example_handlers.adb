with Ada.Unchecked_Conversion;
with Ada.Text_IO;
with SDL_Runtime;
package body Example_Handlers is
   type Publisher_Access is access all Messages.Publisher;
   function Publisher_Ptr is new Ada.Unchecked_Conversion (System.Address, Publisher_Access);
   procedure Receive (Message : SDL_measurement.T_Measurement;
                      Info : Chain_Client.Message_Info; User : System.Address) is
      use type SDL_Runtime.Float64;
      Result : SDL_measurement.T_Measurement := Message;
   begin
      Result.F_value := Result.F_value * 2.0;
      Ada.Text_IO.Put_Line (Ada.Text_IO.Standard_Error, Messages.Publish (Publisher_Ptr (User).all, Result));
   end Receive;
end Example_Handlers;
