"""Vocabulary v2: generic words need an explicit Skills entry; better GenAI coverage on snippets."""
import unittest

from app.services.cv_parser import parse_profile_from_text
from app.services.skills import extract_skills, load_vocabulary


class ExplicitOnlySkillTests(unittest.TestCase):
    def test_research_is_not_extracted_from_titles_or_prose(self):
        for text in ("Research Intern at Example Labs", "Join our research team working on NLP"):
            self.assertNotIn("Research", extract_skills(text), text)

    def test_research_listed_under_skills_is_kept(self):
        profile = parse_profile_from_text("Jane Doe\nSkills: Research, Python\nExperience\nResearch Intern, Example Labs")
        self.assertIn("Research", profile.skills)

    def test_research_heading_alone_does_not_add_the_skill(self):
        profile = parse_profile_from_text("Jane Doe\nRESEARCH EXPERIENCE\nResearch Intern, Example Labs\nSkills: Python")
        self.assertNotIn("Research", profile.skills)


class GenAISnippetTests(unittest.TestCase):
    def test_short_aggregator_snippet(self):
        snippet = ("...build GPT-4 and Llama based assistants, semantic search over documents, prompt design "
                   "and agentic workflows; experience with text embeddings and BERT is a plus...")
        skills = extract_skills(snippet)
        for skill in ("LLM", "Vector Database", "Prompt Engineering", "AI Agents", "Embeddings", "BERT"):
            self.assertIn(skill, skills)

    def test_released_v1_is_unchanged(self):
        names = {entry["name"] for entry in load_vocabulary(1)["skills"]}
        self.assertNotIn("Embeddings", names)
        self.assertIn("Research", names)


if __name__ == "__main__":
    unittest.main()
