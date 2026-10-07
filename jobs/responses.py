"""One response shape for every API call: {success, message, data, errors}."""

import logging

from django.core.exceptions import ObjectDoesNotExist
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from .services import Denied

log = logging.getLogger(__name__)


def ok(data=None, message="OK", code=status.HTTP_200_OK):
    return Response({"success": True, "message": message, "data": data, "errors": None}, status=code)


def fail(message, errors=None, code=status.HTTP_400_BAD_REQUEST):
    return Response({"success": False, "message": message, "data": None, "errors": errors}, status=code)


def exception_handler(exc, context):
    if isinstance(exc, Denied):
        return fail(str(exc), code=status.HTTP_403_FORBIDDEN)
    if isinstance(exc, ObjectDoesNotExist):
        return fail("Not found.", code=status.HTTP_404_NOT_FOUND)
    response = drf_exception_handler(exc, context)
    if response is None:
        log.exception("Unhandled API error")
        return fail("Something went wrong. Please try again.", code=status.HTTP_500_INTERNAL_SERVER_ERROR)
    if response.status_code == 400:
        return fail("Please check the highlighted fields.", errors=response.data)
    detail = response.data.get("detail", "Request failed.") if isinstance(response.data, dict) else "Request failed."
    return fail(str(detail), code=response.status_code)
