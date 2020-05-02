# -*- coding: utf-8 -*-
from __future__ import absolute_import, unicode_literals

import logging
from collections import namedtuple

from django.core import checks
from django.utils.encoding import force_text

try:
    from collections.abc import Sized
except ImportError:
    from collections import Sized
from difflib import get_close_matches

from django.apps import AppConfig
from django.conf import settings
from django.template import Context
from django.template.base import (
    Variable,
    VariableDoesNotExist,
    UNKNOWN_SOURCE,
    Template,
    Origin,
)
from django.template.context import BaseContext
from django.template.defaulttags import URLNode, IfNode, TemplateLiteral
from django.template.exceptions import TemplateSyntaxError
from django.forms import Form

try:
    from typing import (
        Any,
        Tuple,
        Text,
        Optional,
        Type,
        Set,
        Dict,
        List,
        Iterator,
        Union,
    )
except ImportError:
    pass


__version_info__ = "0.1.4"
__version__ = "0.1.4"
version = "0.1.4"
VERSION = "0.1.4"


def get_version():
    # type: () -> Text
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
old_if_render = IfNode.render


VARIABLE_BLACKLIST = {
    # When trying to render the technical 500 template, it accesses
    # settings.SETTINGS_MODULE, which ordinarily is there. But if using
    # a UserSettingsHolder, it doesn't seem to be. I've not looked into
    # it fully, but it's a smashing great error that prevents the debug 500
    # from working otherwise.
    # TODO: Get fixed upstream?
    "settings.SETTINGS_MODULE": ("*",),
    # django-debug-toolbar SQL panel, makes use of a dictionary of queries,
    # whose values aren't always available.
    # TODO: Get fixed upstream?
    "query.starts_trans": ("debug_toolbar/panels/sql.html",),
    "query.ends_trans": ("debug_toolbar/panels/sql.html",),
    "query.in_trans": ("debug_toolbar/panels/sql.html",),
    "query.similar_count": ("debug_toolbar/panels/sql.html",),
    "query.similar_color": ("debug_toolbar/panels/sql.html",),
    "query.duplicate_count": ("debug_toolbar/panels/sql.html",),
    "query.duplicate_color": ("debug_toolbar/panels/sql.html",),
    "query.iso_level": ("debug_toolbar/panels/sql.html",),
    "query.trans_status": ("debug_toolbar/panels/sql.html",),
    # Django admin
    # TODO: Get fixed upstream?
    "model.add_url": (
        "admin/index.html",
        "admin/app_index.html",
    ),  # used on the site/app index
    "model.admin_url": (
        "admin/index.html",
        "admin/app_index.html",
    ),  # used on the site/app index
    "is_popup": ("*",),  # Used all over the shop: (), but not declared everywhere.
    "cl.formset.errors": (
        "admin/change_list.html",
    ),  # Used on the changelist even if there's no formset?
    "show": (
        "*",
    ),  # date_hierarchy for the changelist doesn't actually always return a dictionary ...
    "cl.formset.is_multipart": (
        "admin/change_list.html",
    ),  # Used on the changelist even if there's no formset?
    "result.form.non_field_errors": (
        "admin/change_list_results.html",
    ),  # Used on the changelist ...
    "can_change_related": ("*",),  # Used by related_widget_wrapper
    "can_add_related": ("*",),  # Used by related_widget_wrapper
    "can_delete_related": ("*",),  # Used by related_widget_wrapper
    # Django's technical 500 templates (text & html) via get_traceback_data
    "exception_type": ("*",),
    "exception_value": ("*",),
    "lastframe": ("*",),
    "request_GET_items": ("*",),
    "request_FILES_items": ("*",),
    "request_COOKIES_items": ("*",),
    # Django's "debug" context processor only fills debug and sql_queries if
    # DEBUG=True and the user's IP is in INTERNAL_IPS
    "debug": ("*",),
    "sql_queries": ("*",),
}  # type: Dict[str, Tuple[Text,...]]

