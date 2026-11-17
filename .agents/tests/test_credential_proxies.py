import pytest
from credentials.http import proxy_configuration


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        (
            {"https_proxy": "http://chosen:8080", "HTTPS_PROXY": "http://inherited"},
            {"https": "http://chosen:8080"},
        ),
        (
            {"HTTPS_PROXY": "http://inherited", "https_proxy": "http://chosen:8080"},
            {"https": "http://chosen:8080"},
        ),
        ({"https_proxy": "", "HTTPS_PROXY": "http://inherited"}, {}),
        ({"no_proxy": "", "NO_PROXY": "example.invalid"}, {}),
        (
            {"https_proxy": "", "all_proxy": "http://fallback:8080"},
            {"all": "http://fallback:8080", "http": "http://fallback:8080"},
        ),
    ],
)
def test_lowercase_proxy_settings_override_inherited_values_and_can_disable_them(
    environment: dict[str, str], expected: dict[str, str]
) -> None:
    assert proxy_configuration(environment) == expected
