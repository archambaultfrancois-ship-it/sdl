with GNAT.Sockets;
with SDL_Runtime;
package Framing is
   -- Each connection carries one catalogue frame followed by one data frame.
   procedure Send (Socket : GNAT.Sockets.Socket_Type; Data : SDL_Runtime.Bytes);
   function Receive (Socket : GNAT.Sockets.Socket_Type) return SDL_Runtime.Bytes;
   function To_Bytes (Text : String) return SDL_Runtime.Bytes;
   function To_Text (Data : SDL_Runtime.Bytes) return String;
end Framing;
