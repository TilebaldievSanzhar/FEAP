"""A rejected group must restart section selection, never advance with no group."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

from feap.cascade.levels.base import CascadeState
from feap.cascade.orchestrator import CascadeOrchestrator
from feap.cascade.selection import classify_choice
from feap.schemas.common import BacktrackTrigger


async def test_group_rejection_reaches_escalation_and_next_level_has_a_group(monkeypatch):
    import feap.cascade.orchestrator as module
    state = CascadeState('Стоматологическое кресло')
    state.section = 'XVIII'
    state.add_exclusion(2, '90')
    trigger = BacktrackTrigger(target_level=2, excluded_code='90', reason='Нужен другой раздел')
    calls = []

    async def section(s, db):
        calls.append('section')
        s.section = 'XX'
        return SimpleNamespace()

    async def group(s, db):
        calls.append('group')
        if s.section == 'XVIII':
            return await classify_choice(
                classify_fn=AsyncMock(return_value=SimpleNamespace(
                    selected_group='', backtrack_trigger=trigger)),
                choices={'selected_group': {'91', '92'}}, level=2, user_message='Кресло',
            )
        s.group = '94'
        return SimpleNamespace()

    async def position(s, db):
        calls.append('position')
        assert s.group == '94'
        return SimpleNamespace()

    monkeypatch.setattr(module, 'LEVELS', [
        SimpleNamespace(level=1, run=section), SimpleNamespace(level=2, run=group),
        SimpleNamespace(level=3, run=position),
    ])
    monkeypatch.setattr(module, 'validate_and_build_result', AsyncMock(return_value='completed'))
    result = await CascadeOrchestrator()._run_cascade(state, None, start_level_idx=1)
    assert result == 'completed'
    assert calls == ['group', 'section', 'group', 'position']
    assert state.get_excluded(1) == ['XVIII']
