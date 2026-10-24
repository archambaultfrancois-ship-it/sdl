with Ada.Finalization;
with Ada.Strings.Unbounded;
with Interfaces;
with SDL_Runtime;
with System;
package Chain_Parameters is
   Parameter_Error : exception;
   type Client is new Ada.Finalization.Limited_Controlled with private;
   -- Supply Token for local TCP, or CA/Certificate/Key for distributed mTLS.
   procedure Connect (Self : in out Client; Host : String; Port : Interfaces.Unsigned_16;
                      Id : String; Token : String := ""; CA : String := "";
                      Certificate : String := ""; Key : String := "");
   procedure Close (Self : in out Client);
   overriding procedure Finalize (Self : in out Client);
   type Packet is record
      Header, Path, Root : Ada.Strings.Unbounded.Unbounded_String;
      Payload : SDL_Runtime.Buffer;
   end record;
   -- One complete request/reply is serialized by the native client. Do not
   -- close/finalize the client concurrently with a call using it.
   function Request (Self : Client; Operation : String; Arguments : String := "{}";
                     Payload : SDL_Runtime.Bytes := (1 .. 0 => 0)) return Packet;
   function List (Self : Client) return String;
   procedure Subscribe (Self : Client; Paths_JSON : String);
   function Next (Self : Client) return Packet;
   procedure Unsubscribe (Self : Client);
private
   function Path_Arguments (Path : String) return String;
   type Client is new Ada.Finalization.Limited_Controlled with record
      Native : System.Address := System.Null_Address;
   end record;
end Chain_Parameters;
