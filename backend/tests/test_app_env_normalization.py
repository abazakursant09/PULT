"""APP_ENV canonicalization/launch guards; no app/DB imports or dotenv reads."""
import json
from pathlib import Path
import subprocess
import sys
import unittest


BACKEND_DIR = Path(__file__).resolve().parents[1]
PRODUCTION_VARIANTS = ("production", "PRODUCTION", " Production ", "\tPrOdUcTiOn\n")
VALID_PRODUCTION = {
    "APP_ENV": "production",
    "SECRET_KEY": "synthetic-test-key-" * 4,
    "DATABASE_URL": "postgresql+asyncpg://synthetic:fake@invalid.example/pult",
    "CRED_ENC_KEY": "synthetic-encryption-key-placeholder",
    "SMTP_HOST": "smtp.invalid.example",
    "FRONTEND_URL": "https://app.invalid.example",
}

# Import the real config and its startup guards, but prohibit dotenv loading even
# when Settings() is constructed at module scope. Never inherit operator env.
IMPORT_CONFIG = r'''
import ast
import json
from pathlib import Path
import sys
from unittest.mock import patch
from pydantic import ValidationError
from pydantic_settings import BaseSettings

sys.path.insert(0, sys.argv[1])
original_init = BaseSettings.__init__
def isolated_init(self, *args, **kwargs):
    kwargs["_env_file"] = None
    original_init(self, *args, **kwargs)

with patch.object(BaseSettings, "__init__", isolated_init):
    import config
    action = json.loads(sys.argv[2])
    if "candidate" in action:
        try:
            value = config.Settings(app_env=action["candidate"]).app_env
        except ValidationError:
            print(json.dumps({"validation_error": True}))
        else:
            print(json.dumps({"value": value}))
    else:
        # Evaluate only the docs/CORS settings expressions, never import main
        # (which imports application services and database integrations).
        tree = ast.parse(Path(sys.argv[1], "main.py").read_text(encoding="utf-8-sig"))
        names = {"_expose_docs", "_DEV_ORIGINS", "_allowed_origins"}
        nodes = []
        for node in tree.body:
            targets = node.targets if isinstance(node, ast.Assign) else (
                [node.target] if isinstance(node, ast.AnnAssign) else [])
            if any(isinstance(t, ast.Name) and t.id in names for t in targets):
                nodes.append(node)
            elif (isinstance(node, ast.If)
                  and ast.unparse(node.test) ==
                  "settings.frontend_url and settings.frontend_url not in _allowed_origins"):
                nodes.append(node)
        scope = {"settings": config.settings}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "main-settings-only", "exec"), scope)
        print(json.dumps({"app_env": config.settings.app_env,
                          "non_dev": not config._is_dev_env,
                          "expose_docs": scope["_expose_docs"],
                          "origins": scope["_allowed_origins"]}))
'''


def import_config(overrides=None, *, action=None):
    env = {**VALID_PRODUCTION, **(overrides or {})}
    if sys.platform == "win32":
        env.update(SystemRoot="C:\\Windows", WINDIR="C:\\Windows")
    return subprocess.run(
        [sys.executable, "-I", "-B", "-c", IMPORT_CONFIG,
         str(BACKEND_DIR), json.dumps(action or {})],
        cwd=BACKEND_DIR, env=env, capture_output=True, text=True, timeout=20,
    )


class AppEnvNormalizationTests(unittest.TestCase):
    def assert_starts(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def assert_production_rejects(self, overrides, field):
        for app_env in PRODUCTION_VARIANTS:
            with self.subTest(app_env=app_env):
                result = import_config({**overrides, "APP_ENV": app_env})
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(field, result.stdout + result.stderr)

    def test_loaded_production_value_and_docs_cors_are_canonical(self):
        for app_env in PRODUCTION_VARIANTS:
            with self.subTest(app_env=app_env):
                data = self.assert_starts(import_config({"APP_ENV": app_env}))
                self.assertEqual(data["app_env"], "production")
                self.assertTrue(data["non_dev"])
                self.assertFalse(data["expose_docs"])
                self.assertEqual(data["origins"], [VALID_PRODUCTION["FRONTEND_URL"]])

    def test_weak_key_is_rejected(self):
        self.assert_production_rejects(
            {"SECRET_KEY": "dev-secret-key-change-in-production"}, "SECRET_KEY")

    def test_short_key_is_rejected(self):
        self.assert_production_rejects({"SECRET_KEY": "short"}, "SECRET_KEY")

    def test_sqlite_is_rejected(self):
        self.assert_production_rejects(
            {"DATABASE_URL": "sqlite+aiosqlite:///./never-opened.db"}, "DATABASE_URL")

    def test_missing_smtp_is_rejected(self):
        self.assert_production_rejects({"SMTP_HOST": ""}, "SMTP_HOST")

    def test_localhost_is_rejected(self):
        self.assert_production_rejects(
            {"FRONTEND_URL": "http://localhost:3000"}, "FRONTEND_URL")

    def test_loopback_is_rejected(self):
        self.assert_production_rejects(
            {"FRONTEND_URL": "http://127.0.0.1:3000"}, "FRONTEND_URL")

    def test_missing_encryption_key_is_rejected(self):
        self.assert_production_rejects({"CRED_ENC_KEY": ""}, "CRED_ENC_KEY")

    def test_staging_beta_and_unknown_do_not_inherit_production_requirements(self):
        for value in (" Staging ", " BETA ", " Prodution ", "  "):
            with self.subTest(app_env=value):
                data = self.assert_starts(import_config({
                    "APP_ENV": value, "DATABASE_URL": "sqlite+aiosqlite:///./never-opened.db",
                    "SMTP_HOST": "", "FRONTEND_URL": "http://localhost:3000"}))
                self.assertEqual(data["app_env"], value.strip().lower())
                self.assertTrue(data["non_dev"])

    def test_unknown_and_nonproduction_names_remain_nondev_fail_closed(self):
        for value in (" Staging ", " BETA ", " Prodution ", "  "):
            with self.subTest(app_env=value):
                result = import_config({"APP_ENV": value, "SECRET_KEY": "short"})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("SECRET_KEY", result.stdout + result.stderr)

    def test_explicit_development_names_remain_permissive(self):
        for value in (" Development ", " TEST ", " Local "):
            with self.subTest(app_env=value):
                data = self.assert_starts(import_config({
                    "APP_ENV": value, "SECRET_KEY": "short", "CRED_ENC_KEY": "",
                    "SMTP_HOST": "", "DATABASE_URL": "sqlite+aiosqlite:///./never-opened.db"}))
                self.assertEqual(data["app_env"], value.strip().lower())
                self.assertFalse(data["non_dev"])

    def test_nonstring_values_are_rejected_before_normalization(self):
        for value in (None, 123, False, ["production"]):
            with self.subTest(value=value):
                data = self.assert_starts(import_config(action={"candidate": value}))
                self.assertEqual(data, {"validation_error": True})


if __name__ == "__main__":
    unittest.main()
