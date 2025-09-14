import atexit
import re
import math  # Add this import at the top of the file

# TODO Store instances in the class 
class CalcDict(dict):
    __instances = []

    def __init__(self, name):
        self.__dict__['name'] = name
        CalcDict.__instances.append(self)
    def __getattr__(self, key):
        return self[key]
    def __setattr__(self, key, value):
        if isinstance(value, str):
            self[key] = self.eval_rpn(value)
        else:
            self[key] = float(value)

    def get_totals_output(self):
        output_str = ""
        output_str += f"{self.name}\n"
        output_str += "-" * 40 + "\n"
        # Handling addition of lists of len 1, lists of len > 1
        
        # Resusively add up floats and sublists of floats and sublists
        def sum_up(value):
            if isinstance(value, list):
                return sum(sum_up(item) for item in value)
            try:
                return float(value)
            except (ValueError, TypeError):
                return 0

        total = 0
        for key, value in self.items():
            output_str += f"{key}: {value} = {sum_up(value)}\n"
            total += sum_up(value)
        output_str += f"Total: {total}\n"
        return output_str
        
    # Treat the string as a series of assignments, one per line
    # The variable name to assign to is the first word, the expression
    # is the rest
    def assn(self, input_str):
        for line in input_str.strip().split('\n'):
            if not line.strip():
                continue
            # Remove comments before processing
            line = re.sub(r'#.*$', '', line.strip())
            if not line:
                continue
            match = re.match(r'(\S+)\s*(.*)', line)
            if not match:
                continue
            var_name = match.group(1)
            expression = match.group(2)

            if not var_name.isidentifier():
                raise ValueError(f"Invalid variable name: {var_name}")

            # uses eval_rpn in __setattr__??? It doesn't seem to??
            # is __setattr_ only called when you use dot but not [key]
            self[var_name] = self.eval_rpn(expression)

    def eval_rpn(self, expression):
        stack = []
        for token in expression.split():
            if token.startswith('$'):
                var_name = token[1:]  # Remove the $ prefix
                if var_name not in self:
                    raise KeyError(f"Variable {var_name} not found")
                stack.append(self[var_name])
            # Basic arithmetic operations
            elif token in {'+', '-', '*', '/'}:
                b, a = stack.pop(), stack.pop()
                if token == '+': stack.append(a + b)
                elif token == '-': stack.append(a - b)
                elif token == '*': stack.append(a * b)
                elif token == '/': stack.append(a / b)
            # N-ary operations
            elif token in {'n+', 'n-', 'n*', 'n/'}:
                if token == 'n+':
                    total = sum(stack)
                elif token == 'n-':
                    total = stack[0] - sum(stack[1:])
                elif token == 'n*':
                    total = math.prod(stack)  # Using math.prod instead of manual multiplication
                elif token == 'n/':
                    total = stack[0]
                    for x in stack[1:]:
                        total /= x
                stack.clear()
                stack.append(total)
            # Constants
            elif token == 'pi':
                stack.append(math.pi)
            elif token == 'e':
                stack.append(math.e)
            # Trigonometric functions
            elif token in {'sin', 'cos', 'tan'}:
                x = stack.pop()
                if token == 'sin': stack.append(math.sin(x))
                elif token == 'cos': stack.append(math.cos(x))
                elif token == 'tan': stack.append(math.tan(x))
            # Inverse trigonometric functions
            elif token in {'asin', 'acos', 'atan'}:
                x = stack.pop()
                if token == 'asin': stack.append(math.asin(x))
                elif token == 'acos': stack.append(math.acos(x))
                elif token == 'atan': stack.append(math.atan(x))
            # Logarithmic functions
            elif token == 'log':
                x = stack.pop()
                stack.append(math.log10(x))
            elif token == 'ln':
                x = stack.pop()
                stack.append(math.log(x))
            # Power functions
            elif token == 'pow':
                b, a = stack.pop(), stack.pop()
                stack.append(math.pow(a, b))
            elif token == 'sqrt':
                x = stack.pop()
                stack.append(math.sqrt(x))
            # Other mathematical functions
            elif token == 'abs':
                x = stack.pop()
                stack.append(abs(x))
            elif token == 'round':
                x = stack.pop()
                stack.append(round(x))
            elif token == 'swap':
                b, a = stack.pop(), stack.pop()
                stack.append(b)
                stack.append(a)
            else:
                try:
                    stack.append(float(token))
                except ValueError:
                    raise ValueError(f"Unknown token: {token}")
        
        if len(stack) == 1:
            return stack[0]
        return stack
        
    __delattr__ = dict.__delitem__

    @classmethod
    def total_up_all(cls, print_total=False):
        output_str = ""
        for value in CalcDict.__instances:
            if isinstance(value, CalcDict):
                total_str = value.get_totals_output()
                output_str += total_str + "\n"
        if print_total:
            # print to stdout when requested
            try:
                builtins_print = __builtins__.get('print') if isinstance(__builtins__, dict) else __builtins__.print
            except Exception:
                builtins_print = print
            builtins_print(output_str, end='')
        return output_str


def clear_calcdict_instances():
    """Clear tracked CalcDict instances (helper used by web handlers)."""
    try:
        CalcDict.__instances.clear()
    except Exception:
        # Fallback: replace with empty list
        try:
            CalcDict._CalcDict__instances = []
        except Exception:
            pass



#atexit.register(CalcDict.print_all)



#c = CalcDict("example")
#
#c.assn("""
#              
#
#a 1 2 3 4 5 n+ 2 * 3 + 4 swap /
#b 3 5 -
#
#
#""")



