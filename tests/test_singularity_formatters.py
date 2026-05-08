import unittest

from weekly_assistant.services.singularity_formatters import (
    filter_items_by_query,
    format_singularity_projects,
    format_singularity_tasks,
)


class SingularityFormattersTest(unittest.TestCase):
    def test_formats_projects(self):
        text = format_singularity_projects({"projects": [{"id": "P-1", "title": "Отдать Володе", "state": 1}]})

        self.assertIn("Отдать Володе", text)
        self.assertIn("id=P-1", text)

    def test_formats_tasks_and_deduplicates(self):
        response = {
            "tasks": [
                {"id": "T-1", "title": "Task", "projectId": "P-1", "state": 1},
                {"id": "T-1", "title": "Task", "projectId": "P-1", "state": 1},
            ]
        }

        text = format_singularity_tasks(response)

        self.assertEqual(text.count("Task"), 1)

    def test_filters_by_query(self):
        items = [{"title": "Отдать Володе"}, {"title": "Другое"}]

        result = filter_items_by_query(items, "володе")

        self.assertEqual(result, [{"title": "Отдать Володе"}])


if __name__ == "__main__":
    unittest.main()
