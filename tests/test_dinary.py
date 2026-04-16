import allure

from dinary import __version__


@allure.epic("Build")
@allure.feature("Version")
def test_version():
    assert __version__
