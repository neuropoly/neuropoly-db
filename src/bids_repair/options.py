"""Parsing shared explicit repair inputs independently of either CLI."""


def parse_suffix_aliases(values: list[str] | tuple[str, ...] | None) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for value in values or ():
        source, separator, target = value.partition("=")
        source, target = source.strip(), target.strip()
        if not separator or not source or not target:
            raise ValueError(
                f"Invalid suffix alias '{value}'. Use --suffix-alias OLD=NEW."
            )
        if source in aliases and aliases[source] != target:
            raise ValueError(
                f"Conflicting suffix aliases for '{source}': "
                f"'{aliases[source]}' and '{target}'."
            )
        aliases[source] = target
    return aliases
