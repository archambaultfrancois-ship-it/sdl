with Chain_Text;
package body Chain_Parameters.Typed is
   use Ada.Strings.Unbounded;
   procedure Open (Self : in out Block; Owner : Client; Path : String) is
      Reply : constant Packet := Request (Owner, "parameters_describe", Path_Arguments (Path));
      Bytes : constant SDL_Runtime.Bytes := SDL_Runtime.To_Bytes (Reply.Payload);
      Text : String (1 .. Bytes'Length);
   begin
      if To_String (Reply.Root) /= Root then raise Parameter_Error with "wrong parameter root"; end if;
      for I in Text'Range loop Text (I) := Character'Val (Bytes (Bytes'First + I - 1)); end loop;
      if not Chain_Text.Valid_UTF8 (Text) or else
         not SDL_Runtime.Is_Compatible (SDL_Runtime.Compare_Catalogues (Text, Catalogue, Root, True)) then
         raise Parameter_Error with "incompatible parameter catalogue";
      end if;
      Self.Codec := SDL_Runtime.Prepare (Text, Catalogue);
      Self.Path := To_Unbounded_String (Path);
   end Open;
   function Decode_Update (Self : Block; Update : Packet) return Message_Type is
   begin
      if Length (Self.Path) = 0 or else Update.Path /= Self.Path then raise Parameter_Error with "wrong parameter block"; end if;
      return Decode (Self.Codec, SDL_Runtime.To_Bytes (Update.Payload));
   end Decode_Update;
   function Get (Self : Block; Owner : Client) return Message_Type is
   begin
      return Decode_Update (Self, Request (Owner, "parameters_get", Path_Arguments (To_String (Self.Path))));
   end Get;
   procedure Set (Self : Block; Owner : Client; Value : Message_Type) is
      Reply : constant Packet := Request (Owner, "parameters_set", Path_Arguments (To_String (Self.Path)), Encode (Self.Codec, Value));
      pragma Unreferenced (Reply);
   begin null; end Set;
end Chain_Parameters.Typed;