IF_VARIABLE_BLACKLIST = {
    # When trying to render the technical 404 template, {% if forloop.last and pat.name %} gets used
    # in file <unknown source>
    "pat.name": (UNKNOWN_SOURCE,),
    # Accessing admindocs index will trigger this: {% if title %}<h1>{{ title }}</h1>{% endif %}
    # in file admin_doc/index.html and every subpage therein...
    "title": (
        "admin_doc/index.html",
        "admin_doc/template_tag_index.html",
        "admin_doc/template_filter_index.html",
        "admin_doc/model_index.html",
        "admin_doc/model_detail.html",
        "admin_doc/view_detail.html",
        "admin_doc/bookmarklets.html",
    ),
    # accessing admindocs detailed model information has: {% if field.help_text %} - ...
    # in file admin_doc/model_detail.html
    "field.help_text": ("admin_doc/model_detail.html",),
    # accessing admindocs views will have this in file admin_doc/view_index.html
    "view.title": ("admin_doc/view_index.html",),
    # accessing admindocs views detail will have these in file admin_doc/view_detail.html
    "meta.Context": ("admin_doc/view_detail.html",),
    "meta.Templates": ("admin_doc/view_detail.html",),
}  # type: Dict[str, Tuple[Text,...]]


def variable_blacklist():
    # type: () -> Dict[Text, List[Text]]
    # TODO: make this memoized/cached?
    variables_by_template = {}  # type: Dict[Text, List[Text]]
    for var, templates in VARIABLE_BLACKLIST.items():
        variables_by_template.setdefault(var, [])
        variables_by_template[var].extend(templates)
    for var, templates in IF_VARIABLE_BLACKLIST.items():
        variables_by_template.setdefault(var, [])
        variables_by_template[var].extend(templates)
    user_blacklist = getattr(settings, "SHOUTY_VARIABLE_BLACKLIST", ())
    if hasattr(user_blacklist, "items") and callable(user_blacklist.items):
        for var, templates in user_blacklist.items():
            variables_by_template.setdefault(var, [])
            variables_by_template[var].extend(templates)
    else:
        for var in user_blacklist:
            variables_by_template.setdefault(var, [])
            variables_by_template[var].append("*")
    return variables_by_template


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

    contexts_to_search = []  # type: List[Union[Template, Origin]]
    all_potential_contexts = []  # type: List[Union[Template, Origin]]
    render_context = context.render_context
    # Prefer extends origins
    if "extends_context" in render_context and render_context["extends_context"]:
        all_potential_contexts.extend(render_context.get("extends_context", []))
    # Who knows which order might be right for context or render context? not me.
    all_potential_contexts.append(render_context.template)
    all_potential_contexts.append(context.template)
    render_context_flat = render_context.flatten()
    # Inclusion nodes put their template into the context with themselves as a key.
    for k in render_context_flat:
        if isinstance(render_context_flat[k], Template):
            all_potential_contexts.append(render_context_flat[k])

    for ctx in all_potential_contexts:
        if ctx not in contexts_to_search:
            contexts_to_search.append(ctx)

    assert len(contexts_to_search) <= len(all_potential_contexts)

    for parent in contexts_to_search:
        if isinstance(parent, Origin):
            _template, _origin = context.template.engine.find_template(
                parent.template_name, skip=None,
            )
        else:
            _template = parent
            _origin = parent.origin

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
        blacklist = variable_blacklist()
        whitelisted = blacklist.get(whole_var, [])
        not_blacklisted = whole_var not in blacklist
        whitelisted_by_template = len(whitelisted) > 0
        if not_blacklisted or whitelisted_by_template:
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
            if template_name not in whitelisted and "*" not in whitelisted:
                raise exc
        else:
            # Let the VariableDoesNotExist bubble back up to whereever it's
            # actually suppressed, to avoid having to decide wtf value to
            # return ("", or None?)
            raise


