from __future__ import annotations

import unittest

from coding_agent.approval import ApprovalStore


class ApprovalStoreTests(unittest.TestCase):
    def test_create_issues_an_opaque_token_with_bound_metadata(self) -> None:
        store = ApprovalStore()
        command = "deploy --api-key=do-not-store-this"

        try:
            approval = store.create("task-17", command, now=100.0)
        except NotImplementedError:
            approval = None

        self.assertIsInstance(approval, dict)
        self.assertEqual(approval["task_id"], "task-17")
        self.assertEqual(approval["command"], command)
        self.assertEqual(approval["expires_at"], 700.0)
        self.assertIsInstance(approval["token"], str)
        self.assertTrue(approval["token"])
        self.assertNotIn(command, approval["token"])
        self.assertNotIn("do-not-store-this", approval["token"])

    def test_consume_accepts_a_fresh_matching_token(self) -> None:
        store = ApprovalStore()
        approval = store.create("task-17", "python3 -m unittest", now=100.0)

        self.assertTrue(
            store.consume(
                approval["token"],
                "task-17",
                "python3 -m unittest",
                now=101.0,
            )
        )

    def test_consume_rejects_a_reused_token(self) -> None:
        store = ApprovalStore()
        approval = store.create("task-17", "python3 -m unittest", now=100.0)

        self.assertTrue(store.consume(approval["token"], "task-17", "python3 -m unittest", now=101.0))
        self.assertFalse(store.consume(approval["token"], "task-17", "python3 -m unittest", now=102.0))

    def test_consume_rejects_a_different_task_id_without_consuming_token(self) -> None:
        store = ApprovalStore()
        approval = store.create("task-17", "python3 -m unittest", now=100.0)

        self.assertFalse(store.consume(approval["token"], "task-18", "python3 -m unittest", now=101.0))
        self.assertTrue(store.consume(approval["token"], "task-17", "python3 -m unittest", now=102.0))

    def test_consume_rejects_a_different_command_without_consuming_token(self) -> None:
        store = ApprovalStore()
        approval = store.create("task-17", "python3 -m unittest", now=100.0)

        self.assertFalse(store.consume(approval["token"], "task-17", "python3 -m unittest -v", now=101.0))
        self.assertTrue(store.consume(approval["token"], "task-17", "python3 -m unittest", now=102.0))

    def test_consume_rejects_an_expired_token(self) -> None:
        store = ApprovalStore(ttl_seconds=30)
        approval = store.create("task-17", "python3 -m unittest", now=100.0)

        self.assertEqual(approval["expires_at"], 130.0)
        self.assertFalse(store.consume(approval["token"], "task-17", "python3 -m unittest", now=130.0))


if __name__ == "__main__":
    unittest.main()
