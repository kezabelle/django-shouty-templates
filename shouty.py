# -*- coding: utf-8 -*-
from __future__ import absolute_import, unicode_literals

import logging
from collections import namedtuple

try:
    from collections.abc import Sized
except ImportError:
    from collections import Sized
from difflib import get_close_matches

from django.apps import AppConfig
from django.conf import settings
from django.template import Context
from django.template.base import Variable, VariableDoesNotExist, UNKNOWN_SOURCE
from django.template.context import BaseContext
from django.template.defaulttags import URLNode
from django.template.exceptions import TemplateSyntaxError
from django.forms import Form

try:
    from typing import Any, Tuple, Text, Optional, Type, Set, Dict, List
except ImportError:
    pass


__version_info__ = '0.1.4'
__version__ = '0.1.4'
version = '0.1.4'
VERSION = '0.1.4'

def get_version():
    return version

__all__ = ["MissingVariable", "patch", "Shout", "default_app_config", "get_version"]


logger = logging.getLogger(__name__)


class MissingVariable(TemplateSyntaxError):  # type: ignore
    """
    Django will raise TemplateSyntaxError in various scenarios, so this
    subclass is used to differentiate shouty errors, while still getting the
    same functionality.
    """

    pass


old_resolve_lookup = Variable._resolve_lookup
old_url_render = URLNode.render


VARIABLE_BLACKLIST = (
    # When trying to render the technical 500 template, it accesses
    # settings.SETTINGS_MODULE, which ordinarily is there. But if using
    # a UserSettingsHolder, it doesn't seem to be. I've not looked into
    # it fully, but it's a smashing great error that prevents the debug 500
    # from working otherwise.
    # TODO: Get fixed upstream?
    "settings.SETTINGS_MODULE",
    # django-debug-toolbar SQL panel, makes use of a dictionary of queries,
    # whose values aren't always available.
    # TODO: Get fixed upstream?
    "query.starts_trans",
    "query.ends_trans",
    "query.in_trans",
    "query.similar_count",
    "query.similar_color",
    "query.duplicate_count",
    "query.duplicate_color",
    "query.iso_level",
    "query.trans_status",
    # Django admin
    # TODO: Get fixed upstream?
    "model.add_url", # used on the site/app index
    "model.admin_url", # used on the site/app index
    "is_popup",  # Used all over the shop, but not declared everywhere.
    "cl.formset.errors",  # Used on the changelist even if there's no formset?
    "show",  # date_hierarchy for the changelist doesn't actually always return a dictionary ...
    "cl.formset.is_multipart",  # Used on the changelist even if there's no formset?
    "result.form.non_field_errors",  # Used on the changelist ...
    "can_change_related",  # Used by related_widget_wrapper
    "can_add_related",  # Used by related_widget_wrapper
    "can_delete_related",  # Used by related_widget_wrapper
    # Django's technical 500 templates (text & html) via get_traceback_data
    "exception_type",
    "exception_value",
    "lastframe",
    "request_GET_items",
    "request_FILES_items",
    "request_COOKIES_items",
    # Django's "debug" context processor only fills debug and sql_queries if
    # DEBUG=True and the user's IP is in INTERNAL_IPS
    "debug",
    "sql_queries",
)  # type: Tuple[Text, ...]


def variable_blacklist():
    # type: () -> Tuple[Text, ...]
    # TODO: make this memoized/cached?
    return VARIABLE_BLACKLIST + tuple(getattr(settings, 'SHOUTY_VARIABLE_BLACKLIST', ()))


