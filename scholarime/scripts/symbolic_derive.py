#!/usr/bin/env python3
"""Auditable symbolic computation and formal verification with Lean or Coq."""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


VERSION = "1.0.0"
MAX_RECIPE_BYTES = 2 * 1024 * 1024
MAX_EXPRESSION_CHARS = 5000
MAX_AST_NODES = 500
MAX_RESULT_BYTES = 2 * 1024 * 1024
MAX_TOOL_OUTPUT = 100_000
ALLOWED_ASSUMPTIONS = {
    "real",
    "complex",
    "integer",
    "rational",
    "irrational",
    "positive",
    "negative",
    "nonzero",
    "nonnegative",
    "nonpositive",
    "even",
    "odd",
    "prime",
}
ALLOWED_FUNCTIONS = {
    "sin",
    "cos",
    "tan",
    "asin",
    "acos",
    "atan",
    "sinh",
    "cosh",
    "tanh",
    "exp",
    "log",
    "sqrt",
    "Abs",
    "factorial",
    "gamma",
    "erf",
    "Min",
    "Max",
    "Rational",
}
ALLOWED_CONSTANTS = {"pi", "E", "I", "oo"}
ALLOWED_BINARY = (
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
)
ALLOWED_UNARY = (ast.UAdd, ast.USub)
SUPPORTED_COMPUTE_OPERATIONS = {
    "simplify",
    "expand",
    "factor",
    "cancel",
    "apart",
    "together",
    "trigsimp",
    "substitute",
    "differentiate",
    "integrate",
    "limit",
    "series",
    "solve",
    "evaluate",
    "check_equals",
}
PROVER_DETECTION = {
    ".lean": "lean",
    ".v": "coq",
}
LEAN_FORBIDDEN = {
    "sorry",
    "admit",
    "axiom",
    "unsafe",
    "partial",
    "implemented_by",
    "extern",
    "elab",
    "macro",
    "syntax",
    "run_cmd",
    "#eval",
    "#run",
    "set_option autoImplicit true",
}
COQ_FORBIDDEN = {
    "Admitted",
    "admit",
    "Axiom",
    "Parameter",
    "Conjecture",
    "Abort",
    "Declare ML Module",
    "Add LoadPath",
    "Unset Guard Checking",
    "Unset Positivity Checking",
    "Unset Universe Checking",
}


