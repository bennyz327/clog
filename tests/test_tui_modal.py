from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.tui import ClogApp, RecordFormScreen, ensure_textual


class TuiModalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        ensure_textual()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.sqlite"
        self.app = ClogApp(self.db_path)

    def tearDown(self) -> None:
        self.app.close_connection()
        self._tmpdir.cleanup()

    async def test_add_creator_action_opens_modal(self) -> None:
        async with self.app.run_test() as pilot:
            handled = await self.app.run_action("add_creator")
            await pilot.pause()
            self.assertTrue(handled)
            self.assertEqual(len(self.app.screen_stack), 2)
            self.assertIsInstance(self.app.screen_stack[-1], RecordFormScreen)
            self.assertEqual(self.app.screen_stack[-1].title, "Add Creator")

    async def test_cancel_closes_add_creator_modal(self) -> None:
        async with self.app.run_test() as pilot:
            handled = await self.app.run_action("add_creator")
            await pilot.pause()
            self.assertTrue(handled)
            self.assertEqual(len(self.app.screen_stack), 2)
            await pilot.click("#cancel")
            await pilot.pause()
            self.assertEqual(len(self.app.screen_stack), 1)
            self.assertFalse(self.app.form_action_running)

    async def test_add_name_action_opens_modal(self) -> None:
        self.app.current_creator_id = 1
        async with self.app.run_test() as pilot:
            handled = await self.app.run_action("add_name")
            await pilot.pause()
            self.assertTrue(handled)
            self.assertEqual(len(self.app.screen_stack), 2)
            self.assertIsInstance(self.app.screen_stack[-1], RecordFormScreen)
            self.assertEqual(self.app.screen_stack[-1].title, "Add Name")

    async def test_cancel_closes_add_name_modal(self) -> None:
        self.app.current_creator_id = 1
        async with self.app.run_test() as pilot:
            handled = await self.app.run_action("add_name")
            await pilot.pause()
            self.assertTrue(handled)
            self.assertEqual(len(self.app.screen_stack), 2)
            await pilot.click("#cancel")
            await pilot.pause()
            self.assertEqual(len(self.app.screen_stack), 1)
            self.assertFalse(self.app.form_action_running)
