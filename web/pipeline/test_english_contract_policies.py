import json
from pathlib import Path

from django.test import SimpleTestCase

from pipeline import views


REPO_ROOT = Path(__file__).resolve().parents[2]


class EnglishContractPolicyTests(SimpleTestCase):
    contract_paths = [
        REPO_ROOT / "gaiden" / "contracts" / "en_modern_2025.json",
        REPO_ROOT / "gaiden" / "contracts" / "en_philosofer_2026.json",
        REPO_ROOT / "gaiden" / "contracts" / "en_devotional_2026.json",
        REPO_ROOT / "gaiden" / "contracts" / "refine" / "en_refine_2025.json",
        REPO_ROOT / "gaiden" / "contracts" / "polish" / "en_polish_2025.json",
    ]

    def test_english_contracts_allow_controlled_twenty_percent_rewrite(self):
        for path in self.contract_paths:
            with self.subTest(contract=path.name):
                payload = json.loads(path.read_text(encoding="utf-8"))
                policy = payload.get("assisted_rewrite_policy")

                self.assertIsInstance(policy, dict)
                self.assertEqual(policy.get("allowed_rewrite_percent"), 20)
                self.assertEqual(policy.get("length_variation_percent"), 10)

    def test_runtime_translate_prompt_exposes_rewrite_and_length_policy(self):
        payload = json.loads(
            (REPO_ROOT / "gaiden" / "contracts" / "en_modern_2025.json").read_text(encoding="utf-8")
        )

        runtime_payload = views._harden_translate_contract(payload, "en")
        prompt_text = f"{runtime_payload['system_prompt']}\n{runtime_payload['user_prompt']}"

        self.assertIn("20 percent", prompt_text)
        self.assertIn("+/- 10 percent", prompt_text)

    def test_refine_runtime_contracts_are_disabled(self):
        with self.assertRaisesRegex(RuntimeError, "Refine via JSON contract is disabled"):
            views._build_runtime_refine_contract(object(), "en")
