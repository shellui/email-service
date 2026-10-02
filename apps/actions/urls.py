from django.urls import path

from apps.actions.views import (
    ActionDeliveryDetailView,
    ActionDeliveryListView,
    ActionDeliveryRequeueView,
    ActionEventsView,
    ActionRuleDetailView,
    ActionRuleListCreateView,
    ActionRuleRotateSecretView,
    ActionRuleSendTestView,
    EventLogDetailView,
    EventLogListView,
    EventLogRetentionView,
    EventLogTypesView,
)

urlpatterns = [
    path('event-log', EventLogListView.as_view(), name='shellui-admin-event-log'),
    path('event-log/types', EventLogTypesView.as_view(), name='shellui-admin-event-log-types'),
    path('event-log/retention', EventLogRetentionView.as_view(), name='shellui-admin-event-log-retention'),
    path('event-log/<int:pk>', EventLogDetailView.as_view(), name='shellui-admin-event-log-detail'),
    path('events', ActionEventsView.as_view(), name='shellui-admin-actions-events'),
    path('rules', ActionRuleListCreateView.as_view(), name='shellui-admin-actions-rules'),
    path('rules/<int:pk>', ActionRuleDetailView.as_view(), name='shellui-admin-actions-rule-detail'),
    path('rules/<int:pk>/send-test', ActionRuleSendTestView.as_view(), name='shellui-admin-actions-rule-send-test'),
    path(
        'rules/<int:pk>/rotate-secret',
        ActionRuleRotateSecretView.as_view(),
        name='shellui-admin-actions-rule-rotate-secret',
    ),
    path('deliveries', ActionDeliveryListView.as_view(), name='shellui-admin-actions-deliveries'),
    path('deliveries/<uuid:delivery_id>', ActionDeliveryDetailView.as_view(), name='shellui-admin-actions-delivery-detail'),
    path(
        'deliveries/<uuid:delivery_id>/requeue',
        ActionDeliveryRequeueView.as_view(),
        name='shellui-admin-actions-delivery-requeue',
    ),
]
