def validate_session_scenes(value, session_count, completed_numbers=()):
    """Validate an ordered list of scene lists, one list per session."""
    if not isinstance(value, list) or len(value) != session_count:
        raise ValueError("Preencha as cenas de cada sessão configurada.")
    normalized = []
    for number, scenes in enumerate(value, 1):
        if number in completed_numbers and scenes == []:
            normalized.append([])
            continue
        if not isinstance(scenes, list) or not 1 <= len(scenes) <= 30:
            raise ValueError(f"Sessão {number}: use entre 1 e 30 cenas.")
        if any(not isinstance(scene, str) or not scene.strip() or len(scene) > 10000 for scene in scenes):
            raise ValueError(f"Sessão {number}: cada cena deve conter entre 1 e 10000 caracteres.")
        normalized.append([scene.strip() for scene in scenes])
    return normalized
