"""Validate model choices before they can change the classification state."""
from __future__ import annotations

import logging
from copy import deepcopy
from collections.abc import Awaitable, Callable, Collection
from typing import Annotated, Literal, TypeVar, get_origin

from pydantic import BaseModel, BeforeValidator, Field, ValidationError, create_model

from feap.cascade.backtracker import _infer_target_level
from feap.schemas.codes import normalize_code

T = TypeVar('T', bound=BaseModel)
logger = logging.getLogger(__name__)


def selected_candidate(candidates, field: str, code: str):
    """Candidate order is not a ranking contract; match the actual decision."""
    return next((c for c in candidates if getattr(c, field) == code), None)


def choice_model(output_model: type[T], choices: dict[str, Collection[str]]) -> type[T]:
    """Bind decision fields to this request's actual tree, preserving field order.

    The public schemas remain reusable. The wire schema exposes enum values so
    SGR routes to actual options, rather than merely generating digit strings.
    Local validation below still protects providers that ignore constraints.
    """
    fields = {}
    for name, allowed in choices.items():
        if not allowed:
            raise ValueError(f'No available choices for {name}')
        original = output_model.model_fields[name]
        is_list = get_origin(original.annotation) is list
        # Blank is reserved for an explicit backtrack/clarification response.
        values = tuple(sorted(allowed)) if is_list else ('', *sorted(allowed))
        code_type = Annotated[Literal[values], BeforeValidator(normalize_code)]
        annotation = list[code_type] if is_list else code_type
        info = Field(description=original.description)
        info.metadata = [m for m in original.metadata if not isinstance(m, BeforeValidator)]
        fields[name] = (annotation, info)
        candidate_field = name.removeprefix('selected_')
        candidates_info = output_model.model_fields.get('candidates')
        if not is_list and candidates_info is not None:
            candidate_model = candidates_info.annotation.__args__[0]
            if candidate_field in candidate_model.model_fields:
                candidate_type = Annotated[
                    Literal[tuple(sorted(allowed))], BeforeValidator(normalize_code),
                ]
                candidate_info = deepcopy(candidate_model.model_fields[candidate_field])
                candidate_info.metadata = []
                bound_candidate = create_model(
                    candidate_model.__name__, __base__=candidate_model,
                    **{candidate_field: (candidate_type, candidate_info)},
                )
                fields['candidates'] = (list[bound_candidate], deepcopy(candidates_info))
    return create_model(output_model.__name__, __base__=output_model, **fields)


async def classify_choice(
    *,
    classify_fn: Callable[..., Awaitable[T]],
    choices: dict[str, Collection[str]],
    validate_result: Callable[[T], None] | None = None,
    **kwargs,
) -> T:
    """One bounded repair for malformed/off-tree choices, never a guessed leaf.

    Backtrack and clarification responses may intentionally leave decisions
    blank. No transport retry is added here; the provider owns those retries.
    """
    original_user = kwargs['user_message']
    if 'output_model' in kwargs:
        kwargs['output_model'] = choice_model(kwargs['output_model'], choices)
    for attempt in range(2):
        try:
            result = await classify_fn(**kwargs)
            trigger = getattr(result, 'backtrack_trigger', None)
            if trigger:
                # Handlers return early on backtrack without committing a code.
                # L2 may exclude a group while requesting a section change:
                # apply_backtrack handles this via its existing L2->L1 escalation.
                # Other same/forward-level triggers cannot be executed.
                target = _infer_target_level(trigger)
                group_reselection = target == kwargs['level'] == 2
                if target >= kwargs['level'] and not group_reselection:
                    raise ValueError('Backtrack must target an earlier cascade level')
                return result
            if validate_result:
                validate_result(result)
            for field, allowed in choices.items():
                code = getattr(result, field)
                if not code and getattr(result, 'missing_parameters', None):
                    continue
                values = code if isinstance(code, list) else [code]
                if not values or any(value not in allowed for value in values):
                    raise ValueError(f'{field}={code!r} is not an available code')
            return result
        except (ValidationError, ValueError) as exc:
            if attempt:
                raise
            logger.warning('L%s invalid structured choice; requesting one repair: %s',
                           kwargs['level'], exc)
            allowed_text = '\n'.join(
                f'{field}: {", ".join(sorted(allowed))}' for field, allowed in choices.items()
            )
            kwargs['user_message'] = (
                original_user + '\n\nПРОВЕРКА ФОРМАТА: предыдущий ответ содержал '
                'некорректный код. Повтори анализ и скопируй код из допустимого списка '
                'цифрами без пробелов и пояснений. В candidates используй коды того же '
                'уровня и из того же списка. Не выбирай последний код по умолчанию.\n'
                + allowed_text
                + ('\nОшибка проверки доказательств: ' + str(exc)[:500] if validate_result else '')
                + f'\nЕсли нужен backtrack_trigger, исключаемый код должен относиться '
                f'к уровню строго меньше текущего ({kwargs["level"]}). Нельзя возвращаться '
                'на текущий или следующий уровень. Иначе верни backtrack_trigger=null '
                'и выбери допустимый код.'
            )
    raise AssertionError('unreachable')
