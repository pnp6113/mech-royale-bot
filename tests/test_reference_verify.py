"""The supplemental verifier catches real malformed stdout and early exits."""

from pathlib import Path
import sys
import time
import unittest

from tools.reference_verify import (
    WireError, checked_frame, close_bot, load_checker, load_stream, next_line, read_response,
)


KIT = Path(__file__).resolve().parents[1] / "kit"
FIXTURE = """
import json, os, sys

json.loads(sys.stdin.readline())
print(json.dumps({'type': 'ready', 'name': 'fixture'}), flush=True)
for line in sys.stdin:
    message = json.loads(line)
    if message['type'] == 'end':
        break
    tick = message['tick']
    mode = sys.argv[1]
    if mode == 'crash':
        os._exit(23)
    if mode == 'nonfinite':
        print('{"tick":' + str(tick) + ',"move":[NaN,0]}', flush=True)
    elif mode == 'shape':
        print(json.dumps({'tick': tick, 'move': [0]}), flush=True)
    else:
        print(json.dumps({'tick': tick}), flush=True)
"""


class SupplementalVerifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checker = load_checker(KIT)
        cls.schema = cls.checker.Schema(KIT / "mechfight.schema.json")
        cls.hello, cls.observations, cls.end = load_stream(KIT, KIT / "arena")
        cls.max_bytes = cls.hello["config"]["bot"]["max_line_bytes"]

    def start(self, mode):
        bot = self.checker.Bot([sys.executable, "-u", "-c", FIXTURE, mode])
        self.addCleanup(close_bot, bot)
        bot.send(self.hello)
        _, raw = next_line(bot, time.perf_counter() + 4)
        checked_frame(raw, self.schema, "Ready", self.max_bytes)
        return bot

    def response(self, bot, batch):
        bot.send_many(batch)
        return read_response(
            bot, deadline=time.perf_counter() + 4, wanted_tick=batch[-1]["tick"],
            sent_ticks={obs["tick"] for obs in batch}, schema=self.schema, max_bytes=self.max_bytes,
        )

    def test_nonfinite_action_is_rejected_from_child_pipe(self):
        with self.assertRaisesRegex(WireError, "non-finite JSON number"):
            self.response(self.start("nonfinite"), self.observations[:1])

    def test_finite_but_invalid_action_shape_is_rejected_from_child_pipe(self):
        with self.assertRaisesRegex(WireError, "invalid Action"):
            self.response(self.start("shape"), self.observations[:1])

    def test_overflow_number_and_unterminated_frame_are_rejected(self):
        with self.assertRaisesRegex(WireError, "non-finite JSON number"):
            checked_frame(b'{"tick":0,"move":[1e999,0]}\n', self.schema, "Action", self.max_bytes)
        with self.assertRaisesRegex(WireError, "missing newline"):
            checked_frame(b'{"tick":0}', self.schema, "Action", self.max_bytes)

    def test_queued_stale_actions_are_counted_before_newest(self):
        _, stale = self.response(self.start("queue"), self.observations[:3])
        self.assertEqual(stale, 2)

    def test_child_crash_is_distinct_from_timeout_or_invalid_action(self):
        with self.assertRaisesRegex(WireError, "exited before expected output \\(status 23\\)"):
            self.response(self.start("crash"), self.observations[:1])


if __name__ == "__main__":
    unittest.main()
