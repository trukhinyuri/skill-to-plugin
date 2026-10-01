import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class MCPTests(unittest.TestCase):
    def exchange(self, messages):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/stp.py"), "mcp"],
                                input="\n".join(json.dumps(m) if isinstance(m, dict) else m for m in messages) + "\n",
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return [json.loads(line) for line in result.stdout.splitlines()]

    def test_lifecycle_discovery_and_real_tool_call(self):
        responses = self.exchange([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "validate_plugin", "arguments": {"plugin": str(ROOT)}}},
        ])
        self.assertEqual(len(responses), 3)
        self.assertEqual(len(responses[1]["result"]["tools"]), 8)
        self.assertFalse(responses[2]["result"]["isError"])
        self.assertTrue(json.loads(responses[2]["result"]["content"][0]["text"])["valid"])

    def test_protocol_and_argument_errors_are_recoverable(self):
        responses = self.exchange([
            "invalid-json", {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "inspect_skills", "arguments": {"sources": [], "evil": True}}},
            {"jsonrpc": "2.0", "id": 3, "method": "unknown"},
            {"jsonrpc": "2.0", "id": 4, "method": "ping"},
        ])
        self.assertEqual(responses[0]["error"]["code"], -32700)
        self.assertTrue(responses[2]["result"]["isError"])
        self.assertEqual(responses[3]["error"]["code"], -32601)
        self.assertEqual(responses[4]["result"], {})


if __name__ == "__main__":
    unittest.main()
