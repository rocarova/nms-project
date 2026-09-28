"""Line-based comparison and display helpers for network device configurations."""
import difflib


def line_kind(text):
    """Rough config structure for display: section headers, indented children, comments and blanks."""
    if not text.strip():
        return 'blank'
    if text.lstrip().startswith(('!', '#')):
        return 'comment'
    if text[0] in ' \t':
        return 'child'
    return 'section'


def numbered_lines(config):
    return [{'n': i, 'text': text, 'kind': line_kind(text)} for i, text in enumerate(config.splitlines(), 1)]


def diff_rows(old_config, new_config, context=3):
    """Compares two configs line by line.

    Returns (rows, added, removed). Each row is a dict with kind 'same' | 'add' | 'del' | 'hunk', the old and
    new line numbers, and the text. context=None returns the whole file instead of only changed regions.
    """
    old_lines, new_lines = old_config.splitlines(), new_config.splitlines()
    # autojunk=False: configs repeat lines like "!" hundreds of times, which the heuristic would ignore
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    groups = [matcher.get_opcodes()] if context is None else list(matcher.get_grouped_opcodes(context))

    rows, added, removed = [], 0, 0
    for group in groups:
        if context is not None:
            i1, i2, j1, j2 = group[0][1], group[-1][2], group[0][3], group[-1][4]
            rows.append({'kind': 'hunk', 'text': f'@@ -{i1 + 1},{i2 - i1} +{j1 + 1},{j2 - j1} @@'})
        for tag, i1, i2, j1, j2 in group:
            if tag == 'equal':
                for k in range(i2 - i1):
                    rows.append({'kind': 'same', 'old': i1 + k + 1, 'new': j1 + k + 1, 'text': old_lines[i1 + k]})
                continue
            if tag in ('delete', 'replace'):
                for k in range(i1, i2):
                    rows.append({'kind': 'del', 'old': k + 1, 'new': None, 'text': old_lines[k]})
                removed += i2 - i1
            if tag in ('insert', 'replace'):
                for k in range(j1, j2):
                    rows.append({'kind': 'add', 'old': None, 'new': k + 1, 'text': new_lines[k]})
                added += j2 - j1

    if context is not None and not added and not removed:
        rows = []  # Identical: nothing to show in "changes only" mode
    return rows, added, removed
