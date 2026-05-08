import unittest

from weekly_assistant.services.singularity_access import check_project_access
from weekly_assistant.utils.http import JsonHttpError


class FakeAdapter:
    def list_projects(self, max_count=1000, include_archived=False):
        return {"projects": [{"id": "P-1", "title": "Visible"}]}

    def list_tasks(self, project_id, max_count=100, include_archived=False, include_removed=False):
        if project_id == "P-1":
            return {"tasks": [{"id": "T-1"}, {"id": "T-1"}]}
        return {"tasks": []}

    def _request(self, method, path):
        raise JsonHttpError(method, path, 404, "not found")


class SingularityAccessTest(unittest.TestCase):
    def test_check_project_access_visible_project(self):
        access = check_project_access(FakeAdapter(), "P-1")

        self.assertTrue(access.visible_in_project_list)
        self.assertEqual(access.active_task_count, 1)

    def test_check_project_access_missing_project(self):
        access = check_project_access(FakeAdapter(), "P-2")

        self.assertFalse(access.visible_in_project_list)
        self.assertEqual(access.active_task_count, 0)


if __name__ == "__main__":
    unittest.main()
