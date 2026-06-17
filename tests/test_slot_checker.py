import unittest

from src.slot_checker import _extract_time, _time_choice_priority


class ExtractTimeTests(unittest.TestCase):
    def test_does_not_read_numeric_date_as_time(self):
        self.assertIsNone(_extract_time("Mittwoch, 05.08.2026 Mittwoch, 05.08.2026"))

    def test_reads_colon_time(self):
        self.assertEqual(_extract_time("09:30"), "09:30")

    def test_reads_time_when_date_is_also_present(self):
        self.assertEqual(_extract_time("Mittwoch, 05.08.2026 09:30"), "09:30")

    def test_reads_german_dot_time_with_uhr_suffix(self):
        self.assertEqual(_extract_time("Termin 09.30 Uhr"), "09:30")

    def test_reads_standalone_dot_time(self):
        self.assertEqual(_extract_time("09.30"), "09:30")


class TimeChoicePriorityTests(unittest.TestCase):
    def test_prefers_exact_visible_time_button_over_range_text(self):
        self.assertLess(
            _time_choice_priority("09:30", "09:30"),
            _time_choice_priority("09:00 - 10:00", "09:00"),
        )

    def test_prefers_visible_time_label_over_range_text(self):
        self.assertLess(
            _time_choice_priority("Termin 11:00", "11:00"),
            _time_choice_priority("10:00 - 11:00", "10:00"),
        )

    def test_date_container_is_last_resort(self):
        self.assertLess(
            _time_choice_priority("09:00 - 10:00", "09:00"),
            _time_choice_priority("Montag, 03.08.2026 09:00 - 10:00", "09:00"),
        )


if __name__ == "__main__":
    unittest.main()
