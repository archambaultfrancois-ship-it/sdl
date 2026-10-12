with Ada.Finalization;
with Ada.Strings.Unbounded;
with Interfaces.C;
with SDL_Runtime;
with SDL_Filter_Values;
with System;
package SDL_Filter is
   type Status is (Error, No_Match, Match, Indeterminate);
   type Result is record
      State : Status := Error;
      Diagnostic : Ada.Strings.Unbounded.Unbounded_String;
   end record;
   type Predicate is new Ada.Finalization.Limited_Controlled with private;
   function Prepare (Catalogue, Root_Type, Expression : String) return Predicate;
   overriding procedure Finalize (P : in out Predicate);
   function Evaluate_Wire (P : Predicate; Data : SDL_Runtime.Bytes) return Result;
   generic
      type Message_Type (<>) is private;
      Root_Type : String;
      with function Read (M : Message_Type; Path : SDL_Filter_Values.Path;
         Slot : Natural; Work : in out SDL_Filter_Values.Scratch) return SDL_Filter_Values.Value;
   package Typed is
      function Evaluate (P : Predicate; M : aliased in Message_Type) return Result;
   end Typed;
private
   type Predicate is new Ada.Finalization.Limited_Controlled with record
      Handle : System.Address := System.Null_Address;
   end record;
end SDL_Filter;