def create_exception_with_template_debug(context, part, exception_cls):
    # type: (Context, Text, Type[TemplateSyntaxError]) -> Tuple[Optional[Text], Optional[Dict[Text, Any]]]
    """
    Between Django 2.0 and Django 3.x at least, painting template exceptions
    can do so via the "wrong" template, which highlights nothing.

    Pretty sure it has also done them wrong in niche cases throughout the 1.x
    lifecycle, as I've got utterly used to ignoring that area of the technical 500.

    Anyway, this method attempts to make up for that for our loud error messages
    by eagerly binding what might hopefully be the right candidate variable/url
    to the exception attribute searched for by Django.

    This is still only going to be correct sometimes, because the same variable
    might appear multiple times in a template (or set of templates) and may also
    appear in both line & block comments which won't actually trigger an exception.

    False positives abound!

    https://code.djangoproject.com/ticket/31478
    https://code.djangoproject.com/ticket/28935
    https://code.djangoproject.com/ticket/27956
    """
    __traceback_hide__ = settings.DEBUG
    faketoken = namedtuple("faketoken", "position")
    if (
        "extends_context" in context.render_context
        and context.render_context["extends_context"]
    ):
        for parent in context.render_context.get("extends_context", []):
            _template, _origin = context.template.engine.find_template(
                parent.template_name, skip=None,
            )

            # Because Django doesn't allow multi-line stuff, we know if we don't
            # see one of those we're probably in a block comment? And if we see
            # #} on this line we're probably in a line comment?
            src = _template.source  # type: Text
            lines = src.splitlines()  # type: List[Text]
            for line in lines:
                if part not in line:
                    continue
                if "}}" not in line and "%}" not in line:
                    continue

                # Line comment wrapping over it.
                active_line_start = line.find(part)  # type: int
                # We need to make sure that the {# and #} appear to the left & right of our part
                if (
                    "{#" in line
                    and "#}" in line
                    and line.find("{#") < active_line_start
                    and line.find("#}") > active_line_start
                ):
                    continue

                line_start = src.find(line)  # type: int
                start = src.find(part, line_start)  # type: int
                if start > -1:
                    end = start + len(part)
                    exc_info = _template.get_exception_info(
                        exception_cls("ignored"), faketoken(position=(start, end))
                    )  # type: Dict[Text, Any]
                    template_name = _origin.template_name  # type: Optional[Text]
                    del _template, _origin, start, end
                    return template_name, exc_info
    return None, None


def new_resolve_lookup(self, context):
    # type: (Variable, Any) -> Any
    """
    Call the original _resolve_lookup method, and if it fails with an exception
    which would ordinarily be suppressed by the Django Template Language,
    instead re-format it and re-raise it as another, uncaught exception type.
    """
    __traceback_hide__ = settings.DEBUG
    try:
        return old_resolve_lookup(self, context)
    except VariableDoesNotExist as e:
        whole_var = self.var
        if whole_var not in variable_blacklist():
            try:
                template_name, exc_info = create_exception_with_template_debug(
                    context, whole_var, MissingVariable
                )
                if not template_name:
                    template_name = UNKNOWN_SOURCE
            except Exception as e2:
                logger.warning(
                    "failed to create template_debug information", exc_info=e2
                )
                # In case my code is terrible, and raises an exception, let's
                # just carry on and let Django try for itself to set up relevant
                # debug info
                template_name = UNKNOWN_SOURCE
                exc_info = None
            bit = e.params[0]  # type: Text
            current = e.params[1]

            if isinstance(current, BaseContext):
                possibilities = set(current.flatten().keys())
            elif hasattr(current, "keys") and callable(current.keys):
                possibilities = set(current.keys())
            elif isinstance(current, Sized) and bit.isdigit():
                possibilities = set(str(x) for x in range(0, len(current)))
            elif isinstance(current, Form):
                possibilities = set(current.fields.keys())
            else:
                possibilities = set()
            possibilities = {x for x in possibilities if not x[0] == "_"} | {
                x for x in dir(current) if not x[0] == "_"
            }

            # maybe you typed csrf_token instead of CSRF_TOKEN or what-have-you.
            # But difflib considers case when calculating close matches.
            # So we'll compare everything lower-case, and convert back...
            # Based on https://stackoverflow.com/q/11384714
            possibilities_mapped = {poss.lower(): poss for poss in possibilities}

            # self.var might be 'request.user.pk' but part might just be 'pk'
            if bit != whole_var:
                msg = "Token '{token}' of '{var}' in template '{template}' does not resolve."
            else:
                msg = "Variable '{token}' in template '{template}' does not resolve."
            # Find close names case-insensitively, and if there are any, map
            # them back to their original case/form (so that "csrf_token"
            # might map back to "CSRF_TOKEN" or "Csrf_Token")
            closest = get_close_matches(bit.lower(), possibilities_mapped.keys())
            if closest:
                closest = [
                    possibilities_mapped[match]
                    for match in closest
                    if match in possibilities_mapped
                ]
            if len(closest) > 1:
                msg += "\nPossibly you meant one of: '{closest_matches}'."
            elif closest:
                msg += "\nPossibly you meant to use '{closest_matches}'."
            msg += "\nYou may silence this by adding '{var}' to settings.SHOUTY_VARIABLE_BLACKLIST"
            msg = msg.format(
                token=bit,
                var=whole_var,
                template=template_name,
                closest_matches="', '".join(closest),
            )
            exc = MissingVariable(msg)
            if context.template.engine.debug and exc_info is not None:
                exc_info["message"] = msg
                exc.template_debug = exc_info
            raise exc
        else:
            # Let the VariableDoesNotExist bubble back up to whereever it's
            # actually suppressed, to avoid having to decide wtf value to
            # return ("", or None?)
            raise


