import os
import unittest

from app import limits


class DemoLimits(unittest.TestCase):
    def setUp(self):
        limits.reset()
        for k in ("KACY_DEMO_PAUSED", "KACY_DEMO_PER_HOUR", "KACY_DEMO_PER_DAY", "KACY_DEMO_DAILY_CAP"):
            os.environ.pop(k, None)

    def test_hourly_limit_then_recovers(self):
        os.environ["KACY_DEMO_PER_HOUR"] = "3"
        for i in range(3):
            limits.check_demo_message("1.1.1.1", now=1000 + i)
        with self.assertRaises(limits.LimitError) as e:
            limits.check_demo_message("1.1.1.1", now=1010)
        self.assertEqual(e.exception.status_code, 429)
        limits.check_demo_message("1.1.1.1", now=1000 + 3700)  # an hour later it works again

    def test_other_visitor_unaffected(self):
        os.environ["KACY_DEMO_PER_HOUR"] = "1"
        limits.check_demo_message("a", now=1000)
        limits.check_demo_message("b", now=1001)

    def test_daily_per_visitor(self):
        os.environ["KACY_DEMO_PER_HOUR"] = "100"
        os.environ["KACY_DEMO_PER_DAY"] = "2"
        limits.check_demo_message("a", now=1000)
        limits.check_demo_message("a", now=5000)
        with self.assertRaises(limits.LimitError):
            limits.check_demo_message("a", now=9000)

    def test_global_cap_and_new_day(self):
        os.environ["KACY_DEMO_DAILY_CAP"] = "2"
        limits.check_demo_message("a", now=1000)
        limits.check_demo_message("b", now=1001)
        with self.assertRaises(limits.LimitError) as e:
            limits.check_demo_message("c", now=1002)
        self.assertIn("today", e.exception.detail)
        limits.check_demo_message("c", now=1000 + 86400)  # next UTC day

    def test_kill_switch(self):
        os.environ["KACY_DEMO_PAUSED"] = "1"
        with self.assertRaises(limits.LimitError) as e:
            limits.check_demo_message("a")
        self.assertEqual(e.exception.status_code, 503)
        with self.assertRaises(limits.LimitError):
            limits.check_demo_login("a")

    def test_login_limit(self):
        os.environ["KACY_DEMO_LOGINS_PER_HOUR"] = "2"
        limits.check_demo_login("a", now=1)
        limits.check_demo_login("a", now=2)
        with self.assertRaises(limits.LimitError):
            limits.check_demo_login("a", now=3)
        os.environ.pop("KACY_DEMO_LOGINS_PER_HOUR")


if __name__ == "__main__":
    unittest.main()
