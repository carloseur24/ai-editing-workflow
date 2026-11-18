from __future__ import annotations

import importlib
import unittest

from fenixsoft import cli


class CliContractTest(unittest.TestCase):
    def test_help_lists_every_command(self):
        text = cli.usage()
        for command in cli.ORDER:
            self.assertIn(command, text)

    def test_every_command_target_imports(self):
        for command, (module_name, _) in cli.COMMANDS.items():
            if command == "test":
                continue
            with self.subTest(command=command):
                module = importlib.import_module(module_name)
                self.assertTrue(callable(module.main))

    def test_unknown_command_returns_2(self):
        self.assertEqual(cli.main(["nope"]), 2)

    def test_version(self):
        self.assertEqual(cli.main(["--version"]), 0)


if __name__ == "__main__":
    unittest.main()
