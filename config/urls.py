from django.contrib.auth import views as auth_views
from django.urls import include, path

from jobs import api, views

api_v1 = [
    path("state/", api.state),
    path("jobs/", api.jobs),
    path("jobs/<int:number>/", api.job),
    path("jobs/<int:number>/stages/<str:stage>/", api.job_stage),
    path("jobs/<int:number>/messages/", api.job_message),
    path("roster/", api.roster),
    path("roster/sent/", api.roster_sent),
    path("templates/<str:key>/<str:lang>/", api.template),
    path("whatsapp/templates/<str:key>/<str:lang>/submit/", api.whatsapp_submit),
    path("whatsapp/refresh/", api.whatsapp_refresh),
    path("whatsapp/replies/", api.whatsapp_replies),
    path("staff/", api.staff),
    path("staff/<int:user_id>/", api.staff_member),
]

urlpatterns = [
    path("", views.app, name="app"),
    path("login/", views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("t/<str:token>/", views.track, name="track"),
    path("webhooks/whatsapp/", views.whatsapp_webhook),
    path("healthz", views.healthz),
    path("api/v1/", include(api_v1)),
]
