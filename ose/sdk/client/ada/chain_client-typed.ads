with SDL_Runtime;
generic
   type Message_Type is private;
   Root, Catalogue : String;
   with function Encode (Ctx : SDL_Runtime.Context; Message : Message_Type) return SDL_Runtime.Bytes;
   with function Decode (Ctx : SDL_Runtime.Context; Data : SDL_Runtime.Bytes) return Message_Type;
package Chain_Client.Typed is
   type Publisher is new Ada.Finalization.Limited_Controlled with private;
   procedure Open (Self : in out Publisher; Owner : Client; Topic : String);
   function Publish (Self : Publisher; Message : Message_Type) return String;
   procedure Close (Self : in out Publisher);
   overriding procedure Finalize (Self : in out Publisher);
   type On_Message is access procedure (Message : Message_Type; Info : Message_Info; User : System.Address);
   type Subscription is new Ada.Finalization.Limited_Controlled with private;
   procedure Subscribe (Self : in out Subscription; Owner : Client; Topic : String;
                        Callback : not null On_Message; QoS : Options := (others => <>);
                        User : System.Address := System.Null_Address);
   procedure Unsubscribe (Self : in out Subscription);
   procedure Close (Self : in out Subscription) renames Unsubscribe;
   function Wait_Closed (Self : Subscription; Timeout_Ms : Interfaces.Unsigned_32) return Boolean;
   function Stats (Self : Subscription) return String;
   overriding procedure Finalize (Self : in out Subscription);
private
   type Publisher is new Ada.Finalization.Limited_Controlled with record
      Owner : Handle := System.Null_Address;
      Topic : Ada.Strings.Unbounded.Unbounded_String;
      Encoder : SDL_Runtime.Context;
      Closed : Boolean := True;
   end record;
   type Subscription is new Ada.Finalization.Limited_Controlled with record
      Native, State : System.Address := System.Null_Address;
   end record;
end Chain_Client.Typed;
