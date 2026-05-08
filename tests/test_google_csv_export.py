import unittest

from weekly_assistant.adapters.google_csv_export import export_url


class GoogleCsvExportTest(unittest.TestCase):
    def test_builds_gviz_export_url(self):
        url = export_url("spreadsheet-id", "123")

        self.assertIn("/spreadsheets/d/spreadsheet-id/gviz/tq", url)
        self.assertIn("gid=123", url)
        self.assertIn("tqx=out%3Acsv", url)


if __name__ == "__main__":
    unittest.main()
