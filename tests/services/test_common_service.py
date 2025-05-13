import unittest
from unittest.mock import patch
from api.services.common_service import get_final_summary_comment_text

class TestCommonService(unittest.TestCase):

    @patch('api.services.common_service.app_configs', {"OPENAI_MODEL": "gpt-test"})
    def test_get_final_summary_comment_text_with_specific_model(self):
        expected_text = "本次AI代码审查已完成，审核模型:「gpt-test」 修改意见仅供参考，具体修改请根据实际场景进行调整。"
        self.assertEqual(get_final_summary_comment_text(), expected_text)

    @patch('api.services.common_service.app_configs', {"OPENAI_MODEL": "gpt-4o"}) # Default model
    def test_get_final_summary_comment_text_with_default_model(self):
        expected_text = "本次AI代码审查已完成，审核模型:「gpt-4o」 修改意见仅供参考，具体修改请根据实际场景进行调整。"
        self.assertEqual(get_final_summary_comment_text(), expected_text)

if __name__ == '__main__':
    unittest.main()
