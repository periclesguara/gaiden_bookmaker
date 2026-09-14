"""Django configuration and persistence boundary for per-session QA."""
from dataclasses import asdict
import json

from gaiden.writer_engine.validation import SceneContract
from gaiden.writer_engine.repair import generate_validated_scene
from .normalization import writer_storage_root


def language_code(value):
    raw = value.strip().casefold()
    aliases = {'english':'en', 'inglês':'en', 'português':'pt', 'portuguese':'pt',
        'spanish':'es', 'español':'es', 'espanhol':'es', 'french':'fr', 'français':'fr',
        'german':'de', 'deutsch':'de', 'italian':'it', 'italiano':'it'}
    for name, code in aliases.items():
        if raw.startswith(name):
            return code
    return raw.split('(')[0].strip().replace('_','-').split('-')[0]


def scene_contract(chapter, number, plan):
    config = writer_storage_root()/'contracts'/f'project-{chapter.project_id}.json'
    profile = json.loads(config.read_text(encoding='utf-8')) if config.exists() else {}
    if not isinstance(profile, dict) or set(profile)-{'schema_version','defaults','sessions'}:
        raise ValueError('Invalid Writer contract profile')
    if profile and profile.get('schema_version') != 1:
        raise ValueError('Unsupported Writer contract profile')
    values = {'purpose': '\n'.join(plan) or chapter.script,
        'expected_language': language_code(chapter.project.language),
        'min_words': 400, 'max_words': min(12000, max(400, round(chapter.words_per_session*1.6))),
        'pov':'FOLLOW_PROJECT', 'setting_year': None,
        'style':'Follow the project character bibles, direction and narrative point of view.'}
    values.update(profile.get('defaults', {}))
    values.update(profile.get('sessions', {}).get(f'{chapter.number}:{number}', {}))
    if values['expected_language'] != language_code(chapter.project.language):
        raise ValueError('Contract language conflicts with project language')
    return SceneContract(**values)


def generate_validated_session(
    chapter, number, engine, request, contract, previous_text, *, original_text=None
):
    previous = chapter.sessions.filter(status='COMPLETE', number__lt=number).order_by('-number').first()
    previous_state = previous.generation_parameters.get('continuity', {}) if previous else {}
    return generate_validated_scene(engine, request, contract,
        previous_text=previous_text, previous_state=previous_state,
        top_k=chapter.retrieval_top_k,
        session_root=writer_storage_root()/f'project-{chapter.project_id}'/f'chapter {chapter.number:02d}'/f'session {number:02d}',
        original_text=original_text)


def contract_instructions(contract):
    return ('\n\nMANDATORY SCENE CONTRACT (takes precedence over a general outline):\n'
        + json.dumps(asdict(contract), ensure_ascii=False)
        + '\nWrite only the narrative in the contract language. Meet the length bounds, '
          'finish the last sentence, preserve POV, and never anticipate prohibited events. '
          'Keep every sentence at 60 words or fewer. End naturally before the output limit.')