class SymbolicError(ValueError):
    """A user-correctable symbolic task error."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256(path.read_bytes())


def _validate_expression_syntax(expression: str) -> None:
    if not expression.strip():
        raise SymbolicError("Expression must not be empty.")
    if len(expression) > MAX_EXPRESSION_CHARS:
        raise SymbolicError(f"Expression exceeds {MAX_EXPRESSION_CHARS} characters.")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise SymbolicError(
            f"Expression must use Python/SymPy syntax such as x**2 + sin(x): {exc.msg}"
        ) from exc
    nodes = list(ast.walk(tree))
    if len(nodes) > MAX_AST_NODES:
        raise SymbolicError(f"Expression exceeds {MAX_AST_NODES} AST nodes.")
    for node in nodes:
        if isinstance(node, (ast.Expression, ast.Load, ast.operator, ast.unaryop)):
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float, complex)):
            continue
        if isinstance(node, ast.Name):
            if node.id.startswith("_"):
                raise SymbolicError("Private names are not allowed in expressions.")
            continue
        if isinstance(node, ast.BinOp) and isinstance(node.op, ALLOWED_BINARY):
            continue
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ALLOWED_UNARY):
            continue
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id not in ALLOWED_FUNCTIONS:
                raise SymbolicError(f"Function {node.func.id!r} is not allowed.")
            if node.keywords:
                raise SymbolicError("Keyword arguments are not allowed in expressions.")
            continue
        raise SymbolicError(
            f"Unsupported expression element: {node.__class__.__name__}."
        )


def _variable_specs(recipe: Mapping[str, Any]) -> list[tuple[str, dict[str, bool]]]:
    raw = recipe.get("variables", {})
    specs: list[tuple[str, dict[str, bool]]] = []
    if isinstance(raw, dict):
        for name, assumptions in raw.items():
            if isinstance(assumptions, dict):
                settings = {key: bool(value) for key, value in assumptions.items()}
            elif isinstance(assumptions, list):
                settings = {str(key): True for key in assumptions}
            elif assumptions is True:
                settings = {}
            else:
                raise SymbolicError(f"Invalid assumptions for variable {name!r}.")
            specs.append((str(name), settings))
    elif isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict) or not item.get("name"):
                raise SymbolicError("Variable entries must contain a name.")
            assumptions = item.get("assumptions", [])
            if isinstance(assumptions, dict):
                settings = {key: bool(value) for key, value in assumptions.items()}
            elif isinstance(assumptions, list):
                settings = {str(key): True for key in assumptions}
            else:
                raise SymbolicError("Variable assumptions must be a list or object.")
            specs.append((str(item["name"]), settings))
    else:
        raise SymbolicError("variables must be an object or list.")
    seen: set[str] = set()
    for name, settings in specs:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) or name.startswith("_"):
            raise SymbolicError(f"Invalid variable name: {name!r}")
        if name in seen:
            raise SymbolicError(f"Duplicate variable declaration: {name}")
        seen.add(name)
        unknown = set(settings) - ALLOWED_ASSUMPTIONS
        if unknown:
            raise SymbolicError(
                f"Unknown assumptions for {name}: {', '.join(sorted(unknown))}"
            )
    return specs


def validate_compute_recipe(recipe: Mapping[str, Any]) -> None:
    if recipe.get("version") != 1:
        raise SymbolicError("Compute recipe version must be 1.")
    if not isinstance(recipe.get("operations"), list):
        raise SymbolicError("operations must be a list.")
    for index, operation in enumerate(recipe["operations"]):
        if not isinstance(operation, dict):
            raise SymbolicError(f"Operation {index} must be an object.")
        name = str(operation.get("op", "")).casefold()
        if name not in SUPPORTED_COMPUTE_OPERATIONS:
            raise SymbolicError(f"Unsupported compute operation: {name or '(missing)'}")
    has_expression = isinstance(recipe.get("expression"), str)
    equation = recipe.get("equation")
    has_equation = (
        isinstance(equation, dict)
        and isinstance(equation.get("lhs"), str)
        and isinstance(equation.get("rhs"), str)
    )
    if not has_expression and not has_equation:
        raise SymbolicError("Recipe requires expression or equation {lhs, rhs}.")
    if has_expression:
        _validate_expression_syntax(str(recipe["expression"]))
    if has_equation:
        _validate_expression_syntax(str(equation["lhs"]))
        _validate_expression_syntax(str(equation["rhs"]))
    _variable_specs(recipe)


def _sympy_context(recipe: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    import sympy

    symbols: dict[str, Any] = {}
    for name, settings in _variable_specs(recipe):
        symbols[name] = sympy.Symbol(name, **settings)
    functions = {
        name: getattr(sympy, name)
        for name in ALLOWED_FUNCTIONS
        if hasattr(sympy, name)
    }
    constants = {
        "pi": sympy.pi,
        "E": sympy.E,
        "I": sympy.I,
        "oo": sympy.oo,
    }
    local: dict[str, Any] = {**symbols, **functions, **constants}
    global_dict = {
        "__builtins__": {},
        "Integer": sympy.Integer,
        "Float": sympy.Float,
        "Rational": sympy.Rational,
        **functions,
        **constants,
    }
    return local, global_dict


def _parse_expression(expression: str, recipe: Mapping[str, Any]) -> Any:
    from sympy.parsing.sympy_parser import parse_expr, standard_transformations

    _validate_expression_syntax(expression)
    local, global_dict = _sympy_context(recipe)
    try:
        parsed = parse_expr(
            expression,
            local_dict=local,
            global_dict=global_dict,
            transformations=standard_transformations,
            evaluate=True,
        )
    except Exception as exc:
        raise SymbolicError(f"Could not parse expression {expression!r}: {exc}") from exc
    import sympy

    allowed_symbols = {
        value for value in local.values() if isinstance(value, sympy.Symbol)
    }
    undeclared = parsed.free_symbols - allowed_symbols
    if undeclared:
        raise SymbolicError(
            "Undeclared variable(s): "
            + ", ".join(sorted(str(symbol) for symbol in undeclared))
        )
    return parsed


def _sympy_latex(value: Any) -> str:
    import sympy

    try:
        return sympy.latex(value)
    except Exception:
        return str(value)


def _equivalent(left: Any, right: Any) -> bool | None:
    import sympy

    try:
        difference = sympy.simplify(left - right)
        if difference == 0:
            return True
        if sympy.simplify(sympy.expand(difference)) == 0:
            return True
        return False
    except Exception:
        return None


def _compute_with_sympy(recipe: Mapping[str, Any]) -> dict[str, Any]:
    import sympy

    validate_compute_recipe(recipe)
    local, _ = _sympy_context(recipe)
    symbols = {
        name: value for name, value in local.items() if isinstance(value, sympy.Symbol)
    }
    equation = recipe.get("equation")
    if isinstance(equation, dict):
        left = _parse_expression(str(equation["lhs"]), recipe)
        right = _parse_expression(str(equation["rhs"]), recipe)
        current = sympy.Eq(left, right)
        work_value = left - right
    else:
        current = _parse_expression(str(recipe["expression"]), recipe)
        work_value = current

    steps: list[dict[str, Any]] = [
        {
            "index": 0,
            "op": "input",
            "expression": str(current),
            "latex": _sympy_latex(current),
            "assumptions": {
                name: {key: value for key, value in settings.items() if value}
                for name, settings in _variable_specs(recipe)
            },
            "warnings": [],
        }
    ]
    for index, operation in enumerate(recipe["operations"], 1):
        op_name = str(operation.get("op", "")).casefold()
        before = current
        warnings: list[str] = []
        equivalent: bool | None = None
        if op_name == "simplify":
            work_value = sympy.simplify(work_value)
        elif op_name == "expand":
            work_value = sympy.expand(work_value)
        elif op_name == "factor":
            work_value = sympy.factor(work_value)
        elif op_name == "cancel":
            work_value = sympy.cancel(work_value)
        elif op_name == "apart":
            variable = _get_symbol(operation, "variable", symbols)
            work_value = sympy.apart(work_value, variable)
        elif op_name == "together":
            work_value = sympy.together(work_value)
        elif op_name == "trigsimp":
            work_value = sympy.trigsimp(work_value)
        elif op_name == "substitute":
            replacements = operation.get("replacements")
            if not isinstance(replacements, dict):
                raise SymbolicError("substitute requires a replacements object.")
            parsed_replacements = {
                _symbol_for_name(name, symbols): _parse_expression(str(value), recipe)
                for name, value in replacements.items()
            }
            work_value = work_value.subs(parsed_replacements)
        elif op_name == "differentiate":
            variable = _get_symbol(operation, "variable", symbols)
            order = int(operation.get("order", 1))
            if order < 1:
                raise SymbolicError("differentiate order must be positive.")
            work_value = sympy.diff(work_value, variable, order)
        elif op_name == "integrate":
            variable = _get_symbol(operation, "variable", symbols)
            integrated = sympy.integrate(work_value, variable)
            derivative = sympy.simplify(sympy.diff(integrated, variable) - work_value)
            equivalent = derivative == 0
            warnings.append("Integration result is determined up to an additive constant.")
            if not equivalent:
                warnings.append(
                    "The tool could not verify the integral by differentiation; "
                    "treat the result as a candidate."
                )
            work_value = integrated
        elif op_name == "limit":
            variable = _get_symbol(operation, "variable", symbols)
            point = _parse_expression(str(operation.get("point", "0")), recipe)
            direction = str(operation.get("direction", "+-"))
            work_value = sympy.limit(work_value, variable, point, dir=direction)
            warnings.append(
                "A limit depends on the declared assumptions and the specified direction."
            )
        elif op_name == "series":
            variable = _get_symbol(operation, "variable", symbols)
            point = _parse_expression(str(operation.get("point", "0")), recipe)
            order = int(operation.get("order", 6))
            if order < 1:
                raise SymbolicError("series order must be positive.")
            work_value = sympy.series(work_value, variable, point, order)
        elif op_name == "solve":
            variable = _get_symbol(operation, "variable", symbols)
            solutions = sympy.solve(work_value, variable)
            work_value = sympy.FiniteSet(*solutions) if solutions else sympy.EmptySet
        elif op_name == "evaluate":
            digits = int(operation.get("digits", 15))
            if not 1 <= digits <= 1000:
                raise SymbolicError("evaluate digits must be between 1 and 1000.")
            work_value = sympy.N(work_value, digits)
        elif op_name == "check_equals":
            other = _parse_expression(str(operation.get("expression", "")), recipe)
            equivalent = _equivalent(work_value, other)
            if equivalent is not True:
                warnings.append("The expressions could not be verified as equal.")
            # The current expression is intentionally unchanged.
        else:
            raise SymbolicError(f"Unsupported compute operation: {op_name}")

        if isinstance(equation, dict) and op_name not in {"solve", "evaluate", "check_equals"}:
            current = sympy.Eq(sympy.simplify(work_value), 0)
        else:
            current = work_value
        if equivalent is None:
            if isinstance(before, sympy.Eq) and isinstance(current, sympy.Eq):
                equivalent = _equivalent(
                    before.lhs - before.rhs,
                    current.lhs - current.rhs,
                )
            elif op_name not in {"solve", "limit", "series", "evaluate"}:
                equivalent = _equivalent(before, current)
        if equivalent is False and op_name not in {"solve"}:
            warnings.append("Symbolic equivalence could not be established for this step.")
        steps.append(
            {
                "index": index,
                "op": op_name,
                "expression": str(current),
                "latex": _sympy_latex(current),
                "equivalent": equivalent,
                "warnings": warnings,
            }
        )
    return {
        "version": VERSION,
        "generated_at": _utc_now(),
        "engine": "SymPy",
        "engine_version": sympy.__version__,
        "result": str(current),
        "latex": _sympy_latex(current),
        "steps": steps,
    }


def _symbol_for_name(name: Any, symbols: Mapping[str, Any]) -> Any:
    key = str(name)
    if key not in symbols:
        raise SymbolicError(f"Unknown variable: {key}")
    return symbols[key]


def _get_symbol(
    operation: Mapping[str, Any],
    key: str,
    symbols: Mapping[str, Any],
) -> Any:
    if key not in operation:
        raise SymbolicError(f"Operation {operation.get('op')!r} requires {key}.")
    return _symbol_for_name(operation[key], symbols)


def _worker_main(input_path: Path, output_path: Path) -> int:
    try:
        recipe = json.loads(input_path.read_text(encoding="utf-8"))
        if not isinstance(recipe, dict):
            raise SymbolicError("Compute recipe must be a JSON object.")
        result = _compute_with_sympy(recipe)
        encoded = json.dumps(result, ensure_ascii=False).encode("utf-8")
        if len(encoded) > MAX_RESULT_BYTES:
            raise SymbolicError("Computed result exceeds the 2 MiB output limit.")
        output_path.write_bytes(encoded)
        return 0
    except Exception as exc:
        output_path.write_text(
            json.dumps({"error": str(exc)}, ensure_ascii=False),
            encoding="utf-8",
        )
        return 2


def run_compute(recipe_path: Path, timeout: int) -> dict[str, Any]:
    source = recipe_path.expanduser().resolve()
    if not source.is_file():
        raise SymbolicError(f"Compute recipe not found: {source}")
    if source.stat().st_size > MAX_RECIPE_BYTES:
        raise SymbolicError("Compute recipe exceeds 2 MiB.")
    if timeout <= 0:
        raise SymbolicError("timeout must be positive.")
    try:
        recipe = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SymbolicError(f"Invalid compute recipe JSON: {exc}") from exc
    if not isinstance(recipe, dict):
        raise SymbolicError("Compute recipe must be a JSON object.")
    validate_compute_recipe(recipe)
    with tempfile.TemporaryDirectory(prefix="scholarime-symbolic-") as temporary:
        temp = Path(temporary)
        worker_input = temp / "recipe.json"
        worker_output = temp / "result.json"
        worker_input.write_text(json.dumps(recipe, ensure_ascii=False), encoding="utf-8")
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "_worker_compute",
            str(worker_input),
            str(worker_output),
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise SymbolicError(
                f"Symbolic computation exceeded the {timeout}s timeout."
            ) from exc
        if not worker_output.is_file():
            detail = (completed.stderr or completed.stdout).strip()
            raise SymbolicError(f"Symbolic worker produced no result. {detail}".strip())
        payload = json.loads(worker_output.read_text(encoding="utf-8"))
        if completed.returncode != 0 or "error" in payload:
            raise SymbolicError(payload.get("error") or "Symbolic computation failed.")
        return payload


def _strip_comments_and_strings(text: str, language: str) -> str:
    result = list(text)
    index = 0
    block_depth = 0
    in_string = False
    escaped = False
    line_comment = False
    block_start, block_end = ("/-", "-/") if language == "lean" else ("(*", "*)")
    line_prefix = "--"
    while index < len(text):
        if line_comment:
            if text[index] == "\n":
                line_comment = False
            else:
                result[index] = " "
            index += 1
            continue
        if block_depth:
            if text.startswith(block_start, index):
                result[index : index + len(block_start)] = " " * len(block_start)
                block_depth += 1
                index += len(block_start)
                continue
            if text.startswith(block_end, index):
                result[index : index + len(block_end)] = " " * len(block_end)
                block_depth -= 1
                index += len(block_end)
                continue
            if text[index] != "\n":
                result[index] = " "
            index += 1
            continue
        if in_string:
            if escaped:
                escaped = False
            elif text[index] == "\\":
                escaped = True
            elif text[index] == '"':
                in_string = False
            if text[index] != "\n":
                result[index] = " "
            index += 1
            continue
        if text.startswith(block_start, index):
            result[index : index + len(block_start)] = " " * len(block_start)
            block_depth = 1
            index += len(block_start)
            continue
        if language == "lean" and text.startswith(line_prefix, index):
            result[index : index + len(line_prefix)] = " " * len(line_prefix)
            line_comment = True
            index += len(line_prefix)
            continue
        if text[index] == '"':
            result[index] = " "
            in_string = True
            index += 1
            continue
        index += 1
    if block_depth or in_string:
        raise SymbolicError("Unterminated comment or string in formal source.")
    return "".join(result)


def inspect_formal_source(source: str, prover: str) -> dict[str, Any]:
    if prover not in {"lean", "coq"}:
        raise SymbolicError("prover must be lean or coq.")
    if len(source) > 2 * 1024 * 1024:
        raise SymbolicError("Formal source exceeds 2 MiB.")
    code = _strip_comments_and_strings(source, prover)
    findings: list[str] = []
    if prover == "lean":
        for token in sorted(LEAN_FORBIDDEN):
            if token.startswith("#"):
                matched = re.search(re.escape(token) + r"\b", code)
            else:
                matched = re.search(rf"\b{re.escape(token)}\b", code)
            if matched:
                findings.append(f"forbidden token: {token}")
        imports = re.findall(r"(?m)^\s*import\s+([A-Za-z0-9_.]+)", code)
        allowed_roots = {"Mathlib", "Init", "Std", "Lean", "Batteries"}
        for imported in imports:
            if imported.split(".", 1)[0] not in allowed_roots:
                findings.append(f"unapproved import root: {imported}")
        if not re.search(r"(?m)^\s*(?:theorem|lemma)\s+[A-Za-z_][A-Za-z0-9_']*", code):
            findings.append("no theorem or lemma declaration found")
    else:
        for token in sorted(COQ_FORBIDDEN):
            if re.search(rf"\b{re.escape(token)}\b", code, re.IGNORECASE):
                findings.append(f"forbidden token: {token}")
        if not re.search(
            r"(?m)^\s*(?:Theorem|Lemma|Proposition|Corollary)\s+[A-Za-z_][A-Za-z0-9_']*",
            code,
        ):
            findings.append("no theorem or lemma declaration found")
    return {
        "prover": prover,
        "findings": findings,
        "safe_to_compile": not findings,
        "imports": imports if prover == "lean" else [],
    }


def _tool_version(command: Sequence[str]) -> str:
    try:
        completed = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    text = (completed.stdout or completed.stderr).strip()
    return text.splitlines()[0][:300] if text else ""


def verify_formal_source(
    source_path: Path,
    prover: str,
    project: Path | None,
    timeout: int,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    tool_paths: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    source_path = source_path.expanduser().resolve()
    if not source_path.is_file():
        raise SymbolicError(f"Formal source not found: {source_path}")
    source = source_path.read_text(encoding="utf-8")
    inspection = inspect_formal_source(source, prover)
    result: dict[str, Any] = {
        "version": VERSION,
        "generated_at": _utc_now(),
        "prover": prover,
        "source": str(source_path),
        "source_sha256": _sha256_file(source_path),
        "project": str(project.resolve()) if project else None,
        "prover_version": "",
        "inspection": inspection,
        "command": [],
        "returncode": None,
        "stdout": "",
        "stderr": "",
        "status": "rejected" if not inspection["safe_to_compile"] else "not_run",
    }
    if not inspection["safe_to_compile"]:
        return result
    paths = dict(tool_paths or {})
    if prover == "lean":
        lean = paths.get("lean") or shutil.which("lean")
        lake = paths.get("lake") or shutil.which("lake")
        if project and lake:
            command = [lake, "env", "lean", str(source_path)]
            cwd = project.resolve()
        elif lean:
            command = [lean, str(source_path)]
            cwd = project.resolve() if project else source_path.parent
        else:
            result["status"] = "unavailable"
            result["stderr"] = "Lean executable not found."
            return result
        result["prover_version"] = _tool_version(
            [lean, "--version"] if lean else [lake, "--version"]
        )
    else:
        coqc = paths.get("coqc") or shutil.which("coqc")
        rocq = paths.get("rocq") or shutil.which("rocq")
        if not coqc and not rocq:
            result["status"] = "unavailable"
            result["stderr"] = "Neither coqc nor rocq was found."
            return result
        if coqc:
            command = [coqc, str(source_path)]
            version_command = [coqc, "--version"]
        else:
            command = [rocq, "compile", str(source_path)]
            version_command = [rocq, "--version"]
        cwd = project.resolve() if project else source_path.parent
        result["prover_version"] = _tool_version(version_command)
    result["command"] = command
    try:
        completed = runner(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        result["status"] = "timeout"
        result["stderr"] = f"Verification exceeded the {timeout}s timeout."
        return result
    except OSError as exc:
        result["status"] = "error"
        result["stderr"] = str(exc)
        return result
    result["returncode"] = completed.returncode
    result["stdout"] = (completed.stdout or "")[:MAX_TOOL_OUTPUT]
    result["stderr"] = (completed.stderr or "")[:MAX_TOOL_OUTPUT]
    result["status"] = "verified" if completed.returncode == 0 else "failed"
    return result


def compute_markdown(result: Mapping[str, Any]) -> str:
    lines = [
        "# Symbolic Derivation",
        "",
        f"- Engine: {result['engine']} {result['engine_version']}",
        f"- Generated: {result['generated_at']}",
        "",
    ]
    for step in result["steps"]:
        lines.append(f"## Step {step['index']}: {step['op']}")
        lines.append("")
        lines.append(f"$$ {step['latex']} $$")
        lines.append("")
        for warning in step.get("warnings", []):
            lines.append(f"> Warning: {warning}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def verification_markdown(result: Mapping[str, Any]) -> str:
    lines = [
        "# Formal Verification",
        "",
        f"- Prover: {result['prover']}",
        f"- Source: `{result['source']}`",
        f"- Source SHA-256: `{result['source_sha256']}`",
        f"- Project: `{result['project'] or 'standalone'}`",
        f"- Status: **{result['status']}**",
        f"- Command: `{' '.join(result['command'])}`",
        "",
    ]
    findings = result["inspection"]["findings"]
    if findings:
        lines.extend(["## Findings", ""])
        lines.extend(f"- {finding}" for finding in findings)
        lines.append("")
    if result["stderr"]:
        lines.extend(["## Standard Error", "", "```text", result["stderr"], "```", ""])
    if result["stdout"]:
        lines.extend(["## Standard Output", "", "```text", result["stdout"], "```", ""])
    return "\n".join(lines).rstrip() + "\n"


def _ensure_output(paths: Sequence[Path], overwrite: bool, protected: Sequence[Path]) -> None:
    protected_resolved = {path.expanduser().resolve() for path in protected}
    seen: set[Path] = set()
    for path in paths:
        resolved = path.expanduser().resolve()
        if resolved in protected_resolved:
            raise SymbolicError(f"Output must not overwrite input: {resolved}")
        if resolved in seen:
            raise SymbolicError(f"Output paths must be distinct: {resolved}")
        seen.add(resolved)
        if resolved.exists() and not overwrite:
            raise SymbolicError(f"Output already exists: {resolved}. Pass --overwrite.")


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _default_compute_paths(recipe: Path) -> tuple[Path, Path]:
    return (
        recipe.with_name(recipe.stem + ".result.json"),
        recipe.with_name(recipe.stem + ".trace.md"),
    )


def _default_verify_paths(source: Path) -> tuple[Path, Path]:
    return (
        source.with_name(source.stem + ".verify.json"),
        source.with_name(source.stem + ".verify.md"),
    )


def doctor() -> dict[str, Any]:
    sympy_available = importlib.util.find_spec("sympy") is not None
    sympy_version = ""
    if sympy_available:
        try:
            import sympy

            sympy_version = sympy.__version__
        except Exception:
            sympy_available = False
    tools = {}
    for name in ("lean", "lake", "coqc", "rocq", "elan", "opam"):
        path = shutil.which(name)
        tools[name] = {
            "available": bool(path),
            "path": path or "",
            "version": _tool_version([path, "--version"]) if path else "",
        }
    return {
        "version": VERSION,
        "sympy": {"available": sympy_available, "version": sympy_version},
        "tools": tools,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="symbolic_derive.py",
        description="Run auditable SymPy derivations and verify Lean/Coq proof files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python symbolic_derive.py compute derivation.json\n"
            "  python symbolic_derive.py verify proof.lean --project proof-project\n"
            "  python symbolic_derive.py verify proof.v --prover coq\n"
            "  python symbolic_derive.py doctor\n\n"
            "A successful CAS simplification is not a formal proof. A proof is marked verified\n"
            "only when the selected prover compiles the source without forbidden axioms or admits."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    compute_parser = subparsers.add_parser("compute", help="Run a SymPy computation recipe.")
    compute_parser.add_argument("recipe", type=Path)
    compute_parser.add_argument("--timeout", type=int, default=30)
    compute_parser.add_argument("--output", type=Path, help="Result JSON output.")
    compute_parser.add_argument("--markdown", type=Path, help="LaTeX trace Markdown output.")
    compute_parser.add_argument("--overwrite", action="store_true")

    verify_parser = subparsers.add_parser("verify", help="Compile a Lean or Coq proof source.")
    verify_parser.add_argument("source", type=Path)
    verify_parser.add_argument("--prover", choices=("auto", "lean", "coq"), default="auto")
    verify_parser.add_argument("--project", type=Path, help="Lean Lake or Coq project directory.")
    verify_parser.add_argument("--timeout", type=int, default=120)
    verify_parser.add_argument("--report-json", type=Path)
    verify_parser.add_argument("--report-md", type=Path)
    verify_parser.add_argument("--overwrite", action="store_true")

    subparsers.add_parser("doctor", help="Report installed symbolic and proof engines.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "_worker_compute":
        if len(arguments) != 3:
            return 2
        return _worker_main(Path(arguments[1]), Path(arguments[2]))
    args = build_parser().parse_args(arguments)
    try:
        if args.command == "doctor":
            payload = doctor()
            print(json.dumps(payload, indent=2))
            available = payload["sympy"]["available"] or any(
                item["available"] for item in payload["tools"].values()
            )
            return 0 if available else 1
        if args.command == "compute":
            recipe_path = args.recipe.expanduser().resolve()
            default_json, default_md = _default_compute_paths(recipe_path)
            output_json = (args.output or default_json).expanduser().resolve()
            output_md = (args.markdown or default_md).expanduser().resolve()
            _ensure_output(
                [output_json, output_md],
                overwrite=args.overwrite,
                protected=[recipe_path],
            )
            result = run_compute(recipe_path, args.timeout)
            _write_json(output_json, result)
            _write_text(output_md, compute_markdown(result))
            print(f"Compute result: {output_json}")
            print(f"Trace: {output_md}")
            print(f"Final expression: {result['result']}")
            return 0

        source = args.source.expanduser().resolve()
        prover = args.prover
        if prover == "auto":
            prover = PROVER_DETECTION.get(source.suffix.casefold(), "")
            if not prover:
                raise SymbolicError(
                    "Cannot infer prover from suffix; use --prover lean or --prover coq."
                )
        default_json, default_md = _default_verify_paths(source)
        output_json = (args.report_json or default_json).expanduser().resolve()
        output_md = (args.report_md or default_md).expanduser().resolve()
        _ensure_output(
            [output_json, output_md],
            overwrite=args.overwrite,
            protected=[source],
        )
        result = verify_formal_source(
            source,
            prover,
            args.project,
            args.timeout,
        )
        _write_json(output_json, result)
        _write_text(output_md, verification_markdown(result))
        print(f"Verification report: {output_json}")
        print(f"Status: {result['status']}")
        return 0 if result["status"] == "verified" else 1
    except (SymbolicError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
