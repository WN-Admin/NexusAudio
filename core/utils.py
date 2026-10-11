"""Small shared helpers with no project dependencies."""
import os
import re

_FORBIDDEN_CHARS_RE = re.compile(r'[/\\<>:"|?*\x00-\x1f]')


def sanitize_filename(text, max_length=200):
    """Strip only filesystem-illegal characters, preserve international text.

    Removes path separators as well as reserved characters, so a value taken
    from user input or from file tags can never escape its target directory.
    Leading/trailing dots and spaces are also dropped: they are hidden on some
    platforms and turn a traversal attempt into a stray '....evil' name.
    """
    cleaned = _FORBIDDEN_CHARS_RE.sub('', str(text)).strip()
    cleaned = cleaned.strip('. ').strip()
    if not cleaned:
        cleaned = 'Unknown'
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].strip('. ')
    return cleaned or 'Unknown'


def safe_join(directory, *parts):
    """Join path parts and confirm the result stays inside `directory`.

    Returns None when the result would escape `directory`, so callers can
    refuse the operation instead of writing outside the intended folder.
    """
    base = os.path.abspath(directory)
    target = os.path.abspath(os.path.join(base, *parts))
    if target != base and not target.startswith(base + os.sep):
        return None
    return target