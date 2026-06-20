from __future__ import annotations

import re
from collections import OrderedDict
from collections.abc import Iterator, Mapping
from typing import Final, Self

from jinja2 import StrictUndefined, TemplateError, TemplateSyntaxError, nodes
from jinja2.environment import Template
from jinja2.exceptions import SecurityError
from jinja2.sandbox import SandboxedEnvironment
from pydantic import Field, field_validator, model_validator

from libs.shared.models import JSONValue, SharedBaseModel

DEFAULT_MAX_CONTENT_LENGTH: Final = 4_096
MAX_TEMPLATE_BODY_LENGTH: Final = 50_000

CONTEXT_KEY_PATTERN: Final = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")

SAFE_FILTERS: Final = frozenset(
    {
        "capitalize",
        "default",
        "escape",
        "first",
        "float",
        "int",
        "join",
        "last",
        "length",
        "list",
        "lower",
        "replace",
        "round",
        "sort",
        "string",
        "title",
        "trim",
        "truncate",
        "upper",
        "wordcount",
        "wordwrap",
    }
)
SAFE_TESTS: Final = frozenset(
    {
        "boolean",
        "defined",
        "divisibleby",
        "eq",
        "equalto",
        "escaped",
        "even",
        "false",
        "float",
        "ge",
        "greaterthan",
        "gt",
        "in",
        "integer",
        "iterable",
        "le",
        "lessthan",
        "lower",
        "lt",
        "mapping",
        "ne",
        "none",
        "number",
        "odd",
        "sameas",
        "sequence",
        "string",
        "true",
        "undefined",
        "upper",
    }
)
UNSAFE_NODE_TYPES: Final = (
    nodes.Call,
    nodes.CallBlock,
    nodes.Extends,
    nodes.FromImport,
    nodes.Import,
    nodes.Include,
)


class TemplateEngineError(ValueError):
    """Base error for CGLR template rendering failures."""


class TemplateSecurityError(TemplateEngineError):
    """Raised when a template uses a blocked or unsafe construct."""


class TemplateRenderError(TemplateEngineError):
    """Raised when a safe template cannot be rendered."""


class TemplateValidationError(TemplateEngineError):
    """Raised when rendered content violates validation rules."""

    def __init__(self, violations: tuple[str, ...]) -> None:
        self.violations = violations
        super().__init__("; ".join(violations))


