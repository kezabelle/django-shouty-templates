# -*- coding: utf-8 -*-
from __future__ import absolute_import, unicode_literals

import logging

from django.apps import AppConfig
from django.template.base import Variable, VariableDoesNotExist
from django.template.exceptions import TemplateSyntaxError

try:
    from typing import Any
except ImportError:
    pass


__all__ = ["MissingVariable", "patch", "Shout", "default_app_config"]


logger = logging.getLogger(__name__)


class MissingVariable(TemplateSyntaxError):
    """
    Django will raise TemplateSyntaxError in various scenarios, so this
    subclass is used to differentiate shouty errors, while still getting the
    same functionality.
    """

    pass


old_resolve_lookup = Variable._resolve_lookup


BLACKLIST = (
    # When trying to render the technical 500 template, it accesses
    # settings.SETTINGS_MODULE, which ordinarily is there. But if using
    # a UserSettingsHolder, it doesn't seem to be. I've not looked into
    # it fully, but it's a smashing great error that prevents the debug 500
    # from working otherwise.
    "settings.SETTINGS_MODULE",
    # django-debug-toolbar SQL panel, makes use of a dictionary of queries,
    # whose values aren't always available.
    "query.starts_trans",
    "query.ends_trans",
    "query.in_trans",
    "query.similar_count",
    "query.similar_color",
    "query.duplicate_count",
    "query.duplicate_color",
    "query.iso_level",
    "query.trans_status",
)


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
        dont_report = BLACKLIST
        if whole_var not in dont_report:
            part = e.params[0]
            # self.var might be 'request.user.pk' but part might just be 'pk'
            if part != whole_var:
                msg = "Token '{token}' of '{var}' does not resolve"
            else:
                msg = "Variable '{token}' does not resolve"
            msg = msg.format(token=part, var=whole_var)
            raise MissingVariable(msg)
        else:
            # Let the VariableDoesNotExist bubble back up to whereever it's
            # actually suppressed, to avoid having to decide wtf value to
            # return ("", or None?)
            raise


def patch():
    # type: () -> bool
    """
    Monkeypatch the Django Template Language's Variable class, replacing
    the `_resolve_lookup` method with `new_resolve_lookup` in this module.

    Calling it multiple times should be a no-op, and once applied will
    subsequently continue returning False
    """
    patched = getattr(Variable, "_shouty", False)
    if patched is True:
        return False
    Variable._resolve_lookup = new_resolve_lookup
    Variable._shouty = True
    return True


class Shout(AppConfig):
    """
    Applies the patch automatically if enabled.
    If `shouty` or `shouty.Shout` is added to INSTALLED_APPS only.
    """

    name = "shouty"

    def ready(self):
        logger.info("Applying shouty templates patch")
        return patch()


default_app_config = "shouty.Shout"
