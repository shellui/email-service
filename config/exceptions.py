"""DRF exception handler: stable error_code, never translated sentences."""

from __future__ import annotations

import logging

from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)

_STATUS_CODES = {
    400: 'validation_failed',
    401: 'unauthorized',
    403: 'forbidden',
    404: 'not_found',
    405: 'method_not_allowed',
    409: 'conflict',
    429: 'rate_limited',
}


def exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is None:
        return None

    request = context.get('request')
    request_id = getattr(request, 'request_id', None) if request is not None else None
    data = response.data

    if isinstance(data, dict) and 'error_code' in data:
        if request_id:
            data.setdefault('request_id', request_id)
        response.data = data
        return response

    body: dict = {
        'error_code': _STATUS_CODES.get(response.status_code, 'request_failed'),
    }
    if isinstance(data, dict):
        field_errors = {}
        for key, value in data.items():
            if key in {'detail', 'non_field_errors'}:
                continue
            if isinstance(value, list):
                field_errors[key] = ['invalid' for _ in value]
            elif isinstance(value, dict):
                field_errors[key] = ['invalid']
        if field_errors:
            body['error_code'] = 'validation_failed'
            body['field_errors'] = field_errors
    if request_id:
        body['request_id'] = request_id
    response.data = body

    if response.status_code >= 500:
        method = getattr(request, 'method', '?')
        path = getattr(request, 'path', '?')
        logger.error(
            '%s %s failed with %s (%s): %s',
            method,
            path,
            response.status_code,
            type(exc).__name__,
            exc,
        )
    return response