URL_BLACKLIST = (
    # Admin login
    ('admin_password_reset', 'password_reset_url'),
    # Admin header (every page)
    ('django-admindocs-docroot', 'docsroot'),
)  # type: Tuple[Tuple[Text, Text], ...]

def url_blacklist():
    # type: () -> Tuple[Tuple[Text, Text], ...]
    # TODO: make this memoized/cached?
    return URL_BLACKLIST + tuple(getattr(settings, 'SHOUTY_URL_BLACKLIST', ()))


def new_url_render(self, context):
    # type: (URLNode, Any) -> Any
    """
    Call the original render method, and if it returns nothing AND has been
    put into the context, raise an exception.

    eg:
    {% url '...' %} is fine. Will raise NoReverseMatch anyway.
    {% url '...' as x %} is fine if ... resolves.
    {% url '...' as x %} will now blow up if ... doesn't put something sensible
    into the context (it should've thrown a NoReverseMatch)
    """
    __traceback_hide__ = settings.DEBUG
    value = old_url_render(self, context)
    outvar = self.asvar
    if outvar is not None and context[outvar] == "":
        key = (str(self.view_name.var), outvar)
        if key not in url_blacklist():
            try:
                template_name, exc_info = create_exception_with_template_debug(
                    context, outvar, MissingVariable
                )
                if not template_name:
                    template_name = UNKNOWN_SOURCE
            except Exception as e2:
                logger.warning(
                    "failed to create template_debug information", exc_info=e2
                )
                # In case my code is terrible, and raises an exception, let's
                # just carry on and let Django try for itself to set up relevant
                # debug info
                template_name = UNKNOWN_SOURCE
                exc_info = None
            msg = "{{% url {token!s} ... as {asvar!s} %}} in template '{template} did not resolve.\nYou may silence this by adding {key!r} to settings.SHOUTY_URL_BLACKLIST".format(
                token=self.view_name, asvar=outvar, key=key, template=template_name,
            )
            exc = MissingVariable(msg)
            if context.template.engine.debug and exc_info is not None:
                exc_info["message"] = msg
                exc.template_debug = exc_info
            raise exc
    return value


def patch(invalid_variables, invalid_urls):
    # type: (bool, bool) -> bool
    """
    Monkeypatch the Django Template Language's Variable class, replacing
    the `_resolve_lookup` method with `new_resolve_lookup` in this module.

    Also allows for turning on loud errors if using `{% url ... as outvar %}`
    where the url resolved to nothing.

    Calling it multiple times should be a no-op
    """
    patched_var = getattr(Variable, "_shouty", False)
    if invalid_variables is True:
        if patched_var is False:
            Variable._resolve_lookup = new_resolve_lookup
            Variable._shouty = True

    patched_url = getattr(URLNode, "_shouty", False)
    if invalid_urls is True:
        if patched_url is False:
            URLNode.render = new_url_render
            URLNode._shouty = True
    return True


