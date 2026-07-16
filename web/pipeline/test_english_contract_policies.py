import json

from pipeline import views
from pipeline.test_support import IsolatedStorageSimpleTestCase, write_json_fixture


class EnglishContractPolicyTests(IsolatedStorageSimpleTestCase):
    def setUp(self):
        contract_payload = {
            "assisted_rewrite_policy": {
                "allowed_rewrite_percent": 20,
                "length_variation_percent": 10,
            },
            "system_prompt": (
                "Rewrite literary prose while preserving meaning. "
                "Allow up to 20 percent controlled rewriting and +/- 10 percent length variation."
            ),
            "user_prompt": "Rewrite the following passage.\n\n{text}",
        }
        contract_root = self.test_storage_root / "contracts"
        self.contract_paths = [
            write_json_fixture(contract_root / "en_modern_2025.json", contract_payload),
            write_json_fixture(contract_root / "en_philosofer_2026.json", contract_payload),
            write_json_fixture(contract_root / "en_devotional_2026.json", contract_payload),
            write_json_fixture(contract_root / "refine" / "en_refine_2025.json", contract_payload),
            write_json_fixture(contract_root / "polish" / "en_polish_2025.json", contract_payload),
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
        payload = json.loads(self.contract_paths[0].read_text(encoding="utf-8"))

        runtime_payload = views._harden_translate_contract(payload, "en")
        prompt_text = f"{runtime_payload['system_prompt']}\n{runtime_payload['user_prompt']}"

        self.assertIn("20 percent", prompt_text)
        self.assertIn("+/- 10 percent", prompt_text)

    def test_refine_runtime_contracts_are_disabled(self):
        with self.assertRaisesRegex(RuntimeError, "Refine via JSON contract is disabled"):
            views._build_runtime_refine_contract(object(), "en")
