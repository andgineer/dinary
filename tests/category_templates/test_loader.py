import allure
import pytest

from dinary.category_templates import loader
from dinary.category_templates.loader import Template


def _template(
    *,
    code="simple",
    names=None,
    taglines=None,
    groups=None,
    renames=None,
    visible=None,
    hidden=None,
) -> Template:
    return Template(
        code=code,
        names=names if names is not None else {"en": "Simple", "ru": "Просто"},
        taglines=taglines if taglines is not None else {"en": "Basics", "ru": "Основное"},
        groups=groups if groups is not None else {"food": {"en": "Food", "ru": "Еда"}},
        renames=renames if renames is not None else {},
        visible=visible if visible is not None else {"food": ["groceries"]},
        hidden=hidden if hidden is not None else {},
    )


@allure.epic("Category templates")
@allure.feature("Loader")
class TestLoadVocabulary:
    def test_returns_code_to_translations_mapping(self):
        vocabulary = loader.load_vocabulary()

        assert "groceries" in vocabulary
        assert vocabulary["groceries"] == {"en": "Groceries", "ru": "продукты", "sr": "Namirnice"}


@allure.epic("Category templates")
@allure.feature("Loader")
class TestLoadTemplates:
    def test_returns_all_shipped_templates_alphabetically(self):
        templates = loader.load_templates()

        assert [t.code for t in templates] == ["active", "family", "freelancer", "simple"]

    def test_each_template_has_taglines_for_every_name_language(self):
        templates = loader.load_templates()

        for template in templates:
            assert set(template.taglines) == set(template.names)


@allure.epic("Category templates")
@allure.feature("Loader")
class TestValidate:
    def test_shipped_vocabulary_and_templates_are_valid(self):
        vocabulary = loader.load_vocabulary()
        templates = loader.load_templates()

        loader.validate(vocabulary, templates)

    def test_empty_templates_raises(self):
        with pytest.raises(ValueError, match="no templates"):
            loader.validate({"groceries": {"en": "Groceries", "ru": "продукты"}}, [])

    def test_mismatched_names_language_keys_raises(self):
        vocabulary = {"groceries": {"en": "Groceries", "ru": "продукты"}}
        templates = [
            _template(names={"en": "Simple", "ru": "Просто"}),
            _template(code="active", names={"en": "Active"}),
        ]

        with pytest.raises(ValueError, match="'names' language keys"):
            loader.validate(vocabulary, templates)

    def test_mismatched_taglines_language_keys_raises(self):
        vocabulary = {"groceries": {"en": "Groceries", "ru": "продукты"}}
        templates = [_template(taglines={"en": "Basics"})]

        with pytest.raises(ValueError, match="'taglines' language keys"):
            loader.validate(vocabulary, templates)

    def test_vocabulary_entry_missing_translation_raises(self):
        vocabulary = {"groceries": {"en": "Groceries"}}
        templates = [_template()]

        with pytest.raises(ValueError, match="missing translations"):
            loader.validate(vocabulary, templates)

    def test_code_placed_more_than_once_raises(self):
        vocabulary = {"groceries": {"en": "Groceries", "ru": "продукты"}}
        templates = [
            _template(
                visible={"food": ["groceries"]},
                hidden={"food": ["groceries"]},
            ),
        ]

        with pytest.raises(ValueError, match="placed more than once"):
            loader.validate(vocabulary, templates)

    def test_missing_vocabulary_code_raises(self):
        vocabulary = {
            "groceries": {"en": "Groceries", "ru": "продукты"},
            "fruit": {"en": "Fruit", "ru": "фрукты"},
        }
        templates = [_template(visible={"food": ["groceries"]})]

        with pytest.raises(ValueError, match="missing vocabulary codes"):
            loader.validate(vocabulary, templates)

    def test_unknown_code_raises(self):
        vocabulary = {"groceries": {"en": "Groceries", "ru": "продукты"}}
        templates = [_template(visible={"food": ["groceries", "fruit"]})]

        with pytest.raises(ValueError, match="unknown codes"):
            loader.validate(vocabulary, templates)

    def test_undeclared_group_raises(self):
        vocabulary = {"groceries": {"en": "Groceries", "ru": "продукты"}}
        templates = [
            _template(
                groups={"food": {"en": "Food", "ru": "Еда"}},
                visible={"other": ["groceries"]},
            ),
        ]

        with pytest.raises(ValueError, match="undeclared groups"):
            loader.validate(vocabulary, templates)
