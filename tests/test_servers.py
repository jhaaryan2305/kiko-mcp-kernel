"""Every MCP server must expose exactly the tools the policy assigns to it."""
import inspect
import unittest

from ._support import ROOT, load_policy_doc, load_server

SERVERS = sorted(p.name for p in (ROOT / "mcp_servers").iterdir() if (p / "server.py").exists())


class ServerContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_policy_doc()["tools"]

    def test_all_policy_servers_exist(self) -> None:
        self.assertEqual(sorted({t["server"] for t in self.policy.values()}), SERVERS)

    def test_server_tools_match_policy(self) -> None:
        for dirname in SERVERS:
            with self.subTest(server=dirname):
                mod = load_server(dirname)
                self.assertEqual(mod.SERVER_NAME, dirname)
                expected = {n for n, t in self.policy.items() if t["server"] == dirname}
                self.assertEqual(set(mod.TOOLS), expected)

    def test_inverses_exist_exactly_for_tier_1(self) -> None:
        for dirname in SERVERS:
            with self.subTest(server=dirname):
                mod = load_server(dirname)
                tier1 = {n for n, t in self.policy.items() if t["server"] == dirname and t["tier"] == "tier_1_reversible"}
                self.assertEqual(set(mod.INVERSES), tier1)

    def test_tools_are_documented_and_typed(self) -> None:
        for dirname in SERVERS:
            mod = load_server(dirname)
            for name, fn in {**mod.TOOLS, **mod.INVERSES}.items():
                with self.subTest(tool=name):
                    self.assertTrue(inspect.getdoc(fn), "missing docstring")
                    sig = inspect.signature(fn)
                    for param in sig.parameters.values():
                        if param.kind is param.VAR_KEYWORD:
                            continue
                        self.assertIsNot(param.annotation, param.empty, "{} untyped".format(param.name))


if __name__ == "__main__":
    unittest.main()
