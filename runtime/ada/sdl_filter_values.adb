with Ada.Unchecked_Deallocation;
package body SDL_Filter_Values is
   use type Interfaces.C.size_t;
   procedure Free is new Ada.Unchecked_Deallocation (String, String_Access);
   overriding procedure Finalize (S : in out Scratch) is
   begin
      for I in S.Texts'Range loop Free (S.Texts (I)); end loop;
   end Finalize;
   function Missing return Value is ((others => <>));
   function Invalid return Value is ((Kind => 99, others => <>));
   function Message_Value return Value is ((Kind => 7, others => <>));
   function Array_Value (N : Interfaces.C.size_t) return Value is ((Kind => 6, Count => N, others => <>));
   function Bool_Value (V : Boolean) return Value is ((Kind => 1, Unsigned_Value => Boolean'Pos (V), others => <>));
   function Signed (V : Interfaces.Integer_64) return Value is ((Kind => 2, Signed_Value => V, others => <>));
   function Unsigned (V : Interfaces.Unsigned_64) return Value is ((Kind => 3, Unsigned_Value => V, others => <>));
   function Floating (V : Interfaces.IEEE_Float_64) return Value is ((Kind => 4, Re => V, others => <>));
   function Complex (Re, Im : Interfaces.IEEE_Float_64) return Value is ((Kind => 8, Re => Re, Im => Im, others => <>));
   function Text_Value (S : in out Scratch; Slot : Natural;
      V : Ada.Strings.Unbounded.Unbounded_String) return Value is
   begin
      if Slot not in S.Texts'Range then return Invalid; end if;
      Free (S.Texts (Slot)); S.Texts (Slot) := new String'(Ada.Strings.Unbounded.To_String (V));
      return (Kind => 5, Bytes => (S.Texts (Slot).all'Address,
         Interfaces.C.size_t (S.Texts (Slot)'Length)), others => <>);
   end Text_Value;
   function Owner_Is (S : Step; Name : String) return Boolean is
   begin
      if S.Owner.Size /= Interfaces.C.size_t (Name'Length) then return False; end if;
      declare
         Text : String (1 .. Name'Length) with Import, Address => S.Owner.Data;
      begin return Text = Name; end;
   end Owner_Is;
end SDL_Filter_Values;
