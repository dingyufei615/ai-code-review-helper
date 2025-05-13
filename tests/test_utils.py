import unittest
from api.utils import parse_single_file_diff

class TestUtils(unittest.TestCase):

    def test_parse_single_file_diff_empty(self):
        diff_text = ""
        file_path = "file.py"
        expected = {
            "path": "file.py",
            "old_path": None,
            "changes": [],
            "context": {"old": "", "new": ""},
            "lines_changed": 0
        }
        self.assertEqual(parse_single_file_diff(diff_text, file_path), expected)

    def test_parse_single_file_diff_additions_only(self):
        diff_text = (
            "@@ -0,0 +1,3 @@\n"
            "+line1\n"
            "+line2\n"
            "+line3"
        )
        file_path = "new_file.py"
        expected_changes = [
            {"type": "add", "old_line": None, "new_line": 1, "content": "line1"},
            {"type": "add", "old_line": None, "new_line": 2, "content": "line2"},
            {"type": "add", "old_line": None, "new_line": 3, "content": "line3"},
        ]
        result = parse_single_file_diff(diff_text, file_path)
        self.assertEqual(result["path"], file_path)
        self.assertEqual(result["changes"], expected_changes)
        self.assertEqual(result["lines_changed"], 3)

    def test_parse_single_file_diff_deletions_only(self):
        diff_text = (
            "@@ -1,3 +0,0 @@\n"
            "-old_line1\n"
            "-old_line2\n"
            "-old_line3"
        )
        file_path = "deleted_file.py"
        expected_changes = [
            {"type": "delete", "old_line": 1, "new_line": None, "content": "old_line1"},
            {"type": "delete", "old_line": 2, "new_line": None, "content": "old_line2"},
            {"type": "delete", "old_line": 3, "new_line": None, "content": "old_line3"},
        ]
        result = parse_single_file_diff(diff_text, file_path)
        self.assertEqual(result["path"], file_path)
        self.assertEqual(result["changes"], expected_changes)
        self.assertEqual(result["lines_changed"], 3)

    def test_parse_single_file_diff_mixed_changes(self):
        diff_text = (
            "@@ -1,2 +1,3 @@\n"
            "-old_line1\n"
            " context_line\n"
            "+new_line1\n"
            "+new_line2"
        )
        file_path = "modified_file.py"
        result = parse_single_file_diff(diff_text, file_path)
        self.assertEqual(result["path"], file_path)
        self.assertEqual(len(result["changes"]), 3) # 1 delete, 2 adds
        self.assertEqual(result["lines_changed"], 3)
        self.assertIn({"type": "delete", "old_line": 1, "new_line": None, "content": "old_line1"}, result["changes"])
        self.assertIn({"type": "add", "old_line": None, "new_line": 2, "content": "new_line1"}, result["changes"]) # new_line numbers are based on the new file state after context
        self.assertIn({"type": "add", "old_line": None, "new_line": 3, "content": "new_line2"}, result["changes"])
        self.assertIn("2 -> 1: context_line", result["context"]["new"]) # Check context (修正期望的行号)

    def test_parse_single_file_diff_multiple_hunks(self):
        diff_text = (
            "@@ -1,1 +1,1 @@\n"
            "-old_content1\n"
            "+new_content1\n"
            "@@ -5,1 +5,1 @@\n"
            "-old_content2\n"
            "+new_content2"
        )
        file_path = "multi_hunk.py"
        result = parse_single_file_diff(diff_text, file_path)
        self.assertEqual(result["path"], file_path)
        self.assertEqual(len(result["changes"]), 4) # 2 deletes, 2 adds
        self.assertEqual(result["lines_changed"], 4)

    def test_parse_single_file_diff_renamed_file(self):
        diff_text = "@@ -1,1 +1,1 @@\n-old\n+new"
        file_path = "new_name.py"
        old_file_path = "old_name.py"
        result = parse_single_file_diff(diff_text, file_path, old_file_path=old_file_path)
        self.assertEqual(result["path"], file_path)
        self.assertEqual(result["old_path"], old_file_path)

if __name__ == '__main__':
    unittest.main()
