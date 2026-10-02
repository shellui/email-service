from drf_spectacular.extensions import OpenApiAuthenticationExtension


class ShelluiAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = 'apps.authapi.service_auth.ShelluiAuthentication'
    name = 'bearerAuth'

    def get_security_definition(self, auto_schema):
        return {
            'type': 'http',
            'scheme': 'bearer',
            'bearerFormat': 'JWT or service key',
            'description': (
                'Identity-service JWT (`Bearer <token>`) for admin calls, '
                'or a service API key (`Bearer esk_<token>`) for service calls.'
            ),
        }
