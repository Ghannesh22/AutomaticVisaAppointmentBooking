import asyncio
import logging
import unittest

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

from src.booker import SlotSelectionUnavailable, _click_appointment_time, _click_detected_time_element
from src.slot_checker import SlotCandidate


class VisibleTimeControlTests(unittest.TestCase):
    def test_clicks_detected_time_element(self):
        asyncio.run(self._run_detected_time_element_test())

    def test_does_not_collapse_visible_time_row_after_failed_selection(self):
        asyncio.run(self._run_no_collapse_after_failed_selection_test())

    def test_dom_clicks_detected_time_when_coordinates_are_obstructed(self):
        asyncio.run(self._run_dom_click_obstructed_time_test())

    async def _run_detected_time_element_test(self):
        async with async_playwright() as playwright:
            try:
                browser = await playwright.chromium.launch(headless=True)
            except PlaywrightError as exc:
                self.skipTest(f"Playwright Chromium is not installed: {exc}")

            try:
                page = await browser.new_page()
                await page.set_content(
                    """
                    <button id="slot"><span id="detected">08:30</span></button>
                    <button id="continue" style="display: none">Weiter</button>
                    <script>
                    document.getElementById("slot").addEventListener("click", () => {
                      document.getElementById("continue").style.display = "block";
                    });
                    </script>
                    """
                )
                element = await page.locator("#detected").element_handle()
                slot = SlotCandidate(
                    element=element,
                    date_text="2026-08-19",
                    time_text="08:30",
                    raw_text="Mittwoch, 19.08.2026 | 08:30 08:30 08:30",
                )

                clicked = await _click_detected_time_element(
                    page,
                    slot,
                    logging.getLogger("test"),
                )

                self.assertTrue(clicked)
                self.assertEqual(
                    await page.locator("#continue").evaluate("el => getComputedStyle(el).display"),
                    "block",
                )
            finally:
                await browser.close()

    async def _run_no_collapse_after_failed_selection_test(self):
        async with async_playwright() as playwright:
            try:
                browser = await playwright.chromium.launch(headless=True)
            except PlaywrightError as exc:
                self.skipTest(f"Playwright Chromium is not installed: {exc}")

            try:
                page = await browser.new_page()
                await page.set_content(
                    """
                    <button id="date-row">Donnerstag, 20.08.2026</button>
                    <span id="visible-time">08:30</span>
                    <script>
                    document.getElementById("date-row").addEventListener("click", () => {
                      document.getElementById("visible-time").style.display = "none";
                    });
                    </script>
                    """
                )
                point = await page.locator("#date-row").evaluate(
                    """el => {
                        const rect = el.getBoundingClientRect();
                        return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
                    }"""
                )
                slot = SlotCandidate(
                    element=None,
                    date_text="2026-08-20",
                    time_text="08:30",
                    raw_text="Donnerstag, 20.08.2026 | 08:30",
                    date_click_x=point["x"],
                    date_click_y=point["y"],
                )

                with self.assertRaises(SlotSelectionUnavailable):
                    await _click_appointment_time(page, slot, logging.getLogger("test"))

                self.assertTrue(await page.locator("#visible-time").is_visible())
            finally:
                await browser.close()

    async def _run_dom_click_obstructed_time_test(self):
        async with async_playwright() as playwright:
            try:
                browser = await playwright.chromium.launch(headless=True)
            except PlaywrightError as exc:
                self.skipTest(f"Playwright Chromium is not installed: {exc}")

            try:
                page = await browser.new_page()
                await page.set_content(
                    """
                    <style>
                      #slot { position: absolute; left: 40px; top: 40px; width: 120px; height: 40px; }
                      #cover { position: absolute; left: 40px; top: 40px; width: 120px; height: 40px; z-index: 2; }
                    </style>
                    <button id="slot"><span id="detected">08:30</span></button>
                    <div id="cover"></div>
                    <button id="continue" style="display: none">Weiter</button>
                    <script>
                    document.getElementById("slot").addEventListener("click", () => {
                      document.getElementById("continue").style.display = "block";
                    });
                    </script>
                    """
                )
                element = await page.locator("#detected").element_handle()
                slot = SlotCandidate(
                    element=element,
                    date_text="2026-08-24",
                    time_text="08:30",
                    raw_text="Montag, 24.08.2026 | 08:30 08:30 08:30",
                )

                clicked = await _click_detected_time_element(
                    page,
                    slot,
                    logging.getLogger("test"),
                )

                self.assertTrue(clicked)
                self.assertEqual(
                    await page.locator("#continue").evaluate("el => getComputedStyle(el).display"),
                    "block",
                )
            finally:
                await browser.close()


if __name__ == "__main__":
    unittest.main()
