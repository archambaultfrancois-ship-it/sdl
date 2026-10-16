with Ada.Finalization;
with Ada.Strings.Unbounded;
with Interfaces;
with Interfaces.C;
with Interfaces.C.Strings;
with System;
package Chain_Client is
   Client_Error : exception;
   type Client is new Ada.Finalization.Limited_Controlled with private;
   procedure Connect (Self : in out Client; Host : String; Port : Interfaces.Unsigned_16; Id : String);
   procedure Close (Self : in out Client);
   procedure Run (Self : in out Client);
   function List_Topics (Self : Client) return String;
   overriding procedure Finalize (Self : in out Client);
   type Buffer_Kind is (FIFO, Latest);
   type Overflow_Kind is (Reject_New, Drop_Oldest, Wait);
   type Options is record
      Buffer : Buffer_Kind := FIFO;
      Capacity : Positive := 256;
      Overflow : Overflow_Kind := Reject_New;
      Ack_Received : Boolean := False;
      Max_Retries : Natural := 0;
      Ack_Timeout_Ms : Positive := 100;
      Backoff_Multiplier : Positive := 2;
      Wait_Timeout_Ms : Natural := 100;
      UDP : Boolean := False;
   end record;
   function Latest_Options return Options;
   type Message_Info is record
      Publication_Id, Topic, Epoch : Ada.Strings.Unbounded.Unbounded_String;
      Sequence : Interfaces.Unsigned_64;
   end record;
private
   subtype Handle is System.Address;
   type Client is new Ada.Finalization.Limited_Controlled with record
      Native : Handle := System.Null_Address;
   end record;
   function Policy (Value : Options) return String;
   function C_String (Text : String) return Interfaces.C.char_array;
   function Take_String (Value : Interfaces.C.Strings.chars_ptr) return String;
   procedure Check (Result : Interfaces.C.int);
   procedure Hold (Value : Handle) with Import, Convention => C, External_Name => "chain_ada_hold";
   procedure Release (Value : Handle) with Import, Convention => C, External_Name => "chain_ada_release";
   function Raw (Value : Handle) return System.Address with Import, Convention => C, External_Name => "chain_ada_raw";
   function Wrap (Value : System.Address; Kind : Interfaces.C.int) return Handle
     with Import, Convention => C, External_Name => "chain_ada_wrap";
   procedure Enter_Ada with Import, Convention => C, External_Name => "chain_ada_enter";
   procedure Leave_Ada with Import, Convention => C, External_Name => "chain_ada_leave";
   procedure Set_Failure (Value : Handle; Text : Interfaces.C.char_array)
     with Import, Convention => C, External_Name => "chain_ada_failure";
   function Failure (Value : Handle) return Interfaces.C.Strings.chars_ptr
     with Import, Convention => C, External_Name => "chain_ada_failure_copy";
end Chain_Client;
