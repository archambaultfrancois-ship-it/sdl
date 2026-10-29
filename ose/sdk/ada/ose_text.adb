package body Ose_Text is
   function Valid_UTF8 (Text : String) return Boolean is
      I : Integer := Text'First;
      C, N, Minimum : Natural;
   begin
      while I <= Text'Last loop
         C := Character'Pos (Text (I)); I := I + 1;
         if C < 128 then N := 0; Minimum := 0;
         elsif C in 194 .. 223 then C := C - 192; N := 1; Minimum := 128;
         elsif C in 224 .. 239 then C := C - 224; N := 2; Minimum := 2048;
         elsif C in 240 .. 244 then C := C - 240; N := 3; Minimum := 65536;
         else return False;
         end if;
         if N > Natural (Text'Last - I + 1) then return False; end if;
         for J in 1 .. N loop
            declare B : constant Natural := Character'Pos (Text (I));
            begin
               if B not in 128 .. 191 then return False; end if;
               C := C * 64 + B - 128; I := I + 1;
            end;
         end loop;
         if C < Minimum or C > 16#10FFFF# or C in 16#D800# .. 16#DFFF# then return False; end if;
      end loop;
      return True;
   end Valid_UTF8;
end Ose_Text;
