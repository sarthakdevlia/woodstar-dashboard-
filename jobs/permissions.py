"""Who may change which step. Enforced here on the server; the browser only mirrors
these rules to grey out buttons, so editing the page changes nothing."""

from .stages import STAGE_KEYS, STAGE_LABELS


def check_tick(is_owner, duties, stage, done, on_duty=()):
    """Return None if this person may flip `stage`, else the reason they may not.

    `duties` are the steps they hold today, `done` the steps already complete on the job,
    `on_duty` the names of whoever does hold `stage` today (for the message only).
    """
    idx = STAGE_KEYS.index(stage)
    label = STAGE_LABELS[stage]
    if not is_owner and stage not in duties:
        whose = f"{' or '.join(on_duty)}'s" if on_duty else "nobody's"
        return f'"{label}" is {whose} duty today, not yours.'
    if stage in done:
        if not is_owner:
            return "Only the owner can undo a completed step."
        if any(key in done for key in STAGE_KEYS[idx + 1:]):
            return "Undo the later steps first."
        return None
    if idx > 0 and STAGE_KEYS[idx - 1] not in done:
        return f'"{STAGE_LABELS[STAGE_KEYS[idx - 1]]}" has to be done first.'
    return None


def check_chats(is_owner, duties):
    """Customer chats are the counter's work: the owner, or whoever is on Order received."""
    if is_owner or STAGE_KEYS[0] in duties:
        return None
    return f'Customer chats are answered by the owner, or by whoever has "{STAGE_LABELS[STAGE_KEYS[0]]}" as their duty today.'


def check_create(is_owner, duties):
    """Orders are written at the counter: by the owner, or whoever is on Order received."""
    if is_owner or STAGE_KEYS[0] in duties:
        return None
    return f'Orders are created by whoever has "{STAGE_LABELS[STAGE_KEYS[0]]}" as their duty today, or by the owner.'