def new_if_render(self, context):
    # type: (IfNode, Context) -> Any
    """
    Simple if cases in the template, like {% if x %}{% endif %} are already caught
    by new_resolve_lookup it seems, but more complex cases like {% if x and y or z %}
    are not, because of the way those conditionals are stacked up and nested.
    Worse still, they're dynamically generated Operator classes which do not
    exist at module scope, and where they do exist on the module scope, it's
    as a closure over a lambda, so patching the eval method is pain.

    Instead if we got back a falsy value (which is "" - no output to render)
    we go to some lengths to extract all the individual nodes of all the conditions
    (nested or otherwise) and error for any that don't exist, if the exception
    is an instance of our special subclass.

    {% if x %} should be fine.
    {% if x and y %} will error on y if it is not in the context.
    {% if x or y and z and 1 == 2 %} would error on z if it's not in the context, regardless of evaluation result.
    """

    __traceback_hide__ = settings.DEBUG
    result = old_if_render(self, context)
    if result == "":
        conditions_seen = set()  # type: Set[TemplateLiteral]
        conditions = []  # type: List[TemplateLiteral]

        def extract_first_second_from_branch(_cond):
            # type: (Any) -> Iterator[TemplateLiteral]
            first = getattr(_cond, "first", None)
            second = getattr(_cond, "second", None)
            if first is not None and first:
                for subcond in extract_first_second_from_branch(first):
                    yield subcond
            if second is not None and second:
                for subcond in extract_first_second_from_branch(second):
                    yield subcond
            if first is None and second is None:
                yield _cond

        for index, condition_nodelist in enumerate(self.conditions_nodelists, start=1):
            condition, nodelist = condition_nodelist
            if condition is not None:
                for _cond in extract_first_second_from_branch(condition):
                    if _cond not in conditions_seen:
                        conditions.append(_cond)
                        conditions_seen.add(_cond)
        for condition in conditions:
            if hasattr(condition, "value") and hasattr(condition.value, "resolve"):
                try:
                    condition.value.resolve(context)
                except Exception as e:
                    if isinstance(e, MissingVariable):
                        raise
    return result


URL_BLACKLIST = (
    # Admin login
    ("admin_password_reset", "password_reset_url"),
    # Admin header (every page)
    ("django-admindocs-docroot", "docsroot"),
)  # type: Tuple[Tuple[Text, Text], ...]


def url_blacklist():
    # type: () -> Tuple[Tuple[Text, Text], ...]
    # TODO: make this memoized/cached?
    return URL_BLACKLIST + tuple(getattr(settings, "SHOUTY_URL_BLACKLIST", ()))


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
    if invalid_variables is True:
        patched_var = getattr(Variable, "_shouty", False)
        if patched_var is False:
            Variable._resolve_lookup = new_resolve_lookup
            Variable._shouty = True

        patched_if = getattr(IfNode, "_shouty", False)
        if patched_if is False:
            IfNode.render = new_if_render
            IfNode._shouty = True

    if invalid_urls is True:
        patched_url = getattr(URLNode, "_shouty", False)
        if patched_url is False:
            URLNode.render = new_url_render
            URLNode._shouty = True
    return True


def check_user_blacklists(app_configs, **kwargs):
    # type: (Any, **Any) -> List[checks.Error]
    errors = []
    user_blacklist = getattr(settings, "SHOUTY_VARIABLE_BLACKLIST", ())
    if hasattr(user_blacklist, "items") and callable(user_blacklist.items):
        for var, templates in user_blacklist.items():
            if force_text(var) != var:
                errors.append(
                    checks.Error(
                        "Expected key {!r} to be a string".format(var),
                        obj="settings.SHOUTY_VARIABLE_BLACKLIST",
                    )
                )
            if force_text(templates) == templates:
                errors.append(
                    checks.Error(
                        "Key {} has it's list of templates as a string".format(var),
                        hint="Templates should be like: ('template.html', 'template2.html')",
                        obj="settings.SHOUTY_VARIABLE_BLACKLIST",
                    )
                )
            try:
                template_count = len(templates)
            except Exception:
                errors.append(
                    checks.Error(
                        "Key {} has an unexpected templates defintion".format(var),
                        hint="The value for templates should be like: ('template.html', 'template2.html')",
                        obj="settings.SHOUTY_VARIABLE_BLACKLIST",
                    )
                )
            else:
                if template_count < 1:
                    errors.append(
                        checks.Error(
                            "Key {} has an unexpected templates defintion".format(var),
                            hint="There are no templates whitelisted, nor the magic '*' value",
                            obj="settings.SHOUTY_VARIABLE_BLACKLIST",
                        )
                    )
    else:
        if force_text(user_blacklist) == user_blacklist:
            errors.append(
                checks.Error(
                    "Setting appears to be a string",
                    hint="Should be a sequence or dictionary (eg: ['myvar', 'myvar2'])",
                    obj="settings.SHOUTY_VARIABLE_BLACKLIST",
                )
            )
        try:
            iter(user_blacklist)
        except TypeError:
            errors.append(
                checks.Error(
                    "Setting doesn't appear to be a sequence",
                    hint="Should be a sequence or dictionary (eg: ['myvar', 'myvar2'])",
                    obj="settings.SHOUTY_VARIABLE_BLACKLIST",
                )
            )
        else:
            for var in user_blacklist:
                if force_text(var) != var:
                    errors.append(
                        checks.Error(
                            "Expected {!r} to be a string".format(var),
                            obj="settings.SHOUTY_VARIABLE_BLACKLIST",
                        )
                    )
    return errors


