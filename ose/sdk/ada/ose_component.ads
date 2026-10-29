with Ada.Finalization;
with Ada.Strings.Unbounded;
with Interfaces;
with Interfaces.C;
with SDL_Runtime;
with System;
package Ose_Component is
   Protocol_Error : exception;
   type Event is new Ada.Finalization.Limited_Controlled with private;
   procedure Read_Event (E : in out Event);
   function Op (E : Event) return Character;
   function Body_Text (E : Event) return String;
   function Body_Size (E : Event) return Natural;
   procedure Input (E : Event; Port, Catalogue : out Ada.Strings.Unbounded.Unbounded_String;
                    Payload : out SDL_Runtime.Buffer);
   procedure Tick (E : Event; Time_Ns, Tick_Index : in out Interfaces.Unsigned_64);
   procedure Ready (Parameters : Boolean := False);
   procedure Set_Parameters (Name : String; Payload : SDL_Runtime.Bytes);
   procedure Finish;
   procedure Publish (Port : String; Payload : SDL_Runtime.Bytes);
   overriding procedure Finalize (E : in out Event);
private
   type Raw_Event is record
      Code : Interfaces.C.char;
      Data : System.Address;
      Size : Interfaces.C.size_t;
   end record with Convention => C;
   type Event is new Ada.Finalization.Limited_Controlled with record
      Raw : aliased Raw_Event := (Interfaces.C.nul, System.Null_Address, 0);
   end record;
end Ose_Component;