class TemplateValidationRules(SharedBaseModel):
    min_length: int = Field(default=1, ge=0)
    max_length: int = Field(default=DEFAULT_MAX_CONTENT_LENGTH, ge=1)
    required_blocks: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("required_blocks")
    @classmethod
    def _normalize_required_blocks(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(dict.fromkeys(block.strip() for block in value))
        if any(block == "" for block in normalized):
            raise ValueError("required_blocks не должен содержать пустые маркеры")
        return normalized

    @model_validator(mode="after")
    def _validate_length_range(self) -> Self:
        if self.min_length > self.max_length:
            raise ValueError("min_length не может быть больше max_length")
        return self


class TemplateRenderRequest(SharedBaseModel):
    template_body: str = Field(min_length=1, max_length=MAX_TEMPLATE_BODY_LENGTH)
    context: dict[str, JSONValue] = Field(default_factory=dict)
    validation: TemplateValidationRules = Field(default_factory=TemplateValidationRules)

    @field_validator("context")
    @classmethod
    def _validate_context_keys(
        cls,
        value: dict[str, JSONValue],
    ) -> dict[str, JSONValue]:
        invalid_keys = [
            key
            for key in value
            if CONTEXT_KEY_PATTERN.fullmatch(key) is None or _is_private_name(key)
        ]
        if invalid_keys:
            raise ValueError(
                "context содержит небезопасные ключи: "
                + ", ".join(sorted(invalid_keys))
            )
        return value


class TemplateRenderResult(SharedBaseModel):
    content: str
    content_length: int = Field(ge=0)
    required_blocks: tuple[str, ...] = Field(default_factory=tuple)


class TemplateEngine:
    """Sandboxed Jinja2 renderer with explicit output validation."""

    def __init__(self, *, max_cache_size: int = 256) -> None:
        if max_cache_size < 0:
            raise ValueError("max_cache_size не может быть отрицательным")
        self._environment = _create_sandbox_environment()
        self._max_cache_size = max_cache_size
        self._template_cache: OrderedDict[str, Template] = OrderedDict()

    def render(
        self,
        payload: TemplateRenderRequest | Mapping[str, object],
    ) -> TemplateRenderResult:
        request = (
            payload
            if isinstance(payload, TemplateRenderRequest)
            else TemplateRenderRequest.model_validate(payload)
        )

        try:
            template = self._compiled_template(request.template_body)
            content = template.render(request.context)
        except SecurityError as exc:
            raise TemplateSecurityError("шаблон нарушает правила песочницы") from exc
        except (TemplateSyntaxError, TemplateError) as exc:
            raise TemplateRenderError(str(exc)) from exc

        _validate_rendered_content(content, request.validation)
        return TemplateRenderResult(
            content=content,
            content_length=len(content),
            required_blocks=request.validation.required_blocks,
        )

    def _compiled_template(self, template_body: str) -> Template:
        cached = self._template_cache.get(template_body)
        if cached is not None:
            self._template_cache.move_to_end(template_body)
            return cached

        self._validate_template_ast(template_body)
        template = self._environment.from_string(template_body)
        if self._max_cache_size > 0:
            self._template_cache[template_body] = template
            if len(self._template_cache) > self._max_cache_size:
                self._template_cache.popitem(last=False)
        return template

    def _validate_template_ast(self, template_body: str) -> None:
        try:
            ast = self._environment.parse(template_body)
        except TemplateSyntaxError as exc:
            raise TemplateRenderError(str(exc)) from exc

        _reject_unsafe_nodes(ast)


def render_template(
    payload: TemplateRenderRequest | Mapping[str, object] | None = None,
    **kwargs: object,
) -> TemplateRenderResult:
    if payload is not None and kwargs:
        raise ValueError("payload и keyword-аргументы нельзя передавать одновременно")
    if payload is None and not kwargs:
        raise ValueError("нужны входные данные для рендеринга шаблона")

    raw_payload: TemplateRenderRequest | Mapping[str, object]
    raw_payload = kwargs if payload is None else payload
    return _DEFAULT_ENGINE.render(raw_payload)


def _create_sandbox_environment() -> SandboxedEnvironment:
    environment = SandboxedEnvironment(
        autoescape=False,
        lstrip_blocks=True,
        trim_blocks=True,
        undefined=StrictUndefined,
    )
    environment.globals.clear()
    environment.filters = {
        name: filter_callable
        for name, filter_callable in environment.filters.items()
        if name in SAFE_FILTERS
    }
    environment.tests = {
        name: test_callable
        for name, test_callable in environment.tests.items()
        if name in SAFE_TESTS
    }
    return environment


def _reject_unsafe_nodes(ast: nodes.Template) -> None:
    for node in _walk_nodes(ast):
        if isinstance(node, UNSAFE_NODE_TYPES):
            raise TemplateSecurityError(
                "небезопасная конструкция шаблона "
                f"{node.__class__.__name__} ({_node_location(node)})"
            )
        if isinstance(node, nodes.Getattr) and _is_private_name(node.attr):
            raise TemplateSecurityError(
                f"небезопасный доступ к атрибуту {node.attr!r} ({_node_location(node)})"
            )
        if isinstance(node, nodes.Getitem):
            _reject_private_item_access(node)
        if isinstance(node, nodes.Filter) and node.name not in SAFE_FILTERS:
            raise TemplateSecurityError(
                f"фильтр {node.name!r} не разрешён ({_node_location(node)})"
            )
        if isinstance(node, nodes.Test) and node.name not in SAFE_TESTS:
            raise TemplateSecurityError(
                f"проверка {node.name!r} не разрешена ({_node_location(node)})"
            )


def _reject_private_item_access(node: nodes.Getitem) -> None:
    arg = node.arg
    if not isinstance(arg, nodes.Const):
        return
    if isinstance(arg.value, str) and _is_private_name(arg.value):
        raise TemplateSecurityError(
            f"небезопасный доступ к ключу {arg.value!r} ({_node_location(node)})"
        )


def _validate_rendered_content(
    content: str,
    validation: TemplateValidationRules,
) -> None:
    violations: list[str] = []
    stripped_content = content.strip()

    if len(stripped_content) < validation.min_length:
        violations.append(
            "длина результата меньше min_length "
            f"({len(stripped_content)} < {validation.min_length})"
        )
    if len(content) > validation.max_length:
        violations.append(
            "длина результата больше max_length "
            f"({len(content)} > {validation.max_length})"
        )

    missing_blocks = [
        block for block in validation.required_blocks if block not in content
    ]
    if missing_blocks:
        violations.append(
            "отсутствуют обязательные блоки: " + ", ".join(missing_blocks)
        )

    if violations:
        raise TemplateValidationError(tuple(violations))


def _walk_nodes(node: nodes.Node) -> Iterator[nodes.Node]:
    yield node
    for child in node.iter_child_nodes():
        yield from _walk_nodes(child)


def _is_private_name(name: str) -> bool:
    return name.startswith("_") or "__" in name


def _node_location(node: nodes.Node) -> str:
    if node.lineno is None:
        return "строка неизвестна"
    return f"строка {node.lineno}"


_DEFAULT_ENGINE: Final = TemplateEngine()
