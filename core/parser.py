import re

# Blocs à supprimer : intros superflues uniquement
_RE_VERBOSE_BLOCKS = re.compile(
    r'(?:'
    r'(?:Voici|Je vois que|Vous avez déjà|Ce que vous pouvez faire maintenant|N\'hésitez pas|Vous pouvez aussi)'
    r'[^.]*[.:]\s*\n*'
    r')',
    re.IGNORECASE,
)

_RE_TABLE_ROW = re.compile(r'^\s*\|.+\|')
_RE_TABLE_SEP = re.compile(r'^\s*\|[-:\s]+\|')
_RE_HEADER_CHECKS = re.compile(
    r'(?:Vérification rapide|Étapes? suivantes?|Pour aller plus loin|Ce que vous pouvez faire)\s*:?\s*\n*',
    re.IGNORECASE,
)
_RE_MULTI_NL = re.compile(r'\n{3,}')
_RE_TRAIL_SPACE = re.compile(r'[ \t]+$', re.MULTILINE)

# Détection de JSON/code : on ne nettoie pas le contenu dans un bloc de code ou JSON
_RE_CODE_BLOCK = re.compile(r'```[\s\S]*?```')
_RE_INLINE_JSON = re.compile(r'^\s*[\[{]')


def _looks_like_structured(text: str) -> bool:
    """Retourne True si le texte ressemble à du JSON ou du code — on ne touche pas aux listes."""
    stripped = text.strip()
    return bool(_RE_INLINE_JSON.match(stripped))


def clean(text: str, aggressive: bool = True) -> str:
    if not text:
        return text

    # Ne pas nettoyer si c'est du JSON brut
    if _looks_like_structured(text):
        return text.strip()

    if aggressive:
        text = _RE_VERBOSE_BLOCKS.sub('', text)
        text = _RE_HEADER_CHECKS.sub('', text)

    lines = text.split('\n')
    out: list[str] = []

    in_table = False
    in_code_block = False
    for line in lines:
        stripped = line.strip()

        # Respecter les blocs de code : ne rien supprimer dedans
        if stripped.startswith('```'):
            in_code_block = not in_code_block
            out.append(line)
            continue
        if in_code_block:
            out.append(line)
            continue

        if _RE_TABLE_SEP.match(line) or (in_table and _RE_TABLE_ROW.match(line)):
            in_table = True
            continue
        if _RE_TABLE_ROW.match(line):
            in_table = True
            continue
        if in_table and not stripped:
            in_table = False
            continue
        in_table = False

        # NOTE: on ne supprime plus les listes numérotées (1. 2. 3.)
        # Elles sont utiles dans les réponses et critiques dans le JSON du plan.
        # L'ancienne ligne était : if aggressive and _RE_STEP_NUM.match(stripped): continue

        out.append(line)

    text = '\n'.join(out)
    text = _RE_TRAIL_SPACE.sub('', text)
    text = _RE_MULTI_NL.sub('\n\n', text)
    return text.strip()


def _truncate(text: str, max_chars: int = 2000) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(' ', 1)[0] + '…'


def compress(text: str, max_chars: int | None = None) -> str:
    text = clean(text)
    text = re.sub(r'\s+', ' ', text)
    if max_chars:
        text = _truncate(text, max_chars)
    return text.strip()
