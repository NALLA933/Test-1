from typing import Any, Dict, Iterable, List, Optional


def normalize_character_id(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None

    if isinstance(value, int):
        return value

    if isinstance(value, float) and value.is_integer():
        return int(value)

    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned.isdigit():
            return int(cleaned)

    return None


def format_character_id(value: Any) -> str:
    normalized = normalize_character_id(value)
    if normalized is None:
        return str(value)

    width = max(3, len(str(normalized)))
    return f"{normalized:0{width}d}"


def character_id_variants(value: Any) -> List[Any]:
    normalized = normalize_character_id(value)
    if normalized is None:
        return [value] if value not in (None, "") else []

    variants: List[Any] = []
    for candidate in (normalized, str(normalized), format_character_id(normalized)):
        if candidate not in variants:
            variants.append(candidate)
    return variants


def expand_character_id_variants(values: Iterable[Any]) -> List[Any]:
    expanded: List[Any] = []
    for value in values:
        for candidate in character_id_variants(value):
            if candidate not in expanded:
                expanded.append(candidate)
    return expanded


def character_id_filter(value: Any) -> Any:
    variants = character_id_variants(value)
    if not variants:
        return value
    if len(variants) == 1:
        return variants[0]
    return {"$in": variants}


def character_id_query(value: Any, field: str = "id") -> Dict[str, Any]:
    return {field: character_id_filter(value)}


def character_matches_id(character: Dict[str, Any], value: Any) -> bool:
    return normalize_character_id(character.get("id")) == normalize_character_id(value)


def normalize_character_document(character: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(character)
    character_id = normalize_character_id(character.get("id"))
    if character_id is not None:
        normalized["id"] = character_id
    return normalized
