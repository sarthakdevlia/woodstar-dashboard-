from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction

from jobs.services import Denied
from jobs.stages import OWNER

from .models import User


def _check_password(password, user=None):
    try:
        validate_password(password, user)
    except ValidationError as exc:
        raise Denied(" ".join(exc.messages))


@transaction.atomic
def create_staff(actor, username, name, role, password, phone="", lang="hi"):
    if not actor.is_owner:
        raise Denied("Only the owner can add staff.")
    if User.objects.filter(username__iexact=username).exists():
        raise Denied("That username is already taken.")
    user = User(username=username, name=name, role=role, phone=phone, lang=lang)
    _check_password(password, user)
    user.set_password(password)
    user.save()
    return user


@transaction.atomic
def update_staff(actor, user_id, name=None, role=None, is_active=None, password=None, phone=None, lang=None):
    if not actor.is_owner:
        raise Denied("Only the owner can change staff accounts.")
    user = User.objects.select_for_update().get(pk=user_id)
    losing_owner = user.is_owner and ((role is not None and role != OWNER) or is_active is False)
    # Without this the shop can end up with nobody able to set duties or manage accounts.
    if losing_owner and not User.objects.filter(role=OWNER, is_active=True).exclude(pk=user.pk).exists():
        raise Denied("There must always be at least one active owner.")
    if name is not None:
        user.name = name
    if role is not None:
        user.role = role
    if is_active is not None:
        user.is_active = is_active
    if phone is not None:
        user.phone = phone
    if lang is not None:
        user.lang = lang
    if password:
        _check_password(password, user)
        user.set_password(password)
    user.save()
    return user
