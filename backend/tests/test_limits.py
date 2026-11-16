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


class OpenSignIn(unittest.TestCase):
    def setUp(self):
        import os
        os.environ.pop("KACY_DEFAULT_ROLE", None)
        os.environ["KACY_USER_ROLES"] = "boss@example.com=exec"

    def tearDown(self):
        import os
        os.environ.pop("KACY_DEFAULT_ROLE", None)
        os.environ.pop("KACY_USER_ROLES", None)

    def test_closed_by_default(self):
        from app.identity.rbac import AccessDenied, role_for
        with self.assertRaises(AccessDenied):
            role_for("stranger@gmail.com")

    def test_open_gives_employee_but_keeps_listed_roles(self):
        import os
        from app.identity.rbac import Role, is_open_user, role_for
        os.environ["KACY_DEFAULT_ROLE"] = "employee"
        self.assertEqual(role_for("stranger@gmail.com"), Role.EMPLOYEE)
        self.assertTrue(is_open_user("stranger@gmail.com"))
        self.assertEqual(role_for("boss@example.com"), Role.EXEC)
        self.assertFalse(is_open_user("boss@example.com"))
        self.assertFalse(is_open_user("alice@kacy-demo.test"))

    def test_default_can_never_be_manager_or_exec(self):
        import os
        from app.identity.rbac import role_for
        os.environ["KACY_DEFAULT_ROLE"] = "exec"
        with self.assertRaises(ValueError):
            role_for("stranger@gmail.com")
