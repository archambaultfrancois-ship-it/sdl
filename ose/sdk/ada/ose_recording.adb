with Ada.Strings.Unbounded; use Ada.Strings.Unbounded;
with Ose_Component;
with Ose_Text;
package body Ose_Recording is
   use type Ada.Containers.Count_Type;
   procedure Declare_Point (R : in out Registry; Name, Root, Catalogue, Description : String) is
      First : Boolean := True;
      Value : Point;
   begin
      if R.Frozen or else R.Points.Contains (Name) or else R.Points.Length >= 128 or else
        Name'Length = 0 or else Name'Length > 200 or else Description'Length > 1024 or else
        not Ose_Text.Valid_UTF8 (Description) then
         raise Program_Error with "invalid recording declaration";
      end if;
      for C of Name loop
         if C = '.' then
            if First then raise Program_Error with "empty recording name segment"; end if;
            First := True;
         else
            if not (C in 'a' .. 'z' or else C in 'A' .. 'Z' or else C = '_' or else
                    (not First and then C in '0' .. '9')) then
               raise Program_Error with "invalid recording name";
            end if;
            First := False;
         end if;
      end loop;
      if First then raise Program_Error with "empty recording name segment"; end if;
      declare
         Text : constant String := Root & Description & Catalogue;
      begin
         for C of Text loop
            if C = Character'Val (0) then raise Program_Error with "NUL in recording declaration"; end if;
         end loop;
      end;
      Value.Root := To_Unbounded_String (Root);
      Value.Encoder := SDL_Runtime.Prepare (Catalogue, Catalogue);
      Ose_Component.Declare_Recording (Name, Root, Catalogue, Description);
      R.Points.Insert (Name, Value);
   end Declare_Point;
   procedure Update (R : in out Registry; Active : String) is
      Start : Positive := Active'First;
   begin
      for Cursor in R.Points.Iterate loop
         R.Points.Reference (Cursor).Active := False;
      end loop;
      for I in Active'First .. Active'Last + 1 loop
         if I > Active'Last or else Active (I) = Character'Val (0) then
            if Active'Length = 0 then return; end if;
            declare
               Name : constant String := Active (Start .. I - 1);
            begin
               if not R.Points.Contains (Name) or else R.Points.Constant_Reference (Name).Active then
                  raise Ose_Component.Protocol_Error with "unknown or duplicate active recording point";
               end if;
               R.Points.Reference (Name).Active := True;
            end;
            Start := I + 1;
         end if;
      end loop;
   end Update;
   procedure Check_Type (R : Registry; Name, Root : String) is
   begin
      if not R.Points.Contains (Name) or else To_String (R.Points.Constant_Reference (Name).Root) /= Root then
         raise Program_Error with "unknown recording point or wrong SDL type";
      end if;
   end Check_Type;
   function Is_Enabled (R : Registry; Name : String) return Boolean is
   begin
      if not R.Points.Contains (Name) then raise Program_Error with "unknown recording point"; end if;
      return R.Points.Constant_Reference (Name).Active;
   end Is_Enabled;
   function Emit (R : in out Registry; Name : String; Payload : SDL_Runtime.Bytes) return Emit_Result is
      Size : constant Natural := Name'Length + 2 + Payload'Length;
   begin
      if not Is_Enabled (R, Name) then return Disabled; end if;
      if Size > 256 * 1024 - R.Tick_Bytes then return Dropped; end if;
      Ose_Component.Record_Bytes (Name, Payload);
      R.Tick_Bytes := R.Tick_Bytes + Size;
      return Queued;
   end Emit;
end Ose_Recording;
