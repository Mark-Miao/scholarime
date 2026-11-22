from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOL_DIR = ROOT / "scholarime" / "references" / "less-ai-tone"


class LessAiToneToolTests(unittest.TestCase):
    def test_required_prompt_resources_exist(self) -> None:
        for filename in (
            "TOOL.md",
            "UPSTREAM-SKILL.md",
            "DETECT.md",
            "references/semantic-criteria.md",
            "RESEARCH.md",
            "LICENSE.txt",
        ):
            self.assertTrue((TOOL_DIR / filename).is_file(), filename)
        self.assertTrue(
            (TOOL_DIR / "examples" / "agent-memory-case" / "README.md").is_file()
        )
        self.assertFalse((TOOL_DIR / "scripts").exists())

    def test_tool_returns_processed_text_only(self) -> None:
        text = (TOOL_DIR / "TOOL.md").read_text(encoding="utf-8")
        self.assertIn("prompt-only", text)
        self.assertIn("Return only the complete processed text", text)
        self.assertIn("Do not call", text)

    def test_main_skill_routes_de_ai_requests(self) -> None:
        skill = (ROOT / "scholarime" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("## De-AI Tone Tool", skill)
        self.assertIn("references/less-ai-tone/TOOL.md", skill)
        self.assertIn("降低 AI 率", skill)


if __name__ == "__main__":
    unittest.main()
