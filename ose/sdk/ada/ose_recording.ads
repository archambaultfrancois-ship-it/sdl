with Ada.Containers.Indefinite_Hashed_Maps;
with Ada.Strings.Hash;
with Ada.Strings.Unbounded;
with SDL_Runtime;
package Ose_Recording is
   type Emit_Result is (Queued, Disabled, Dropped);
   type Point is record
      Root : Ada.Strings.Unbounded.Unbounded_String;
      Encoder : SDL_Runtime.Context;
      Active : Boolean := False;
   end record;
   package Point_Maps is new Ada.Containers.Indefinite_Hashed_Maps
     (String, Point, Ada.Strings.Hash, "=");
   type Registry is limited record
      Points : Point_Maps.Map;
      Frozen : Boolean := False;
      Tick_Bytes : Natural := 0;
   end record;
   procedure Declare_Point (R : in out Registry; Name, Root, Catalogue, Description : String);
   procedure Update (R : in out Registry; Active : String);
   procedure Check_Type (R : Registry; Name, Root : String);
   function Is_Enabled (R : Registry; Name : String) return Boolean;
   function Emit (R : in out Registry; Name : String; Payload : SDL_Runtime.Bytes) return Emit_Result;
end Ose_Recording;
