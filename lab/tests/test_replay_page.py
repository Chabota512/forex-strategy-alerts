import re
import subprocess
import unittest
from pathlib import Path


REPLAY_PATH = Path(__file__).parents[1] / "replay.html"
NODE_CHECK = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const html = fs.readFileSync(process.argv[1], "utf8");
const script = html.match(/<script>([\s\S]*?)<\/script>/);
assert.ok(script, "inline replay script must exist");
const context = { module: { exports: {} }, console };
vm.runInNewContext(script[1], context, { filename: "replay.html" });
const { parseCsvLine, parseCsvTextForTests, indexAlertsForTests, filenamePairHint } =
  context.module.exports;
const quotedCsv = '2026-01-01T00:00:00Z,SELL,"a note, with ""quotes""' + '"';
assert.deepEqual(
  Array.from(parseCsvLine(quotedCsv)),
  ["2026-01-01T00:00:00Z", "SELL", 'a note, with "quotes"'],
);
const rows = parseCsvTextForTests(
  "time,symbol,tf,direction,cct,cst,strength_label,note\n" +
  '2026-01-01T00:00:00Z,GBPUSD,M5,SELL,downtrend,downtrend,STRONG,"comma, note"\n',
);
const alertIndex = indexAlertsForTests(rows);
const alert = alertIndex.get("GBPUSD_M5").get(Date.parse("2026-01-01T00:00:00Z"))[0];
assert.equal(alert.direction, "SELL");
assert.equal(alert.strength, "STRONG");
assert.equal(filenamePairHint("synthetic_GBPUSD_M5.csv"), "GBPUSD_M5");
assert.throws(() => parseCsvLine('"unfinished field'));
"""


class ReplayPageTests(unittest.TestCase):
    def test_page_is_self_contained_and_has_no_network_calls(self):
        text = REPLAY_PATH.read_text(encoding="utf-8")
        self.assertIn('type="file"', text)
        self.assertIn('id="position"', text)
        self.assertIn('id="speed"', text)
        self.assertIn("Files stay in your browser", text)
        self.assertNotIn("fetch(", text)
        self.assertNotIn("https://", text)

    def test_inline_csv_parser_and_alert_index(self):
        completed = subprocess.run(
            ["node", "-e", NODE_CHECK, str(REPLAY_PATH)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=completed.stderr or completed.stdout,
        )


if __name__ == "__main__":
    unittest.main()
