# -*- coding: utf-8 -*-
from __future__ import absolute_import, unicode_literals

import logging
from collections import namedtuple

from django.apps import AppConfig
from django.conf import UserSettingsHolder, settings
from django.template.base import Variable, VariableDoesNotExist
from django.template.defaulttags import URLNode
from django.template.exceptions import TemplateSyntaxError

try:
    from typing import Any
except ImportError:
    pass


__version_info__ = '0.1.1'
__version__ = '0.1.1'
version = '0.1.1'
VERSION = '0.1.1'

def get_version():
    return version

__all__ = ["MissingVariable", "patch", "Shout", "default_app_config", "get_version"]


logger = logging.getLogger(__name__)


class MissingVariable(TemplateSyntaxError):
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
    "is_popup",  # Used all over the shop, but not declared everywhere.
    "cl.formset.errors",  # Used on the changelist even if there's no formset?
    "show",  # date_hierarchy for the changelist doesn't actually always return a dictionary ...
    "cl.formset.is_multipart",  # Used on the changelist even if there's no formset?
    "result.form.non_field_errors",  # Used on the changelist ...
)


def variable_blacklist():
    # type: () -> tuple
    # TODO: make this memoized/cached?
    return VARIABLE_BLACKLIST + tuple(getattr(settings, 'SHOUTY_VARIABLE_BLACKLIST', ()))


def new_resolve_lookup(self, context):
    # type: (Variable, Any) -> Any
    """
    Call the original _resolve_lookup method, and if it fails with an exception
    which would ordinarily be suppressed by the Django Template Language,
    instead re-format it and re-raise it as another, uncaught exception type.
    """
    try:
        return old_resolve_lookup(self, context)
    except VariableDoesNotExist as e:
        whole_var = self.var
        if whole_var not in variable_blacklist():
            part = e.params[0]
            # self.var might be 'request.user.pk' but part might just be 'pk'
            if part != whole_var:
                msg = "Token '{token}' of '{var}' does not resolve - you can silence it by adding '{var}' to settings.SHOUTY_VARIABLE_BLACKLIST"
            else:
                msg = "Variable '{token}' does not resolve - you can silence it by adding '{var} to settings.SHOUTY_VARIABLE_BLACKLIST"
            msg = msg.format(token=part, var=whole_var)
            raise MissingVariable(msg)
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
)

def url_blacklist():
    # type: () -> tuple
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
    value = old_url_render(self, context)
    outvar = self.asvar
    if outvar is not None and context[outvar] == "":
        key = (str(self.view_name.var), outvar)
        if key not in url_blacklist():
            raise MissingVariable(
                '{{% url {token!s} ... as {asvar!s} %}} did not resolve - you can silence it by adding {key!r} to settings.SHOUTY_URL_BLACKLIST'.format(token=self.view_name, asvar=self.asvar, key=key)
            )
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


class Shout(AppConfig):
    """
    Applies the patch automatically if enabled.
    If `shouty` or `shouty.Shout` is added to INSTALLED_APPS only.
    """
    name = "shouty"

    def ready(self) -> bool:
        logger.info("Applying shouty templates patch")
        return patch(
            invalid_variables=getattr(settings, "SHOUTY_VARIABLES", True),
            invalid_urls=getattr(settings, "SHOUTY_URLS", True),
        )


default_app_config = "shouty.Shout"
