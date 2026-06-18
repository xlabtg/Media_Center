from __future__ import annotations

import pytest
from cglr.template_engine import (
    TemplateRenderError,
    TemplateSecurityError,
    TemplateValidationError,
    render_template,
)


def test_template_engine_renders_common_jinja_template() -> None:
    result = render_template(
        {
            "template_body": (
                "# {{ title }}\n"
                "{% for item in items %}- {{ loop.index }}. {{ item }}\n"
                "{% endfor %}"
                "CTA: {{ cta|upper }}"
            ),
            "context": {
                "title": "Plan",
                "items": ["post", "poll"],
                "cta": "act now",
            },
            "validation": {
                "max_length": 200,
                "required_blocks": ["# Plan", "CTA:"],
            },
        }
    )

    assert result.content == "# Plan\n- 1. post\n- 2. poll\nCTA: ACT NOW"
    assert result.content_length == len(result.content)
    assert result.required_blocks == ("# Plan", "CTA:")


@pytest.mark.parametrize(
    "validation",
    [
        {"max_length": 8},
        {"required_blocks": ["CTA:"]},
    ],
)
def test_template_engine_rejects_invalid_rendered_content(
    validation: dict[str, object],
) -> None:
    with pytest.raises(TemplateValidationError) as exc_info:
        render_template(
            {
                "template_body": "Body text that fails validation",
                "context": {},
                "validation": validation,
            }
        )

    assert exc_info.value.violations


@pytest.mark.parametrize(
    ("template_body", "context"),
    [
        ("{% include 'secrets.j2' %}", {}),
        ("{{ data.__class__ }}", {"data": {"value": 1}}),
        ("{{ data.items() }}", {"data": {"value": 1}}),
    ],
)
def test_template_engine_rejects_unsafe_templates(
    template_body: str,
    context: dict[str, object],
) -> None:
    with pytest.raises(TemplateSecurityError):
        render_template(
            {
                "template_body": template_body,
                "context": context,
            }
        )


def test_template_engine_rejects_missing_context_keys() -> None:
    with pytest.raises(TemplateRenderError, match="missing"):
        render_template(
            {
                "template_body": "Hello {{ missing }}",
                "context": {},
            }
        )
