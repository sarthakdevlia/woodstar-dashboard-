from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.utils import timezone

from .catalog import CATEGORIES, SHOP, TEMPLATE_DEFAULTS, item_line
from .models import Job
from .serializers import me_to_dict
from .stages import LANG_CHOICES, ROLE_CHOICES, STAGES

MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 15 * 60


def _client_ip(request):
    # Render's proxy puts the real client first in X-Forwarded-For.
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return forwarded.split(",")[0].strip() or request.META.get("REMOTE_ADDR", "")


class LoginView(auth_views.LoginView):
    """Stops password guessing: five failures for one username from one address locks
    that pair out for fifteen minutes."""

    template_name = "login.html"
    redirect_authenticated_user = True

    def _key(self):
        username = self.request.POST.get("username", "").lower()[:150]
        return f"login-fail:{_client_ip(self.request)}:{username}"

    def post(self, request, *args, **kwargs):
        if cache.get(self._key(), 0) >= MAX_FAILED_LOGINS:
            # An unbound form, so the password is never even checked while locked out.
            context = self.get_context_data(form=self.form_class(request), locked_out=True)
            return self.render_to_response(context, status=429)
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        key = self._key()
        cache.set(key, cache.get(key, 0) + 1, LOCKOUT_SECONDS)
        return super().form_invalid(form)

    def form_valid(self, form):
        cache.delete(self._key())
        return super().form_valid(form)


@login_required
def app(request):
    return render(request, "app.html", {
        "bootstrap": {
            "me": me_to_dict(request.user), "stages": STAGES, "categories": CATEGORIES, "shop": SHOP,
            "templateDefaults": TEMPLATE_DEFAULTS, "roles": ROLE_CHOICES, "langs": LANG_CHOICES,
        },
    })


def track(request, token):
    """What the customer's link opens: no login, their order only, always the live status."""
    job = Job.objects.prefetch_related("ticks", "items").filter(track_token=token).first()
    if not job:
        raise Http404
    ticks = {t.stage: t for t in job.ticks.all()}
    steps, now_found = [], False
    for stage in STAGES:
        tick = ticks.get(stage["key"])
        state = "done" if tick else "coming"
        if not tick and not now_found:
            state, now_found = "now", True
        steps.append({"label": stage["label"], "said": stage["said"], "state": state,
                      "at": timezone.localtime(tick.done_at) if tick else None})
    reached = [s for s in steps if s["state"] == "done"]
    response = render(request, "track.html", {
        "job": job, "steps": steps, "shop": SHOP,
        "complete": len(reached) == len(STAGES),
        "status": reached[-1]["label"] if reached else STAGES[0]["label"],
        "next": next((s["label"] for s in steps if s["state"] == "now"), ""),
        "lines": [item_line(i) for i in job.items.all()],
    })
    response["Cache-Control"] = "no-store"
    return response


def healthz(request):
    return HttpResponse("ok", content_type="text/plain")
