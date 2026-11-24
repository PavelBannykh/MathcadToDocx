"""Render Mathcad math30 expression trees without evaluating expressions.

Numeric results are the values actually stored in ``result``. Their full decimal
precision is retained; worksheet display rounding is deliberately not simulated.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET


MATH_NS = "http://schemas.mathsoft.com/math30"


def _tag(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _escape(text: str) -> str:
    """Escape literal text rather than allowing XML values to inject TeX."""
    replacements = {
        "\\": r"\backslash{}", "{": r"\{", "}": r"\}",
        "_": r"\_", "^": r"\text{\textasciicircum{}}", "&": r"\&",
        "%": r"\%", "#": r"\#", "$": r"\$", "~": r"\text{\textasciitilde{}}",
    }
    return "".join(replacements.get(char, char) for char in text)


_GREEK = dict(zip(
    "α β γ δ ε ζ η θ ι κ λ μ ν ξ π ρ σ τ υ φ χ ψ ω Γ Δ Θ Λ Ξ Π Σ Υ Φ Ψ Ω ϕ ϑ ϵ ς".split(),
    (r"\alpha \beta \gamma \delta \epsilon \zeta \eta \theta \iota \kappa "
     r"\lambda \mu \nu \xi \pi \rho \sigma \tau \upsilon \varphi \chi \psi "
     r"\omega \Gamma \Delta \Theta \Lambda \Xi \Pi \Sigma \Upsilon \Phi \Psi "
     r"\Omega \phi \vartheta \varepsilon \varsigma").split(),
))
_FUNCTIONS = {
    "sin", "cos", "tan", "cot", "sinh", "cosh", "tanh", "arcsin",
    "arccos", "arctan", "exp", "ln", "log", "min", "max", "det", "arg",
}


class MathRenderer:
    """Stateful renderer; warnings accumulate until the caller clears them.

    ``imaginary_symbol`` may be set from worksheet/region result-format settings.
    No file access, numeric calculation, or external TeX execution takes place.
    """

    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.imaginary_symbol = "i"

    def render(self, element: ET.Element) -> str:
        """Return LaTeX (without display delimiters) for a math container/node."""
        return self._render(element)

    def _warn(self, message: str) -> None:
        self.warnings.append(message)

    def _placeholder(self, label: str) -> str:
        return r"\text{" + _escape("[" + label + "]") + "}"

    def _unknown(self, element: ET.Element, label: str | None = None) -> str:
        name = _tag(element)
        self._warn(label or f"Unsupported Mathcad construct: {name}")
        visible = [self._render(child) for child in element]
        if (element.text or "").strip():
            visible.insert(0, r"\text{" + _escape(element.text.strip()) + "}")
        marker = self._placeholder(label or "unsupported: " + name)
        return marker + (r"\left(" + ", ".join(visible) + r"\right)" if visible else "")

    def _identifier(self, text: str) -> str:
        if text in _GREEK:
            return _GREEK[text]
        if text == "∞":
            return r"\infty"
        if len(text) == 1 and text.isascii() and text.isalnum():
            return text
        if text in _FUNCTIONS:
            return "\\" + text
        # An underscore inside id text is literal. Only the subscript attribute
        # represents a Mathcad literal subscript; conflating them changes names.
        return r"\mathrm{" + _escape(text) + "}"

    def _number(self, text: str) -> str:
        text = text.strip()
        match = re.fullmatch(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))[eE]([+-]?\d+)", text)
        if match:
            return match[1] + r"\times 10^{" + str(int(match[2])) + "}"
        if re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", text):
            return text
        if text.lower() in {"inf", "+inf", "infinity", "+infinity"}:
            return r"\infty"
        if text.lower() in {"-inf", "-infinity"}:
            return r"-\infty"
        if text.lower() == "nan":
            return r"\operatorname{NaN}"
        self._warn(f"Unrecognized numeric literal: {text}")
        return r"\text{" + _escape(text or "[empty number]") + "}"

    def _children(self, element: ET.Element, separator: str = ", ") -> str:
        return separator.join(self._render(child) for child in element)

    def _precedence(self, element: ET.Element) -> int:
        name = _tag(element)
        if name in {"define", "localDefine", "globalDefine", "eval", "symEval"}:
            return 1
        if name == "complex" and len(element) > 1:
            return 10
        if name in {"real", "imag"} and (element.text or "").strip().startswith("-"):
            return 25
        if name == "apply" and len(element):
            return {"plus": 10, "minus": 10, "mult": 20, "div": 20,
                    "neg": 25, "pow": 30}.get(_tag(element[0]), 100)
        return 100

    def _group(self, element: ET.Element, minimum: int) -> str:
        value = self._render(element)
        if self._precedence(element) < minimum:
            return r"\left(" + value + r"\right)"
        return value

    def _render(self, element: ET.Element) -> str:
        name = _tag(element)
        children = list(element)
        if name == "math":
            # resultFormat is worksheet metadata, not a mathematical operand.
            return r"\quad ".join(self._render(child) for child in children
                                    if _tag(child) not in {"resultFormat", "rendering"})
        if name == "provenance":
            return r"\quad ".join(self._render(child) for child in children
                                    if child.tag.startswith("{" + MATH_NS + "}"))
        if name == "id":
            value = self._identifier(element.text or "")
            if "subscript" in element.attrib:
                sub = element.attrib["subscript"]
                # A literal subscript is a label, not an arithmetic expression.
                value += "_{" + (sub if sub.isdigit() else r"\mathrm{" + _escape(sub) + "}") + "}"
            return value
        if name in {"real", "integer"}:
            return self._number(element.text or "")
        if name == "imag":
            coefficient = (element.text or "").strip()
            symbol = r"\mathrm{" + _escape(element.get("symbol", self.imaginary_symbol)) + "}"
            if coefficient == "1":
                return symbol
            if coefficient == "-1":
                return "-" + symbol
            return self._number(coefficient) + r"\," + symbol
        if name == "complex":
            parts: list[str] = []
            for child in children:
                value = self._render(child)
                parts.append((" - " + value[1:] if value.startswith("-") else " + " + value)
                             if parts else value)
            return "".join(parts) or self._placeholder("empty complex")
        if name in {"str", "string"}:
            return r'\text{“' + _escape(element.text or "") + '”}'
        if name in {"define", "globalDefine", "localDefine"}:
            if len(children) != 2:
                return self._unknown(element, "malformed " + name)
            operator = {"define": r"\coloneqq", "globalDefine": r"\equiv",
                        "localDefine": r"\leftarrow"}[name]
            return self._render(children[0]) + " " + operator + " " + self._render(children[1])
        if name in {"eval", "symEval"}:
            return self._evaluation(element)
        if name in {"result", "symResult"}:
            if not children:
                message = "saved result unavailable" if not (element.text or "").strip() else "encoded result unsupported"
                return self._unknown(element, message)
            return self._children(element, r"\quad ")
        if name == "apply":
            return self._apply(element)
        if name == "parens":
            return r"\left(" + self._children(element) + r"\right)"
        if name in {"sequence", "list", "boundVars", "args"}:
            return self._children(element)
        if name == "matrix":
            return self._matrix(element)
        if name in {"unitOverride", "unitedValue", "unitMonomial", "unitProduct"}:
            return self._children(element, r"\,")
        if name == "unitReference":
            unit = element.get("unit", element.text or "")
            symbols = {"degree": r"{}^\circ", "radian": r"\mathrm{rad}",
                       "meter": r"\mathrm{m}", "metre": r"\mathrm{m}",
                       "second": r"\mathrm{s}", "kilogram": r"\mathrm{kg}",
                       "ampere": r"\mathrm{A}", "volt": r"\mathrm{V}",
                       "ohm": r"\Omega", "hertz": r"\mathrm{Hz}"}
            value = symbols.get(unit, r"\mathrm{" + _escape(unit) + "}")
            exponent = element.get("power", element.get("exponent"))
            return value + ("^{" + self._number(exponent) + "}" if exponent else "")
        if name == "lambda":
            if len(children) == 2 and _tag(children[0]) == "boundVars":
                return self._render(children[0]) + r"\mapsto " + self._render(children[1])
            return self._unknown(element, "unsupported lambda layout")
        if name == "range":
            return r",\ldots,".join(self._render(child) for child in children)
        if name in {"program", "block"}:
            return r"\begin{array}{l}" + self._children(element, r" \\ ") + r"\end{array}"
        if name in {"return", "break", "continue", "otherwise"}:
            return r"\mathbf{" + name + "}" + (r"\;" + self._children(element) if children else "")
        if name == "if" and len(children) in {2, 3}:
            rows = self._render(children[1]) + r" & \text{if } " + self._render(children[0])
            if len(children) == 3:
                rows += r" \\ " + self._render(children[2]) + r" & \text{otherwise}"
            return r"\begin{cases}" + rows + r"\end{cases}"
        if name == "placeholder":
            self._warn("Mathcad expression contains an empty placeholder")
            return r"\square"
        return self._unknown(element)

    def _evaluation(self, element: ET.Element) -> str:
        symbolic = _tag(element) == "symEval"
        result_name = "symResult" if symbolic else "result"
        expressions = [child for child in element if _tag(child) not in {result_name, "command", "unitOverride"}]
        result = next((child for child in element if _tag(child) == result_name), None)
        lhs = r"\quad ".join(self._render(child) for child in expressions)
        operator = "="
        if symbolic:
            operator = "=" if element.get("style") == "equals" else r"\longrightarrow"
            commands = [child for child in element if _tag(child) == "command"]
            if commands and element.get("hide-keywords") != "true":
                operator = r"\xrightarrow{" + "; ".join(self._children(command) for command in commands) + "}"
            if element.get("hide-lhs") == "true":
                lhs = ""
        if result is None:
            self._warn(f"{_tag(element)} has no saved result; expression was not recalculated")
            rhs = self._placeholder("saved result unavailable")
            override = next((child for child in element if _tag(child) == "unitOverride"), None)
            if override is not None:
                rhs += r"\," + self._render(override)
        else:
            rhs = self._render(result)
            override = next((child for child in element if _tag(child) == "unitOverride"), None)
            # unitedValue already carries the saved result's units.
            if override is not None and not any(_tag(child) == "unitedValue" for child in result):
                rhs += r"\," + self._render(override)
        return (lhs + " " + operator + " " if lhs else "") + rhs

    def _matrix(self, element: ET.Element) -> str:
        try:
            rows, cols = int(element.get("rows", "0")), int(element.get("cols", "0"))
        except ValueError:
            return self._unknown(element, "invalid matrix dimensions")
        values = [self._render(child) for child in element]
        if rows <= 0 or cols <= 0 or rows * cols != len(values):
            self._warn(f"Matrix dimensions {rows}x{cols} do not match {len(values)} stored entries")
            return self._placeholder(f"matrix {rows}x{cols}: invalid entries") + r"\left(" + ", ".join(values) + r"\right)"
        # math30 stores columns consecutively. Verified against the worksheet's
        # saved PNG of a nonsymmetric 3x3 transformation matrix.
        body = r" \\ ".join(" & ".join(values[col * rows + row] for col in range(cols))
                             for row in range(rows))
        return r"\begin{bmatrix}" + body + r"\end{bmatrix}"

    def _apply(self, element: ET.Element) -> str:
        if not len(element):
            return self._unknown(element, "empty apply")
        op, *args = list(element)
        name = _tag(op)
        values = lambda: [self._render(arg) for arg in args]
        if name == "id":
            function = self._render(op)
            if (op.text or "") not in _FUNCTIONS and "subscript" not in op.attrib:
                function = self._identifier(op.text or "")
            return function + r"\left(" + ", ".join(values()) + r"\right)"
        if name in {"plus", "mult", "minus"} and args:
            if name == "minus" and len(args) == 1:
                return "-" + self._group(args[0], 25)
            separator = {"plus": " + ", "minus": " - ", "mult": r" \cdot "}[name]
            minimum = 20 if name == "mult" else 10
            return separator.join(self._group(arg, minimum + (1 if name == "minus" and index else 0))
                                  for index, arg in enumerate(args))
        if name == "neg" and len(args) == 1:
            return "-" + self._group(args[0], 26)
        if name == "div" and len(args) == 2:
            numerator, denominator = values()
            return r"\frac{" + numerator + "}{" + denominator + "}"
        if name == "pow" and len(args) == 2:
            return "{" + self._group(args[0], 31) + "}^{" + self._render(args[1]) + "}"
        if name == "sqrt" and len(args) == 1:
            return r"\sqrt{" + self._render(args[0]) + "}"
        if name in {"nthRoot", "root"} and len(args) == 2:
            return r"\sqrt[" + self._render(args[1]) + "]{" + self._render(args[0]) + "}"
        if name in {"absval", "determinant", "norm"} and len(args) == 1:
            bracket = r"\|" if name == "norm" else "|"
            return r"\left" + bracket + self._render(args[0]) + r"\right" + bracket
        if name in {"indexer", "subscript"} and len(args) >= 2:
            indices = ", ".join(self._render(arg) for arg in args[1:])
            return "{" + self._group(args[0], 100) + "}_{" + indices + "}"
        if name in {"transpose", "conjugate", "factorial"} and len(args) == 1:
            value = self._render(args[0])
            if name == "conjugate":
                return r"\overline{" + value + "}"
            return "{" + self._group(args[0], 100) + "}" + ("^{T}" if name == "transpose" else "!")
        comparisons = {"equal": "=", "notEqual": r"\ne", "lessThan": "<", "greaterThan": ">",
                       "lessOrEqual": r"\le", "greaterOrEqual": r"\ge", "and": r"\land", "or": r"\lor"}
        if name in comparisons and len(args) >= 2:
            return (" " + comparisons[name] + " ").join(values())
        if name == "not" and len(args) == 1:
            return r"\neg " + self._group(args[0], 100)
        if name in {"sum", "summation", "product", "integral", "int"}:
            # A lambda with one bound variable and two explicit bounds is an
            # unambiguous layout. Others remain visible with a warning.
            if len(args) == 3 and _tag(args[0]) == "lambda" and len(args[0]) == 2:
                bound, body = args[0]
                if _tag(bound) == "boundVars" and len(bound) == 1:
                    variable = self._render(bound[0])
                    low, high = self._render(args[1]), self._render(args[2])
                    integral = name in {"integral", "int"}
                    symbol = r"\int" if integral else (r"\prod" if name == "product" else r"\sum")
                    lower = low if integral else variable + "=" + low
                    return symbol + "_{" + lower + "}^{" + high + "} " + self._render(body) + (r"\,\mathrm{d}" + variable if integral else "")
            return self._unknown(element, "unsupported " + name + " layout")
        # Preserve operands of unknown operators, including the operator's name.
        self._warn(f"Unsupported Mathcad operator: {name}")
        return self._placeholder("unsupported operator: " + name) + r"\left(" + ", ".join(values()) + r"\right)"
