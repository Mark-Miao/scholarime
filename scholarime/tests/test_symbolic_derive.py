from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "symbolic_derive.py"
SPEC = importlib.util.spec_from_file_location("symbolic_derive", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Could not load {SCRIPT}")
symbolic = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = symbolic
SPEC.loader.exec_module(symbolic)


class SymbolicSafetyTests(unittest.TestCase):
    def test_expression_rejects_import_and_attribute_access(self) -> None:
        with self.assertRaises(symbolic.SymbolicError):
            symbolic._validate_expression_syntax("__import__('os').system('bad')")
        with self.assertRaises(symbolic.SymbolicError):
            symbolic._validate_expression_syntax("x.real")
        with self.assertRaises(symbolic.SymbolicError):
            symbolic._validate_expression_syntax("open('/tmp/file')")

    def test_expression_accepts_sympy_arithmetic(self) -> None:
        symbolic._validate_expression_syntax("x**2 + sin(x) + sqrt(2)")

    def test_lean_rejects_sorry_and_unapproved_import(self) -> None:
        source = """import Mathlib
import Shell
theorem bad : True := by
  sorry
"""
        result = symbolic.inspect_formal_source(source, "lean")
        self.assertFalse(result["safe_to_compile"])
        self.assertTrue(any("sorry" in item for item in result["findings"]))
        self.assertTrue(any("Shell" in item for item in result["findings"]))

    def test_lean_accepts_basic_theorem(self) -> None:
        source = """theorem add_zero_example (n : Nat) : n + 0 = n :=
  Nat.add_zero n
"""
        result = symbolic.inspect_formal_source(source, "lean")
        self.assertTrue(result["safe_to_compile"], result["findings"])

    def test_coq_rejects_admitted(self) -> None:
        source = """Theorem bad : forall n : nat, n + 0 = n.
Proof.
  intros n.
  admit.
Admitted.
"""
        result = symbolic.inspect_formal_source(source, "coq")
        self.assertFalse(result["safe_to_compile"])
        self.assertGreaterEqual(sum("admit" in item.casefold() or "Admitted" in item for item in result["findings"]), 1)

    def test_comments_and_strings_do_not_trigger_false_positives(self) -> None:
        lean = """-- sorry is not code
/- admit is also not code -/
theorem safe : True := by
  trivial
def text : String := "sorry"
"""
        result = symbolic.inspect_formal_source(lean, "lean")
        self.assertTrue(result["safe_to_compile"], result["findings"])


class FormalVerificationTests(unittest.TestCase):
    def test_lean_compile_success_is_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "proof.lean"
            source.write_text(
                "theorem example (n : Nat) : n = n := by rfl\n",
                encoding="utf-8",
            )
            calls = []

            def runner(command, **kwargs):
                calls.append((command, kwargs))
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            result = symbolic.verify_formal_source(
                source,
                "lean",
                None,
                10,
                runner=runner,
                tool_paths={"lean": "/fake/lean"},
            )
            self.assertEqual(result["status"], "verified")
            self.assertEqual(result["returncode"], 0)
            self.assertEqual(len(calls), 1)

    def test_compiler_failure_is_not_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "proof.lean"
            source.write_text("theorem example : False := by trivial\n", encoding="utf-8")

            def runner(command, **kwargs):
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="type mismatch")

            result = symbolic.verify_formal_source(
                source,
                "lean",
                None,
                10,
                runner=runner,
                tool_paths={"lean": "/fake/lean"},
            )
            self.assertEqual(result["status"], "failed")
            self.assertIn("type mismatch", result["stderr"])

    def test_forbidden_source_is_rejected_without_running(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "proof.lean"
            source.write_text("theorem bad : True := by sorry\n", encoding="utf-8")
            called = False

            def runner(command, **kwargs):
                nonlocal called
                called = True
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            result = symbolic.verify_formal_source(
                source,
                "lean",
                None,
                10,
                runner=runner,
                tool_paths={"lean": "/fake/lean"},
            )
            self.assertEqual(result["status"], "rejected")
            self.assertFalse(called)

    def test_rocq_command_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "proof.v"
            source.write_text(
                "Theorem example : forall n : nat, n = n.\nProof. auto. Qed.\n",
                encoding="utf-8",
            )
            calls = []

            def runner(command, **kwargs):
                calls.append(command)
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with mock.patch.object(
                symbolic.shutil,
                "which",
                side_effect=lambda name: "/fake/rocq" if name == "rocq" else None,
            ):
                result = symbolic.verify_formal_source(
                    source,
                    "coq",
                    None,
                    10,
                    runner=runner,
                    tool_paths={"rocq": "/fake/rocq"},
                )
            self.assertEqual(result["status"], "verified")
            self.assertEqual(calls[0][:2], ["/fake/rocq", "compile"])


class SymbolicComputationTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("sympy"), "SymPy is not installed")
    def test_compute_trace_and_derivative(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            recipe = root / "derive.json"
            recipe.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "variables": {"x": {"real": True}},
                        "expression": "x**3 + x",
                        "operations": [
                            {"op": "differentiate", "variable": "x"},
                            {"op": "factor"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            result = symbolic.run_compute(recipe, timeout=20)
            self.assertEqual(result["engine"], "SymPy")
            self.assertIn("3", result["result"])
            self.assertEqual(len(result["steps"]), 3)

    def test_recipe_validation_does_not_need_sympy(self) -> None:
        symbolic.validate_compute_recipe(
            {
                "version": 1,
                "variables": [{"name": "x", "assumptions": ["real"]}],
                "expression": "x**2",
                "operations": [{"op": "expand"}],
            }
        )


if __name__ == "__main__":
    unittest.main()