class Shout(AppConfig):  # type: ignore
    """
    Applies the patch automatically if enabled.
    If `shouty` or `shouty.Shout` is added to INSTALLED_APPS only.
    """

    name = "shouty"

    def ready(self):
        # type: () -> bool
        logger.info("Applying shouty templates patch")
        checks.register(check_user_blacklists, checks.Tags.templates)
        return patch(
            invalid_variables=getattr(settings, "SHOUTY_VARIABLES", True),
            invalid_urls=getattr(settings, "SHOUTY_URLS", True),
        )


default_app_config = "shouty.Shout"


if __name__ == "__main__":
    from django.test import TestCase, override_settings
    from django.test.runner import DiscoverRunner
    import django
    from django.conf import settings as test_settings
    from django.utils.functional import SimpleLazyObject

    def urlpatterns():
        # type: () -> Tuple[Any, ...]
        from django.urls import path, include
        from django.contrib import admin

        return (
            path("admin/doc/", include("django.contrib.admindocs.urls")),
            path("admin/", admin.site.urls),
        )

    test_settings.configure(
        DATABASES={
            "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}
        },
        INSTALLED_APPS=(
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "django.contrib.admin",
            "django.contrib.admindocs",
            "django.contrib.sessions",
            "django.contrib.messages",
            "shouty",
        ),
        MIDDLEWARE=(
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
            "django.contrib.messages.middleware.MessageMiddleware",
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
        ROOT_URLCONF=SimpleLazyObject(urlpatterns),
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

        def test_silencing(self):
            # type: () -> None
            """ Adding a variable to the blacklist works OK """
            t = TMPL("this works: {{ a }}")
            with override_settings(SHOUTY_VARIABLE_BLACKLIST=("a",)):
                t.render(CTX({}))
            with override_settings(SHOUTY_VARIABLE_BLACKLIST={"a": [UNKNOWN_SOURCE]}):
                t.render(CTX({}))
            with override_settings(SHOUTY_VARIABLE_BLACKLIST={"a": ["*"]}):
                t.render(CTX({}))
            with override_settings(SHOUTY_VARIABLE_BLACKLIST={"a": ["test.html"]}):
                with self.assertRaises(self.MissingVariable):
                    t.render(CTX({}))

        def test_checks(self):
            # type: () -> None
            """ system checks work OK """
            with override_settings(SHOUTY_VARIABLE_BLACKLIST=("a",)):
                self.assertEqual(check_user_blacklists(None), [])
            with override_settings(SHOUTY_VARIABLE_BLACKLIST={"a": [UNKNOWN_SOURCE]}):
                self.assertEqual(check_user_blacklists(None), [])
            with override_settings(SHOUTY_VARIABLE_BLACKLIST={"a": ["*"]}):
                self.assertEqual(check_user_blacklists(None), [])
            with override_settings(SHOUTY_VARIABLE_BLACKLIST={"a": ["test.html"]}):
                self.assertEqual(check_user_blacklists(None), [])
            with override_settings(SHOUTY_VARIABLE_BLACKLIST="a"):
                self.assertEqual(
                    check_user_blacklists(None)[0].msg,
                    "Setting appears to be a string",
                )
            with override_settings(SHOUTY_VARIABLE_BLACKLIST=1):
                self.assertEqual(
                    check_user_blacklists(None)[0].msg,
                    "Setting doesn't appear to be a sequence",
                )
            with override_settings(SHOUTY_VARIABLE_BLACKLIST=(1,)):
                self.assertEqual(
                    check_user_blacklists(None)[0].msg, "Expected 1 to be a string",
                )

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

        def test_for_loop(self):
            # type: () -> None
            t = TMPL(
                """
                {% for x in chef.can_add_cakes %}
                {{ x }}
                {% endfor %}
                
                {% for x in chef.can_add_pastry %}
                {{ x }}
                {% endfor %}
                """
            )

            class Chef(object):
                can_add_cakes = (1, 2, 3)
                can_add_pastries = (1, 2, 3)

            exc = (
                "Token 'can_add_pastry' of 'chef.can_add_pastry' in template '<unknown source>' does not resolve.\n"
                "Possibly you meant one of: 'can_add_pastries', 'can_add_cakes'.\n"
                "You may silence this by adding 'chef.can_add_pastry' to settings.SHOUTY_VARIABLE_BLACKLIST"
            )
            with self.assertRaisesMessage(self.MissingVariable, exc):
                t.render(CTX({"chef": Chef()}))

        def test_multiple_variables_in_if_stmt_and_only_some_resolve(self):
            # type: () -> None
            t = TMPL(
                """
                {% if chef.can_add_cakes and chef.can_add_pastry == 1 %}
                whee
                {% endif %}
                """
            )

            class Chef(object):
                can_add_cakes = (1, 2, 3)
                can_add_pastries = (1, 2, 3)

            exc = (
                "Token 'can_add_pastry' of 'chef.can_add_pastry' in template '<unknown source>' does not resolve.\n"
                "Possibly you meant one of: 'can_add_pastries', 'can_add_cakes'.\n"
                "You may silence this by adding 'chef.can_add_pastry' to settings.SHOUTY_VARIABLE_BLACKLIST"
            )
            with self.assertRaisesMessage(self.MissingVariable, exc):
                t.render(CTX({"chef": Chef()}))

        def test_many_if_variables1(self):
            # type: () -> None
            t = TMPL(
                """
                {% if False and whooo == 2 or 0 and 1 and wheee and whooo.wheee %}
                whee
                {% endif %}
                """
            )
            exc = (
                "Variable 'wheee' in template '<unknown source>' does not resolve.\n"
                "You may silence this by adding 'wheee' to settings.SHOUTY_VARIABLE_BLACKLIST"
            )
            with self.assertRaisesMessage(self.MissingVariable, exc):
                t.render(CTX({"whooo": 0}))

        def test_many_if_variables2(self):
            # type: () -> None
            t = TMPL(
                """
                {% if False and whooo == 2 or 0 and 1 and wheee and x == 2 or f is None and False is True or wheee == wheeee %}
                whee
                {% endif %}
                """
            )
            exc = (
                "Variable 'whooo' in template '<unknown source>' does not resolve.\n"
                "You may silence this by adding 'whooo' to settings.SHOUTY_VARIABLE_BLACKLIST"
            )
            with self.assertRaisesMessage(self.MissingVariable, exc):
                t.render(CTX({}))

        def test_if_elif(self):
            # type: () -> None
            t = TMPL(
                """
                {% if 1 == 2 %}
                whee
                {% elif 2 == 3 %}
                whoo
                {% elif x == 4 %}
                wiggle
                {% endif %}
                """
            )
            exc = (
                "Variable 'x' in template '<unknown source>' does not resolve.\n"
                "You may silence this by adding 'x' to settings.SHOUTY_VARIABLE_BLACKLIST"
            )
            with self.assertRaisesMessage(self.MissingVariable, exc):
                t.render(CTX({}))

        def test_exception_debug_info(self):
            # type: () -> None
            t = TMPL(
                """
                {% if x == y %}
                {{ abc }}
                {% elif y == z %}
                {{ def }}
                {% endif %}
                """
            )
            t.engine.debug = True
            try:
                t.render(CTX({"x": 1, "y": 1, "def": 2}))
            except self.MissingVariable as exc:
                self.assertEqual(exc.template_debug["line"], 3)
                self.assertEqual(exc.template_debug["name"], UNKNOWN_SOURCE)
                self.assertEqual(exc.template_debug["during"], "abc")

    class UrlTestCase(TestCase):  # type: ignore
        def setUp(self):
            # type: () -> None
            from shouty import MissingVariable

            self.MissingVariable = MissingVariable

        def test_most_basic(self):
            # type: () -> None
            t = TMPL(
                """
                {% url "waffle" as wheee %}
                """
            )
            exc = (
                '{% url "waffle" ... as wheee %} in template \'<unknown source> did not resolve.\n'
                "You may silence this by adding ('waffle', 'wheee') to settings.SHOUTY_URL_BLACKLIST"
            )
            with self.assertRaisesMessage(self.MissingVariable, exc):
                t.render(CTX())

    class ReadmeExampleTestCase(TestCase):  # type: ignore
        def setUp(self):
            # type: () -> None
            from shouty import MissingVariable

            self.MissingVariable = MissingVariable

        def test_chef_renamed_to_sous_chef(self):
            # type: () -> None
            t = TMPL(
                """
                {% if chef.can_add_cakes %}
                    <label class="alert alert-{{ chef.is_cake_chef|yesno:"success,danger,default" }}
                {% endif %}
                """
            )

            class Chef(object):
                is_cake_chef = True
                can_add_cakes = True

            exc = (
                "Token 'chef' of 'chef.can_add_cakes' in template '<unknown source>' does not resolve.\n"
                "Possibly you meant to use 'sous_chef'.\n"
                "You may silence this by adding 'chef.can_add_cakes' to settings.SHOUTY_VARIABLE_BLACKLIST"
            )
            with self.assertRaisesMessage(self.MissingVariable, exc):
                t.render(CTX({"sous_chef": Chef()}))

        def test_is_cake_chef_renamed_to_is_pastry_king(self):
            # type: () -> None
            t = TMPL(
                """
                {% if chef.can_add_cakes %}
                    <label class="alert alert-{{ chef.is_cake_chef|yesno:"success,danger,default" }}
                {% endif %}
                """
            )

            class Chef(object):
                is_pastry_king = 1
                can_add_cakes = 1

            exc = (
                "Token 'is_cake_chef' of 'chef.is_cake_chef' in template '<unknown source>' does not resolve.\n"
                "You may silence this by adding 'chef.is_cake_chef' to settings.SHOUTY_VARIABLE_BLACKLIST"
            )
            with self.assertRaisesMessage(self.MissingVariable, exc):
                t.render(CTX({"chef": Chef()}))

        def test_can_add_cakes_renamed_to_can_add_pastries(self):
            # type: () -> None
            t = TMPL(
                """
                {% if chef.can_add_cakes %}
                    <label class="alert alert-{{ chef.is_cake_chef|yesno:"success,danger,default" }}
                {% endif %}
                """
            )

            class Chef(object):
                is_pastry_king = 1

                def can_add_pastries(self):
                    # type: () -> bool
                    return True

            exc = (
                "Token 'can_add_cakes' of 'chef.can_add_cakes' in template '<unknown source>' does not resolve.\n"
                "Possibly you meant to use 'can_add_pastries'.\n"
                "You may silence this by adding 'chef.can_add_cakes' to settings.SHOUTY_VARIABLE_BLACKLIST"
            )
            with self.assertRaisesMessage(self.MissingVariable, exc):
                t.render(CTX({"chef": Chef()}))

    class CommonAppsTestCase(TestCase):  # type: ignore
        def setUp(self):
            # type: () -> None
            from shouty import MissingVariable
            from django.contrib.auth import get_user_model

            self.MissingVariable = MissingVariable
            self.user = get_user_model().objects.create_superuser(
                username="admin", email="admin@admin.admin", password="admin"
            )
            self.client.force_login(self.user)

        def assertStatusCode(self, resp, value):
            # type: (Any, int) -> None
            if resp.status_code != value:
                self.fail(
                    "Expected status code {}, response had code {}".format(
                        value, resp.status_code
                    )
                )

        def test_admin_login_page_without_being_logged_in(self):
            # type: () -> None
            """ The admin login screen should not raise MissingVariable, regardless of authentication state """
            self.client.logout()
            r1 = self.client.get("/admin/")
            self.assertStatusCode(r1, 302)
            r2 = self.client.get("/admin/", follow=True)
            self.assertStatusCode(r2, 200)

        def test_get_requests_which_should_render_ok(self):
            # type: () -> None
            """ normal requests to these admin & admindocs pages should not raise MissingVariable """
            urls = (
                "/admin/doc/",
                "/admin/doc/tags/",
                "/admin/doc/filters/",
                "/admin/doc/models/",
                "/admin/doc/models/admin.logentry/",
                "/admin/doc/models/auth.permission/",
                "/admin/doc/models/auth.group/",
                "/admin/doc/models/auth.user/",
                "/admin/doc/models/contenttypes.contenttype/",
                "/admin/doc/models/sessions.session/",
                # "/admin/doc/views/",
                "/admin/doc/views/django.contrib.admindocs.views.ViewIndexView/",
                "/admin/doc/views/django.contrib.admin.sites.AdminSite.index/",
                "/admin/doc/views/django.contrib.admin.options.ModelAdmin.change_view/",
                "/admin/doc/views/django.contrib.admin.options.ModelAdmin.changelist_view/",
                "/admin/doc/bookmarklets/",
                "/admin/",
                "/admin/auth/group/",
                "/admin/auth/user/",
                "/admin/password_change/",
                "/admin/auth/group/add/",
                "/admin/auth/user/{}/change/".format(self.user.pk),
                "/admin/auth/user/{}/history/".format(self.user.pk),
                "/admin/auth/user/{}/delete/".format(self.user.pk),
            )
            for url in urls:
                with self.subTest(url=url):
                    response = self.client.get(url, follow=False)
                    self.assertStatusCode(response, 200)

        def test_example_404(self):
            # type: () -> None
            """ The technical 404 page should not itself cause a 500 error """
            with override_settings(DEBUG=True):
                response = self.client.get("/favicon.ico", follow=False)
                self.assertStatusCode(response, 404)
            with override_settings(DEBUG=False):
                response = self.client.get("/favicon.ico", follow=False)
                self.assertStatusCode(response, 404)

    class InternalVariableBlacklistTestCase(TestCase):  # type: ignore
        def test_im_not_an_idiot(self):
            # type: () -> None
            for k, v in VARIABLE_BLACKLIST.items():
                if len(v) == 0:
                    self.fail(
                        "Key {!s} of the VARIABLE_BLACKLIST has no templates or wildcard defined".format(
                            k
                        )
                    )
                elif len(v) > 1 and "*" in v:
                    self.fail(
                        "Key {!s} of the VARIABLE_BLACKLIST has templates defined and also a wildcard: {!r}".format(
                            k, v
                        )
                    )
            for k, v in IF_VARIABLE_BLACKLIST.items():
                if len(v) == 0:
                    self.fail(
                        "Key {!s} of the IF_VARIABLE_BLACKLIST has no templates or wildcard defined".format(
                            k
                        )
                    )
                elif len(v) > 1 and "*" in v:
                    self.fail(
                        "Key {!s} of the IF_VARIABLE_BLACKLIST has templates defined and also a wildcard: {!r}".format(
                            k, v
                        )
                    )

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
            test_runner.test_loader.loadTestsFromTestCase(UrlTestCase),
            test_runner.test_loader.loadTestsFromTestCase(ReadmeExampleTestCase),
            test_runner.test_loader.loadTestsFromTestCase(CommonAppsTestCase),
            test_runner.test_loader.loadTestsFromTestCase(
                InternalVariableBlacklistTestCase
            ),
            test_runner.test_loader.loadTestsFromTestCase(MyPyTestCase),
        ),
    )
