with Ada.IO_Exceptions;
with Ada.Streams; use Ada.Streams;
with Interfaces; use Interfaces;
package body Framing is
   Max_Frame_Size : constant Unsigned_32 := 1024 * 1024;

   procedure Write_All (Socket : GNAT.Sockets.Socket_Type; Data : Stream_Element_Array) is
      Next : Stream_Element_Offset := Data'First;
      Last : Stream_Element_Offset;
   begin
      while Next <= Data'Last loop
         GNAT.Sockets.Send_Socket (Socket, Data (Next .. Data'Last), Last);
         if Last < Next then raise Ada.IO_Exceptions.End_Error with "socket closed while sending"; end if;
         Next := Last + 1;
      end loop;
   end Write_All;

   procedure Read_All (Socket : GNAT.Sockets.Socket_Type; Data : out Stream_Element_Array) is
      Next : Stream_Element_Offset := Data'First;
      Last : Stream_Element_Offset;
   begin
      while Next <= Data'Last loop
         GNAT.Sockets.Receive_Socket (Socket, Data (Next .. Data'Last), Last);
         if Last < Next then raise Ada.IO_Exceptions.End_Error with "socket closed while receiving"; end if;
         Next := Last + 1;
      end loop;
   end Read_All;

   procedure Send (Socket : GNAT.Sockets.Socket_Type; Data : SDL_Runtime.Bytes) is
      Header : Stream_Element_Array (1 .. 4);
      Payload : Stream_Element_Array (1 .. Stream_Element_Offset (Data'Length));
      N : Unsigned_32;
   begin
      if Data'Length = 0 or else Data'Length > Natural (Max_Frame_Size) then
         raise Ada.IO_Exceptions.Data_Error with "invalid frame size";
      end if;
      N := Unsigned_32 (Data'Length);
      for I in Header'Range loop
         Header (I) := Stream_Element (Shift_Right (N, Natural (4 - I) * 8) and 255);
      end loop;
      for I in Payload'Range loop
         Payload (I) := Stream_Element (Data (Data'First + Natural (I) - 1));
      end loop;
      Write_All (Socket, Header);
      Write_All (Socket, Payload);
   end Send;

   function Receive (Socket : GNAT.Sockets.Socket_Type) return SDL_Runtime.Bytes is
      Header : Stream_Element_Array (1 .. 4);
      N : Unsigned_32 := 0;
   begin
      Read_All (Socket, Header);
      for B of Header loop N := Shift_Left (N, 8) or Unsigned_32 (B); end loop;
      if N = 0 or else N > Max_Frame_Size then
         raise Ada.IO_Exceptions.Data_Error with "invalid frame size";
      end if;
      declare
         Payload : Stream_Element_Array (1 .. Stream_Element_Offset (N));
         Data : SDL_Runtime.Bytes (1 .. Natural (N));
      begin
         Read_All (Socket, Payload);
         for I in Data'Range loop Data (I) := Unsigned_8 (Payload (Stream_Element_Offset (I))); end loop;
         return Data;
      end;
   end Receive;

   function To_Bytes (Text : String) return SDL_Runtime.Bytes is
      Data : SDL_Runtime.Bytes (1 .. Text'Length);
   begin
      for I in Data'Range loop Data (I) := Character'Pos (Text (Text'First + I - 1)); end loop;
      return Data;
   end To_Bytes;

   function To_Text (Data : SDL_Runtime.Bytes) return String is
      Text : String (1 .. Data'Length);
   begin
      for I in Text'Range loop Text (I) := Character'Val (Data (Data'First + I - 1)); end loop;
      return Text;
   end To_Text;
end Framing;
