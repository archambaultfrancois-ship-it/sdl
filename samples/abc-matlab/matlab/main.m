function main(varargin)
% Three sequential stages exchange SDL messages as encoded byte frames.
   input_stage = EquationInput('new');
   solver_stage = EquationResult('new');
   display_stage = EquationResult('new');

   input_stage = input_stage_run(input_stage, varargin{:});
   input_frame = EquationInput('encode', input_stage);
   solver_input = EquationInput('decode', input_frame);
   solver_stage = solver_stage_run(solver_input, solver_stage);
   result_frame = EquationResult('encode', solver_stage);
   display_stage = EquationResult('decode', result_frame);
   display_stage_run(display_stage);
end

function value = input_stage_run(value, varargin)
   if numel(varargin) == 3
      numbers = cellfun(@double, varargin);
   elseif isempty(varargin)
      fprintf('Enter coefficients a, b, c for a*x^2 + b*x + c = 0: ');
      line = input('', 's');
      numbers = sscanf(line, '%f');
   else
      error('Provide either zero or three equation coefficients.');
   end
   if numel(numbers) ~= 3 || any(~isfinite(numbers))
      error('Please enter three finite real numbers.');
   end
   value.a = numbers(1);
   value.b = numbers(2);
   value.c = numbers(3);
end

function result = solver_stage_run(value, result)
   fprintf('Solver received:\n%s\n', EquationInput('display', value));
   if value.a == 0
      if value.b == 0
         if value.c == 0
            result.kind = EquationKind('INFINITE_SOLUTIONS');
         else
            result.kind = EquationKind('NO_SOLUTION');
         end
      else
         result.kind = EquationKind('ONE_REAL');
         result.x1 = -value.c / value.b;
      end
   else
      discriminant = value.b * value.b - 4 * value.a * value.c;
      if discriminant > 0
         root = sqrt(discriminant);
         result.kind = EquationKind('TWO_REAL');
         result.x1 = (-value.b - root) / (2 * value.a);
         result.x2 = (-value.b + root) / (2 * value.a);
      elseif discriminant == 0
         result.kind = EquationKind('ONE_REAL');
         result.x1 = -value.b / (2 * value.a);
      else
         result.kind = EquationKind('COMPLEX');
         result.real_part = -value.b / (2 * value.a);
         result.imaginary_part = sqrt(-discriminant) / abs(2 * value.a);
      end
   end
   fprintf('Solver sending:\n%s\n', EquationResult('display', result));
end

function display_stage_run(result)
   switch double(result.kind)
      case 1
         fprintf('Two real roots: x1 = %.12g, x2 = %.12g\n', result.x1, result.x2);
      case 2
         fprintf('One real root: x = %.12g\n', result.x1);
      case 3
         fprintf('Complex roots: x = %.12g +/- %.12gi\n', result.real_part, result.imaginary_part);
      case 4
         fprintf('Every real number is a solution.\n');
      case 5
         fprintf('There is no solution.\n');
      otherwise
         error('Received an unknown equation result.');
   end
end
