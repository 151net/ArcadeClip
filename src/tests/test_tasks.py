"""Cancellation is expected; genuine background failures remain errors."""
from concurrent.futures import CancelledError
import unittest
from unittest.mock import patch

from tasks import Task


class TaskTests(unittest.TestCase):
    def test_cancellation_and_failure_logging(self):
        for error in (CancelledError(), InterruptedError(), ValueError("failed")):
            with self.subTest(error=type(error).__name__):
                def operation(cancel, report):
                    raise error
                task = Task(operation)
                with patch("tasks.logging.exception") as log:
                    task.run()
                cancelled = isinstance(error, (CancelledError, InterruptedError))
                self.assertEqual(task.cancel.is_set(), cancelled)
                self.assertEqual(log.call_count, 0 if cancelled else 1)
                self.assertIs(task.error, error)
                self.assertIsNone(task.operation)


if __name__ == "__main__":
    unittest.main()
