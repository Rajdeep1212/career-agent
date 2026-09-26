"""GenAI-era titles belong to the AI role family, so a GenAI search keeps them."""
import unittest

from app.services.role_discovery import family_for_title
from app.services.search_intent import interpret_search_request


class GenAIRoleFamilyTests(unittest.TestCase):
    def test_genai_titles_map_to_the_ai_family(self):
        for title in ("GenAI Engineer", "Gen AI Developer", "Generative AI Developer", "LLM Engineer",
                      "NLP Engineer", "Prompt Engineer", "Computer Vision Engineer", "AI Engineer (Fresher)"):
            family = family_for_title(title)
            self.assertEqual(family and family["family"], "ai", title)

    def test_genai_request_gets_the_ai_family(self):
        self.assertEqual(interpret_search_request("GenAI engineer fresher").role_families, ["ai"])

    def test_plain_software_titles_are_unchanged(self):
        self.assertEqual(family_for_title("Python Backend Developer")["family"], "software")


if __name__ == "__main__":
    unittest.main()
