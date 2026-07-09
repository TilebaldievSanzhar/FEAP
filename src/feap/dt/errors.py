"""Доменные исключения ДТ-ядра.

Вынесены отдельно, чтобы service / catalog / classify_link могли делить их
без циклических импортов.
"""

from __future__ import annotations


class NotFoundError(LookupError):
    """Сущность не найдена (или принадлежит другой организации)."""


class NoItemsToClassify(RuntimeError):
    """В досье нет позиций, требующих классификации."""
