"""Offline contracts for the shared request-delay policy."""

import random
import time
import unittest
from unittest.mock import call, patch

import inscrawler.downloads.control as downloads_control


class DelayPolicyTests(unittest.TestCase):
    def test_configured_delay_ranges(self):
        self.assertEqual(downloads_control.REQUEST_DELAY, (20, 40))
        self.assertEqual(downloads_control.DOWNLOAD_DELAY, (20, 40))
        self.assertEqual(downloads_control.USER_SWITCH_DELAY, (40, 80))
        self.assertEqual(downloads_control.DELAY_JITTER_RANGE, (1.0, 1.5))

    def test_every_delay_kind_uses_shared_jitter_range(self):
        cases = (
            ("request", (20, 40)),
            ("download", (20, 40)),
            ("user_switch", (40, 80)),
        )

        for kind, base_range in cases:
            with self.subTest(kind=kind):
                minimum = float(base_range[0])
                with (
                    patch.object(
                        random,
                        "uniform",
                        side_effect=(minimum, 1.0),
                    ) as uniform,
                    patch.object(
                        time,
                        "monotonic",
                        side_effect=(0.0, minimum),
                    ),
                    patch.object(downloads_control, "wait_if_paused"),
                    patch.object(downloads_control, "_raise_if_stop_requested"),
                    patch("builtins.print"),
                ):
                    downloads_control.delay(kind)

                self.assertEqual(
                    uniform.call_args_list,
                    [call(*base_range), call(1.0, 1.5)],
                )


if __name__ == "__main__":
    unittest.main()
