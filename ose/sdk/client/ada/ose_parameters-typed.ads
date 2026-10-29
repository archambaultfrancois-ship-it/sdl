with SDL_Runtime;
with Ada.Strings.Unbounded;
generic
   type Message_Type is private;
   Root, Catalogue : String;
   with function Encode (Ctx : SDL_Runtime.Context; Message : Message_Type) return SDL_Runtime.Bytes;
   with function Decode (Ctx : SDL_Runtime.Context; Data : SDL_Runtime.Bytes) return Message_Type;
package Ose_Parameters.Typed is
   type Block is private;
   procedure Open (Self : in out Block; Owner : Client; Path : String);
   function Get (Self : Block; Owner : Client) return Message_Type;
   function Decode_Update (Self : Block; Update : Packet) return Message_Type;
   procedure Set (Self : Block; Owner : Client; Value : Message_Type);
private
   type Block is record
      Path : Ada.Strings.Unbounded.Unbounded_String;
      Codec : SDL_Runtime.Context;
   end record;
end Ose_Parameters.Typed;
