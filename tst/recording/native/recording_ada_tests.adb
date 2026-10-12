with Ada.Command_Line;
with Ada.Strings.Unbounded;
with Ada.Text_IO;
with SDL_Recording;
procedure Recording_Ada_Tests is
   use SDL_Recording;
   use type UInt64;
   use type UInt32;
   use Ada.Strings.Unbounded;
   Path : constant String := Ada.Command_Line.Argument (1);
   Seen : Natural := 0;
   procedure Consume (Item : Recording_Record) is
   begin
      pragma Assert (Item.Log_Time_Ns = UInt64'Last);
      Seen := Seen + 1;
   end Consume;
begin
   declare
      W : Writer := Create (Path);
      C : constant UInt16 := Register_Channel (W, "ada/packet", "SDL2|Packet{}", "Packet");
   begin
      Write_Encoded (W, C, (1 => 1), UInt64'Last, UInt64'Last, UInt32'Last);
      Flush (W, True); Close (W);
   end;
   pragma Assert (Validate (Path) = 1);
   declare
      R : Reader := Open (Path);
      Item : Recording_Record;
   begin
      pragma Assert (Next (R, Item));
      pragma Assert (To_String (Item.Topic) = "ada/packet");
      pragma Assert (Item.Sequence = UInt32'Last);
      pragma Assert (To_Bytes (Item.Payload) = Bytes'(1 => 1));
      pragma Assert (not Next (R, Item)); Close (R);
   end;
   declare
      R : Reader := Open (Path);
      Count : constant UInt64 := Replay (R, 0.0, Consume'Access);
   begin
      pragma Assert (Count = 1 and Seen = 1); Close (R);
   end;
   Ada.Text_IO.Put_Line ("Ada recording tests passed");
end Recording_Ada_Tests;