class Shout(AppConfig):  # type: ignore
    """
    Applies the patch automatically if enabled.
    If `shouty` or `shouty.Shout` is added to INSTALLED_APPS only.
    """
    name = "shouty"

    def ready(self):
        # type: () -> bool
        logger.info("Applying shouty templates patch")
        return patch(
            invalid_variables=getattr(settings, "SHOUTY_VARIABLES", True),
            invalid_urls=getattr(settings, "SHOUTY_URLS", True),
        )


default_app_config = "shouty.Shout"


if __name__ == "__main__":
    from django.test import TestCase
    from django.test.runner import DiscoverRunner
    import django
    from django.conf import settings as test_settings

    test_settings.configure(
        DATABASES={
            "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}
        },
        INSTALLED_APPS=(
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "django.contrib.messages",
            "django.contrib.admin",
            "shouty",
        ),
        MIDDLEWARE=(
            "django.contrib.auth.middleware.AuthenticationMiddleware",
            "django.contrib.messages.middleware.MessageMiddleware",
            "django.contrib.sessions.middleware.SessionMiddleware",
        ),
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [],
                "APP_DIRS": True,
                "OPTIONS": {
                    "context_processors": (
                        "django.contrib.auth.context_processors.auth",
                        "django.contrib.messages.context_processors.messages",
                    )
                },
            },
        ],
        SHOUTY_VARIABLES=True,
        SHOUTY_URLS=True,
    )
    django.setup()
    from django.template import Template as TMPL, Context as CTX
    from django.forms import IntegerField

    class BasicUsageTestCase(TestCase):  # type: ignore
        def setUp(self):
            # type: () -> None
            from shouty import MissingVariable

            self.MissingVariable = MissingVariable

        def test_most_basic(self):
            # type: () -> None
            t = TMPL(
                """
                this works: {{ a }}
                this does not work: {{ b }}
                """
            )
            exc = (
                "Variable 'b' in template '<unknown source>' does not resolve.\n"
                "Possibly you meant to use 'be'.\n"
                "You may silence this by adding 'b' to settings.SHOUTY_VARIABLE_BLACKLIST"
            )
            with self.assertRaisesMessage(self.MissingVariable, exc):
                t.render(CTX({"a": 1, "be": 2}))

        def test_nested_tokens_on_dict(self):
            # type: () -> None
            t = TMPL(
                """
                this works: {{ a }}
                this works: {{ a.b }}
                this does not work: {{ a.b.c }}
                """
            )
            exc = (
                "Token 'c' of 'a.b.c' in template '<unknown source>' does not resolve.\n"
                "Possibly you meant to use 'cd'.\n"
                "You may silence this by adding 'a.b.c' to settings.SHOUTY_VARIABLE_BLACKLIST"
            )
            with self.assertRaisesMessage(self.MissingVariable, exc):
                t.render(CTX({"a": {"b": {"cd": 1}}}))

        def test_nested_tokens_on_namedtuple(self):
            # type: () -> None
            t = TMPL(
                """
                this works: {{ a }}
                this works: {{ a.b }}
                this does not work: {{ a.b.c }}
                """
            )
            nt = namedtuple("nt", "cd ce cf cg")(cd=1, ce=2, cf=3, cg=4)  # type: ignore
            exc = (
                "Token 'c' of 'a.b.c' in template '<unknown source>' does not resolve.\n"
                "Possibly you meant one of: 'cg', 'cf', 'ce'.\n"
                "You may silence this by adding 'a.b.c' to settings.SHOUTY_VARIABLE_BLACKLIST"
            )
            with self.assertRaisesMessage(self.MissingVariable, exc):
                t.render(CTX({"a": {"b": nt}}))

        def test_index(self):
            # type: () -> None
            t = TMPL(
                """
                this works: {{ a }}
                this does not work: {{ a.11 }}
                """
            )
            exc = (
                "Token '11' of 'a.11' in template '<unknown source>' does not resolve.\n"
                "Possibly you meant to use '1'.\n"
                "You may silence this by adding 'a.11' to settings.SHOUTY_VARIABLE_BLACKLIST"
            )
            with self.assertRaisesMessage(self.MissingVariable, exc):
                t.render(CTX({"a": (1, 2)}))

        def test_nested_templates(self):
            # type: () -> None
            t = TMPL(
                """
                this works: {{ a }}
                but this won't: {% include subtemplate %}
                """
            )
            st = TMPL(
                """
                this works: {{ b }}
                this won't work: {{ c }}
                """
            )
            exc = (
                "Variable 'c' in template '<unknown source>' does not resolve.\n"
                "You may silence this by adding 'c' to settings.SHOUTY_VARIABLE_BLACKLIST"
            )
            with self.assertRaisesMessage(self.MissingVariable, exc):
                t.render(CTX({"a": 1, "subtemplate": st, "b": 2}))

        def test_form_possibilities(self):
            # type: () -> None
            t = TMPL(
                """
                {{ form.exampl }}
                """
            )

            class MyForm(Form):  # type: ignore
                example = IntegerField()

            exc = (
                "Token 'exampl' of 'form.exampl' in template '<unknown source>' does not resolve.\n"
                "Possibly you meant to use 'example'.\n"
                "You may silence this by adding 'form.exampl' to settings.SHOUTY_VARIABLE_BLACKLIST"
            )
            with self.assertRaisesMessage(self.MissingVariable, exc):
                t.render(CTX({"form": MyForm(data={"example": "1"})}))

        def test_model_possibilities(self):
            # type: () -> None
            t = TMPL(
                """
                {{ obj.object_i }}
                """
            )
            from django.contrib.admin.models import LogEntry
            from django.contrib.auth.models import User
            from django.contrib.contenttypes.models import ContentType

            user = User.objects.create()
            example = LogEntry.objects.create(
                user=user,
                content_type=ContentType.objects.get_for_model(user),
                object_id=str(user.pk),
                object_repr=str(user),
                action_flag=1,
                change_message="",
            )

            exc = (
                "Token 'object_i' of 'obj.object_i' in template '<unknown source>' does not resolve.\n"
                "Possibly you meant one of: 'object_id', 'objects', 'object_repr'.\n"
                "You may silence this by adding 'obj.object_i' to settings.SHOUTY_VARIABLE_BLACKLIST"
            )
            with self.assertRaisesMessage(self.MissingVariable, exc):
                t.render(CTX({"obj": example}))

        def test_model_related_possibilities(self):
            # type: () -> None
            t = TMPL(
                """
                {{ obj.logentry_se.all }}
                """
            )
            from django.contrib.admin.models import LogEntry
            from django.contrib.auth.models import User
            from django.contrib.contenttypes.models import ContentType

            user = User.objects.create()

            exc = (
                "Token 'logentry_se' of 'obj.logentry_se.all' in template '<unknown source>' does not resolve.\n"
                "Possibly you meant to use 'logentry_set'.\n"
                "You may silence this by adding 'obj.logentry_se.all' to settings.SHOUTY_VARIABLE_BLACKLIST"
            )
            with self.assertRaisesMessage(self.MissingVariable, exc):
                t.render(CTX({"obj": user}))

    class MyPyTestCase(TestCase):  # type: ignore
        def test_for_types(self):
            # type: () -> None
            try:
                from mypy import api as mypy
                import os
            except ImportError:
                return
            else:
                here = os.path.abspath(__file__)
                report, errors, exit_code = mypy.run(
                    ["--strict", "--ignore-missing-imports", here]
                )
                if errors:
                    self.fail(errors)
                elif exit_code > 0:
                    self.fail(report)

    test_runner = DiscoverRunner(interactive=False, verbosity=2)

    failures = test_runner.run_tests(
        test_labels=(),
        extra_tests=(
            test_runner.test_loader.loadTestsFromTestCase(BasicUsageTestCase),
            test_runner.test_loader.loadTestsFromTestCase(MyPyTestCase),
        ),
    )
