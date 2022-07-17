# -*- coding: utf-8 -*-
from __future__ import absolute_import, unicode_literals
import logging
import sys

from collections import namedtuple

from django.core import checks

try:
    from django.utils.encoding import force_str as force_text
except ImportError:
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
    Origin,
    Node,
    Token,
    NodeList,
    Template,
)
from django.template.context import BaseContext
from django.template.defaulttags import URLNode, IfNode, TemplateLiteral
from django.template.exceptions import TemplateSyntaxError, TemplateDoesNotExist
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

__version_info__ = "0.2.0"
__version__ = "0.2.0"
version = "0.2.0"
VERSION = "0.2.0"


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

    def __init__(self, *args, token, template_name, all_template_names):
        super().__init__(*args)
        self.token = token
        self.template_name = template_name
        self.all_template_names = all_template_names


old_resolve_lookup = Variable._resolve_lookup
old_url_render = URLNode.render
old_if_render = IfNode.render

ANY_TEMPLATE = "*"
ANY_VARIABLE = "*"


# The following values throw an exception when used as {{ var.name }}
VARIABLE_BLACKLIST = {
    # When trying to render the technical 500 template, it accesses
    # settings.SETTINGS_MODULE, which ordinarily is there. But if using
    # a UserSettingsHolder, it doesn't seem to be. I've not looked into
    # it fully, but it's a smashing great error that prevents the debug 500
    # from working otherwise.
    # TODO: Get fixed upstream?
    "settings.SETTINGS_MODULE": (ANY_TEMPLATE,),
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
    "subtitle": ("admin/base_site.html",),
    "model.add_url": (
        "admin/index.html",
        "admin/app_index.html",
    ),  # used on the site/app index
    "model.admin_url": (
        "admin/index.html",
        "admin/app_index.html",
    ),  # used on the site/app index
    "is_popup": (
        ANY_TEMPLATE,
    ),  # Used all over the shop: (), but not declared everywhere.
    "cl.formset.errors": (
        "admin/change_list.html",
    ),  # Used on the changelist even if there's no formset?
    "show": (ANY_TEMPLATE,),
    # date_hierarchy for the changelist doesn't actually always return a dictionary ...
    "cl.formset.is_multipart": (
        "admin/change_list.html",
    ),  # Used on the changelist even if there's no formset?
    "result.form.non_field_errors": (
        "admin/change_list_results.html",
    ),  # Used on the changelist ...
    "can_change_related": (ANY_TEMPLATE,),  # Used by related_widget_wrapper
    "can_add_related": (ANY_TEMPLATE,),  # Used by related_widget_wrapper
    "can_delete_related": (ANY_TEMPLATE,),  # Used by related_widget_wrapper
    # Django's technical 500 templates (text & html) via get_traceback_data
    "exception_type": (ANY_TEMPLATE,),
    "exception_value": (ANY_TEMPLATE,),
    "lastframe": (ANY_TEMPLATE,),
    "request_GET_items": (ANY_TEMPLATE,),
    "request_FILES_items": (ANY_TEMPLATE,),
    "request_COOKIES_items": (ANY_TEMPLATE,),
    # Django's "debug" context processor only fills debug and sql_queries if
    # DEBUG=True and the user's IP is in INTERNAL_IPS
    "debug": (ANY_TEMPLATE,),
    "sql_queries": (ANY_TEMPLATE,),
    "site_title": ("admin_honeypot/login.html",),
    "site_header": ("admin_honeypot/login.html",),
    # Django pipeline templates.
    "media": ("pipeline/css.html",),
    # In Django 3.1 this ... might be necessary. IDK I can't recall
    # if the request context processor being missing actually causes
    # a system check error for the admin.
    "request.path": ("admin/app_list.html",),
    # In Django 3.1+ this has turned up...
    "show_changelinks": ("admin/app_list.html",),
    "is_nav_sidebar_enabled": ("admin/base_site.html",),
    # These can occur in the technical 500 page.
    "template_info.name": (UNKNOWN_SOURCE,),
    "template_info.line": (UNKNOWN_SOURCE,),
    "template_info.top": (UNKNOWN_SOURCE,),
    "template_info.bottom": (UNKNOWN_SOURCE,),
    "template_info.total": (UNKNOWN_SOURCE,),
    "template_info.source_lines": (UNKNOWN_SOURCE,),
    # accessing admindocs views will have this in file admin_doc/view_index.html
    "view.title": ("admin_doc/view_index.html",),
}  # type: Dict[str, Tuple[Text,...]]


# The following values throw an exception when used in an if like {% if forloop.last and pat.name %}
IF_VARIABLE_BLACKLIST = {
    # When trying to render the technical 404 template, {% if forloop.last and pat.name %} gets used
    # in file <unknown source>
    "pat.name": (UNKNOWN_SOURCE,),
    # Accessing admindocs index will trigger this: {% if title %}<h1>{{ title }}</h1>{% endif %}
    # in file admin_doc/index.html and every subpage therein...
    "title": (
        "admin/base.html",
        "admin/base_site.html",
        "pipeline/css.html",
    ),
    "subtitle": (
        "admin/base.html",
        "admin/base_site.html",
    ),
    # accessing admindocs detailed model information has: {% if field.help_text %} - ...
    # in file admin_doc/model_detail.html
    "field.help_text": ("admin_doc/model_detail.html",),
    # accessing admindocs views detail will have these in file admin_doc/view_detail.html
    "meta.Context": ("admin_doc/view_detail.html",),
    "meta.Templates": ("admin_doc/view_detail.html",),
    # Rest framework...
    "name": ("rest_framework/base.html",),
    "code_style": ("rest_framework/base.html",),
    "style.hide_label": (
        "rest_framework/horizontal/checkbox.html",
        "rest_framework/horizontal/checkbox_multiple.html",
        "rest_framework/horizontal/dict_field.html",
        "rest_framework/horizontal/fieldset.html",
        "rest_framework/horizontal/input.html",
        "rest_framework/horizontal/list_field.html",
        "rest_framework/horizontal/list_fieldset.html",
        "rest_framework/horizontal/radio.html",
        "rest_framework/horizontal/select.html",
        "rest_framework/horizontal/select_multiple.html",
        "rest_framework/horizontal/textarea.html",
        "rest_framework/vertical/checkbox.html",
        "rest_framework/vertical/checkbox_multiple.html",
        "rest_framework/vertical/dict_field.html",
        "rest_framework/vertical/fieldset.html",
        "rest_framework/vertical/input.html",
        "rest_framework/vertical/list_field.html",
        "rest_framework/vertical/list_fieldset.html",
        "rest_framework/vertical/radio.html",
        "rest_framework/vertical/select.html",
        "rest_framework/vertical/select_multiple.html",
        "rest_framework/vertical/textarea.html",
    ),
    "style.placeholder": (
        "rest_framework/horizontal/input.html",
        "rest_framework/horizontal/textarea.html",
        "rest_framework/inline/input.html",
        "rest_framework/inline/textarea.html",
        "rest_framework/vertical/input.html",
        "rest_framework/vertical/textarea.html",
    ),
    "style.autofocus": (
        "rest_framework/horizontal/input.html",
        "rest_framework/inline/input.html",
        "rest_framework/vertical/input.html",
    ),
    # Django pipeline templates.
    "charset": ("pipeline/css.html",),
    "async": ("pipeline/js.html",),
    "defer": ("pipeline/js.html",),
}  # type: Dict[str, Tuple[Text,...]]

# The following IfNode contents don't have an else but do have an elif, so
# need to be silenced.
IF_ELSE_BLACKLIST = {
    # Django admin
    "if model.admin_url and show_changelinks": ("admin/app_list.html",),
    "if not line.fields|length_is:'1'": ("admin/includes/fieldset.html",),
}


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
            variables_by_template[var].append(ANY_TEMPLATE)
    return variables_by_template


def if_else_blacklist():
    # type: () -> Dict[Text, List[Text]]
    variables_by_template = {}  # type: Dict[Text, List[Text]]
    # Is compounding the IF specific checks with the normal checks silencing
    # anything incorrectly, I wonder? ...
    for var, templates in IF_ELSE_BLACKLIST.items():
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
            variables_by_template[var].append(ANY_TEMPLATE)
    return variables_by_template


def is_silenced(whole_var, template_name, blacklist, all_template_names):
    ignored_templates_for_this_var = blacklist.get(whole_var, [])
    ignored_templates_for_any_var = blacklist.get(ANY_VARIABLE, [])
    if ANY_TEMPLATE in ignored_templates_for_this_var:
        logger.debug(
            "Ignoring '%s' globally via * (of %s)",
            whole_var,
            ignored_templates_for_this_var,
        )
        return True
    elif template_name in ignored_templates_for_this_var:
        logger.debug(
            "Ignoring '%s' for template '%s' (of %s)",
            whole_var,
            template_name,
            ignored_templates_for_this_var,
        )
        return True
    elif any(x in ignored_templates_for_this_var for x in all_template_names):
        logger.debug(
            "Ignoring '%s' for template '%s' (of %s)",
            whole_var,
            template_name,
            all_template_names,
        )
        return True
    elif any(x in ignored_templates_for_any_var for x in all_template_names):
        logger.debug(
            "Ignoring '%s' for template '%s' (of %s) due to * over %s",
            whole_var,
            template_name,
            all_template_names,
            ignored_templates_for_any_var,
        )
        return True
    elif template_name in ignored_templates_for_any_var:
        logger.debug(
            "Ignoring '%s' for template '%s' (of %s) due to * over %s",
            whole_var,
            template_name,
            all_template_names,
            ignored_templates_for_any_var,
        )
        return True
    else:
        return False


def create_exception_with_template_debug(context, part, node):
    # type: (Context, Text, Optional[Node]) -> Tuple[Text, Dict[Text, Any], List[Text]]
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

    token = None  # type: Optional[Token]
    origin = None  # type: Optional[Origin]
    if node is not None:
        token = node.token
        origin = node.origin

    if origin is not None:
        contexts_to_search.append(origin)
        all_potential_contexts.append(origin)

    render_context = context.render_context
    # Prefer extends origins
    if "extends_context" in render_context and render_context["extends_context"]:
        all_potential_contexts.extend(render_context.get("extends_context", []))
    # Who knows which order might be right for context or render context? not me.
    # It's possible for the template attribute to not exist OR for it to be None
    # in theory, so we'll guard against that.
    render_context_template = getattr(
        render_context, "template", None
    )  # type: Optional[Template]
    if render_context_template is not None:
        all_potential_contexts.append(render_context_template)
    context_template = getattr(context, "template", None)  # type: Optional[Template]
    if context_template is not None:
        all_potential_contexts.append(context.template)
    render_context_flat = render_context.flatten()
    # Inclusion nodes put their template into the context with themselves as a key.
    for k in render_context_flat:
        if isinstance(render_context_flat[k], Template):
            all_potential_contexts.append(render_context_flat[k])
    context_flat = context.flatten()
    for k in context_flat:
        if isinstance(context_flat[k], Template):
            all_potential_contexts.append(context_flat[k])

    for ctx in all_potential_contexts:
        if ctx not in contexts_to_search:
            contexts_to_search.append(ctx)

    assert len(contexts_to_search) <= len(all_potential_contexts)

    template_names = []  # type: List[Text]

    for parent in contexts_to_search:
        if isinstance(parent, Origin):
            # an Origin without a template name would crash deep down in
            # find_template with
            # TypeError: join() argument must be str, bytes, or os.PathLike object, not 'NoneType'
            if parent.template_name:
                try:
                    _template, _origin = context.template.engine.find_template(
                        parent.template_name,
                        skip=None,
                    )
                except TemplateDoesNotExist:
                    # Got an Origin without the Template backing it being findable...
                    # So just fake one.
                    _origin = parent
                    _template = Template("", origin=_origin, name=parent)
            else:
                _origin = parent
                _template = Template("", origin=_origin, name=parent)
        else:
            _template = parent
            _origin = parent.origin

        if _origin.template_name is None:
            template_names.append(UNKNOWN_SOURCE)
        else:
            template_names.append(_origin.template_name)

        src = _template.source  # type: Text

        # Just skip every subsequent check if the token/variable/part isn't anywhere in the template
        # which is sometimes the case if walking backwards through a series of templates, and sometimes
        # (eg: in crispy_forms) the variable never exists in any template file (html5_required) so we can
        # short circuit here.
        if part not in src:
            continue

        # Using a DebugLexer instead of a Lexer, so we have positions.
        if token is not None and getattr(token, "position", None) is not None:
            start = src.find(part, token.position[0])  # type: int
            if start > -1:
                end = start + len(part)
                highlight_part = faketoken(position=(start, end))
                exc_info = _template.get_exception_info(
                    ValueError("ignored"), highlight_part
                )
                return (
                    _template.origin.template_name or UNKNOWN_SOURCE,
                    exc_info,
                    template_names,
                )

    if UNKNOWN_SOURCE not in template_names:
        template_names.append(UNKNOWN_SOURCE)
    return UNKNOWN_SOURCE, {}, template_names


def new_resolve_lookup(self, context):
    # type: (Variable, Any) -> Any
    """
    Call the original _resolve_lookup method, and if it fails with an exception
    which would ordinarily be suppressed by the Django Template Language,
    instead re-format it and re-raise it as another, uncaught exception type.
    """
    __traceback_hide__ = settings.DEBUG
    parent_frame = sys._getframe()
    parent_node = None  # type: Optional[Node]
    while parent_frame.f_locals:
        if "self" in parent_frame.f_locals:
            obj = parent_frame.f_locals["self"]
            if (
                isinstance(obj, Node)
                and hasattr(obj, "origin")
                and hasattr(obj, "token")
            ):
                parent_node = obj
                break
        parent_frame = parent_frame.f_back
    try:
        return old_resolve_lookup(self, context)
    except VariableDoesNotExist as e:
        # Given the token {{ xyz }}
        # it should be possible to NOT raise the MissingVariable exception by setting:
        # "xyz": ['*']
        # "xyz": ['path/to/specific/template.html', 'other/template.html']
        # "*": ['path/to/template.html']
        whole_var = self.var
        blacklist = variable_blacklist()
        ignored_templates_for_this_var = blacklist.get(whole_var, [])
        ignored_templates_for_any_var = blacklist.get(ANY_VARIABLE, [])
        not_being_ignored = whole_var not in blacklist
        has_per_template_ignores = (len(ignored_templates_for_this_var) > 0) or (
            len(ignored_templates_for_any_var) > 0
        )
        if not_being_ignored or has_per_template_ignores:
            try:
                (
                    template_name,
                    exc_info,
                    all_template_names,
                ) = create_exception_with_template_debug(
                    context, whole_var, parent_node
                )
            except Exception as e2:
                logger.warning(
                    "failed to create template_debug information", exc_info=e2
                )
                # In case my code is terrible, and raises an exception, let's
                # just carry on and let Django try for itself to set up relevant
                # debug info
                template_name = UNKNOWN_SOURCE
                exc_info = {}
                all_template_names = [UNKNOWN_SOURCE]
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
            if all_template_names:
                msg += "\nSilence this occurance only by adding '{var}': ['{template}'] to the settings.SHOUTY_VARIABLE_BLACKLIST dictionary."
            msg += "\nSilence this globally by adding '{var}': ['*'] to the settings.SHOUTY_VARIABLE_BLACKLIST dictionary."
            msg = msg.format(
                token=bit,
                var=whole_var,
                template=template_name,
                closest_matches="', '".join(closest),  # type: ignore
                templates="', '".join(all_template_names),
            )
            exc = MissingVariable(
                msg,
                token=whole_var,
                template_name=template_name,
                all_template_names=all_template_names,
            )
            if context.template.engine.debug and exc_info:
                exc_info["message"] = msg
                exc.template_debug = exc_info
            if not is_silenced(whole_var, template_name, blacklist, all_template_names):
                raise exc
        else:
            # Let the VariableDoesNotExist bubble back up to whereever it's
            # actually suppressed, to avoid having to decide wtf value to
            # return ("", or None?)
            raise e


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
    whole_var = self.token.contents
    # Attempt to handle the case where there's an {% if %} followed by an {% elif %} but no {% else %}
    # Note to self: I always have access to the top of the node (self.token), so possibly can refactor
    # some other places to parse less?
    if (
        len(self.conditions_nodelists) > 1
        and self.conditions_nodelists[-1][0] is not None
    ):
        blacklist = if_else_blacklist()
        ignored_templates_for_this_var = blacklist.get(whole_var, [])
        ignored_templates_for_any_var = blacklist.get(ANY_VARIABLE, [])
        not_being_ignored = whole_var not in blacklist
        has_per_template_ignores = (len(ignored_templates_for_this_var) > 0) or (
            len(ignored_templates_for_any_var) > 0
        )
        if not_being_ignored or has_per_template_ignores:
            try:
                (
                    template_name,
                    exc_info,
                    all_template_names,
                ) = create_exception_with_template_debug(context, whole_var, self)
            except Exception as e2:
                logger.warning(
                    "failed to create template_debug information", exc_info=e2
                )
                # In case my code is terrible, and raises an exception, let's
                # just carry on and let Django try for itself to set up relevant
                # debug info
                template_name = UNKNOWN_SOURCE
                all_template_names = [template_name]
                exc_info = {}

            if context.template.engine.debug and exc_info is not None:
                msg = (
                    "No `else` statement found for '{{% {ifnode} %}}{{% elif ... %}}{{% endif %}}' in '{template}'"
                    "\nSilence this by adding '{ifnode}': ['{template}'] to the settings.SHOUTY_VARIABLE_BLACKLIST dictionary.".format(
                        ifnode=whole_var, template=template_name
                    )
                )
                exc = MissingVariable(
                    msg,
                    token=whole_var,
                    template_name=template_name,
                    all_template_names=all_template_names,
                )
                exc_info["message"] = msg
                exc.template_debug = exc_info
                if not is_silenced(
                    whole_var,
                    exc.template_name,
                    blacklist,
                    exc.all_template_names,
                ):
                    raise exc

    # I need to collect all the conditions from all the nodelists BEFORE calling
    # the original render method, to allow for peeking at which nodelist was
    # rendered. When exhaustive {% if %}{% elif %} checking occurs and FORCES
    # an else condition, that else condition may something other than "", so requires
    # knowing that the ELSE nodelist was the one which was rendered, whilst
    # still collecting the individual conditions that made up all the if components
    # to check which ones failed with a MissingVariable exception rather than just
    # evaluating falsy...
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
            condition.value.resolve(context)

    return old_if_render(self, context)


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
        key = (str(self.view_name.var), str(outvar))
        if key not in url_blacklist():
            try:
                (
                    template_name,
                    exc_info,
                    all_template_names,
                ) = create_exception_with_template_debug(context, outvar, self)
            except Exception as e2:
                logger.warning(
                    "failed to create template_debug information", exc_info=e2
                )
                # In case my code is terrible, and raises an exception, let's
                # just carry on and let Django try for itself to set up relevant
                # debug info
                template_name = UNKNOWN_SOURCE
                all_template_names = [template_name]
                exc_info = {}
            msg = "{{% url {token!s} ... as {asvar!s} %}} in template '{template} did not resolve.\nYou may silence this globally by adding {key!r} to settings.SHOUTY_URL_BLACKLIST".format(
                token=self.view_name,
                asvar=outvar,
                key=key,
                template=template_name,
            )
            exc = MissingVariable(
                msg,
                token=key,
                template_name=template_name,
                all_template_names=all_template_names,
            )
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
    if not settings.DEBUG:
        return False

    if invalid_variables is True:
        patched_var = getattr(Variable, "_shouty", False)
        if patched_var is False:
            Variable._resolve_lookup = new_resolve_lookup
            Variable._shouty = True

        # Provides exhaustive if/elif/else checking as well as all conditional
        # in context checking ...
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
                if var == ANY_VARIABLE and template_count < 1:
                    errors.append(
                        checks.Error(
                            "Magic variable * has an unexpected templates defintion".format(
                                var
                            ),
                            hint="Using * requires you to specify a specific template (or set of templates) to ignore",
                            obj="settings.SHOUTY_VARIABLE_BLACKLIST",
                        )
                    )
                elif var == ANY_VARIABLE and ANY_TEMPLATE in templates:
                    errors.append(
                        checks.Error(
                            "Magic variable * has an unexpected templates defintion".format(
                                var
                            ),
                            hint="Using * for both the variable and template isn't supported, use settings.SHOUTY_VARIABLES = False",
                            obj="settings.SHOUTY_VARIABLE_BLACKLIST",
                        )
                    )
                elif template_count < 1:
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
    import os

    try:
        import coverage
    except ImportError:
        sys.stdout.write("coverage not installed\n")
        cov = None
    else:
        sys.stdout.write("using coverage\n")
        cov = coverage.Coverage(
            include=["shouty.py"], branch=True, check_preimported=True
        )
        cov.start()
    from unittest import skipIf
    from contextlib import contextmanager
    from django.test import TestCase, SimpleTestCase, override_settings
    from django.test.runner import DiscoverRunner
    from django.views.debug import ExceptionReporter
    from django.test.client import RequestFactory
    from django import setup as django_setup, VERSION as DJANGO_VERSION
    from django.conf import settings as test_settings
    from django.utils.functional import SimpleLazyObject

    EXTRA_INSTALLED_APPS = ()  # type: Tuple[Text, ...]
    try:
        import admin_honeypot

        if DJANGO_VERSION[0:2] > (1, 9):
            EXTRA_INSTALLED_APPS += ("admin_honeypot",)
    except ImportError:
        pass
    try:
        import crispy_forms

        EXTRA_INSTALLED_APPS += ("crispy_forms",)
    except ImportError:
        pass

    def urlpatterns():
        # type: () -> Tuple[Any, ...]
        try:
            from django.urls import re_path, include
        except ImportError:
            from django.conf.urls import url as re_path, include
        from django.contrib import admin

        patterns = ()  # type: Tuple[Any, ...]
        if "admin_honeypot" in EXTRA_INSTALLED_APPS:
            patterns += (re_path(r"^admin_honeypot/", include("admin_honeypot.urls")),)

        patterns += (
            re_path(r"^admin/doc/", include("django.contrib.admindocs.urls")),
            re_path(r"^admin/", admin.site.urls),
        )
        return patterns

    if DJANGO_VERSION[0:2] <= (1, 9):
        version_specific_settings = {
            "MIDDLEWARE_CLASSES": [
                "django.contrib.sessions.middleware.SessionMiddleware",
                "django.contrib.auth.middleware.AuthenticationMiddleware",
                "django.contrib.messages.middleware.MessageMiddleware",
            ]
        }
    else:
        version_specific_settings = {
            "MIDDLEWARE": [
                "django.contrib.sessions.middleware.SessionMiddleware",
                "django.contrib.auth.middleware.AuthenticationMiddleware",
                "django.contrib.messages.middleware.MessageMiddleware",
            ]
        }
    test_settings.configure(
        DEBUG=True,
        SECRET_KEY="test-test-test-test-test-test-test-test-test-test-test-test",
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
            "shouty.Shout",
        )
        + EXTRA_INSTALLED_APPS,
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [],
                "APP_DIRS": True,
                "OPTIONS": {
                    "context_processors": (
                        "django.contrib.auth.context_processors.auth",
                        "django.contrib.messages.context_processors.messages",
                        "django.template.context_processors.request",
                    )
                },
            },
        ],
        ROOT_URLCONF=SimpleLazyObject(urlpatterns),
        SHOUTY_VARIABLES=True,
        SHOUTY_URLS=True,
        LOGGING={
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "console": {
                    "format": "[%(levelname)s] %(name)s -> %(funcName)s -> %(message)s"
                }
            },
            "handlers": {
                "console": {"class": "logging.StreamHandler", "formatter": "console"},
            },
            "root": {
                "handlers": ["console"],
                "level": "ERROR",
            },
            "loggers": {
                "shouty": {
                    "handlers": ["console"],
                    "level": os.environ.get("SHOUTY_LOGGING", "WARNING").upper(),
                    "propagate": False,
                },
                "django.request": {
                    "handlers": ["console"],
                    "level": "ERROR",
                    "propagate": False,
                },
            },
        },
        **version_specific_settings
    )
    django_setup()
    from django.template import Template, Context as CTX
    from django.forms import IntegerField
    from django.template.loader import render_to_string

    class TMPL(Template):  # type: ignore
        def __init__(self, *args, **kwargs):
            # type: (Any, Any) -> None
            super(TMPL, self).__init__(*args, **kwargs)
            self.engine.debug = True

    class CustomAssertions(object):
        def assertStatusCode(self, resp, value):
            # type: (Any, int) -> None
            if resp.status_code != value:
                self.fail(
                    "Expected status code {}, response had code {}".format(
                        value, resp.status_code
                    )
                )

        @contextmanager
        def assertRaisesWithTemplateDebug(
            self, exception_type, exception_repr, debug_data
        ):
            # type: (Type[Exception], Text, Dict[Text, Any]) -> Iterator[None]
            try:
                yield
            except exception_type as exc:
                self.assertIn(str(exception_repr), str(exc))  # type: ignore
                req = RequestFactory().get("/")
                reporter = ExceptionReporter(
                    request=req,
                    exc_type=exception_type,
                    exc_value=exc,
                    tb=None,
                    is_email=True,
                )
                traceback_data = reporter.get_traceback_data()
                template_debug = traceback_data.get(
                    "template_info", {}
                )  # type: Dict[Text, Any]
                if template_debug == {}:  # type: ignore
                    self.fail(
                        "Missing template_debug attribute from {}".format(exc)
                    )  # type: ignore
                if not debug_data:
                    self.fail(  # type: ignore
                        "No data provided to check against {}".format(template_debug)
                    )

                expected = {}
                found = {}
                for expected_key, expected_value in debug_data.items():
                    self.assertIn(
                        expected_key, set(template_debug.keys())
                    )  # type: ignore
                    found_value = template_debug[expected_key]
                    if expected_value != found_value:
                        expected[expected_key] = expected_value
                        found[expected_key] = found_value

                other_keys = {}
                for k, v in template_debug.items():
                    if k not in found and k not in expected:
                        if isinstance(v, str) and len(v) > 10:
                            v = "{}...".format(v[0:10])
                        other_keys[k] = v

                if expected and found:
                    self.fail(  # type: ignore
                        "Found template_debug data {found!r} instead of {expected!r}\nOther possible keys include: {others!r}".format(
                            found=found,
                            expected=expected,
                            others=other_keys,
                        )
                    )
            else:
                self.fail(  # type: ignore
                    "No exception was raised. Expected {!s} matching: {!s}...".format(
                        exception_type.__name__, exception_repr.splitlines()[0]
                    )
                )

    class BasicUsageTestCase(CustomAssertions, TestCase):  # type: ignore
        def setUp(self):
            # type: () -> None
            from shouty import MissingVariable

            self.MissingVariable = MissingVariable

        @override_settings(DEBUG=True)
        def test_most_basic(self):
            # type: () -> None
            t = TMPL(
                """
                this works: {{ a }}
                this does not work: {{ b }}
                """
            )
            with self.assertRaisesWithTemplateDebug(
                self.MissingVariable,
                "Variable 'b' in template '<unknown source>' does not resolve.\n"
                "Possibly you meant to use 'be'.\n"
                "Silence this occurance only by adding 'b': ['<unknown source>'] to the settings.SHOUTY_VARIABLE_BLACKLIST dictionary.\n"
                "Silence this globally by adding 'b': ['*'] to the settings.SHOUTY_VARIABLE_BLACKLIST dictionary.",
                {"line": 3, "start": 76, "end": 77, "during": "b"},
            ):
                t.render(CTX({"a": 1, "be": 2}))

        @override_settings(DEBUG=True)
        def test_nested_tokens_on_dict(self):
            # type: () -> None
            t = TMPL(
                """
                this works: {{ a }}
                this works: {{ a.b }}
                this does not work: {{ a.b.c }}
                """
            )
            exc = ()
            with self.assertRaisesWithTemplateDebug(
                self.MissingVariable,
                "Token 'c' of 'a.b.c' in template '<unknown source>' does not resolve.\n"
                "Possibly you meant to use 'cd'.\n"
                "Silence this occurance only by adding 'a.b.c': ['<unknown source>'] to the settings.SHOUTY_VARIABLE_BLACKLIST dictionary.\n"
                "Silence this globally by adding 'a.b.c': ['*'] to the settings.SHOUTY_VARIABLE_BLACKLIST dictionary.",
                {"line": 4, "start": 114, "end": 119, "during": "a.b.c"},
            ):
                t.render(CTX({"a": {"b": {"cd": 1}}}))

        @override_settings(DEBUG=True)
        def test_nested_tokens_on_namedtuple(self):
            # type: () -> None
            t = TMPL(
                """
                this works: {{ a }}
                this works: {{ a.b }}
                this does not work: ... {{ a.b.c }}
                """
            )
            nt = namedtuple("nt", "cd ce cf cg")(cd=1, ce=2, cf=3, cg=4)  # type: ignore
            with self.assertRaisesWithTemplateDebug(
                self.MissingVariable,
                "Token 'c' of 'a.b.c' in template '<unknown source>' does not resolve.\n"
                "Possibly you meant one of: 'cg', 'cf', 'ce'.\n"
                "Silence this occurance only by adding 'a.b.c': ['<unknown source>'] to the settings.SHOUTY_VARIABLE_BLACKLIST dictionary.\n"
                "Silence this globally by adding 'a.b.c': ['*'] to the settings.SHOUTY_VARIABLE_BLACKLIST dictionary.",
                {"line": 4, "start": 118, "end": 123, "during": "a.b.c"},
            ):
                t.render(CTX({"a": {"b": nt}}))

        @override_settings(DEBUG=True)
        def test_index(self):
            # type: () -> None
            t = TMPL(
                """
                this works: {{ a }}
                this does not work: {{ a.11 }}
                """
            )
            with self.assertRaisesWithTemplateDebug(
                self.MissingVariable,
                "Token '11' of 'a.11' in template '<unknown source>' does not resolve.\n"
                "Possibly you meant to use '1'.\n"
                "Silence this occurance only by adding 'a.11': ['<unknown source>'] to the settings.SHOUTY_VARIABLE_BLACKLIST dictionary.\n"
                "Silence this globally by adding 'a.11': ['*'] to the settings.SHOUTY_VARIABLE_BLACKLIST dictionary.",
                {"line": 3, "start": 76, "end": 80, "during": "a.11"},
            ):
                t.render(CTX({"a": (1, 2)}))

        @override_settings(DEBUG=True)
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
            with self.assertRaisesWithTemplateDebug(
                self.MissingVariable,
                "Variable 'c' in template '<unknown source>' does not resolve.\n"
                "Silence this occurance only by adding 'c': ['<unknown source>'] to the settings.SHOUTY_VARIABLE_BLACKLIST dictionary.\n"
                "Silence this globally by adding 'c': ['*'] to the settings.SHOUTY_VARIABLE_BLACKLIST dictionary.",
                {"start": 73, "end": 74, "during": "c"},
            ):
                t.render(CTX({"a": 1, "subtemplate": st, "b": 2}))

        @override_settings(DEBUG=True)
        def test_form_possibilities(self):
            # type: () -> None
            t = TMPL(
                """
                {{ form.exampl }}
                """
            )

            class MyForm(Form):  # type: ignore
                example = IntegerField()

            with self.assertRaisesWithTemplateDebug(
                self.MissingVariable,
                "Token 'exampl' of 'form.exampl' in template '<unknown source>' does not resolve.\n"
                "Possibly you meant to use 'example'.\n"
                "Silence this occurance only by adding 'form.exampl': ['<unknown source>'] to the settings.SHOUTY_VARIABLE_BLACKLIST dictionary.\n"
                "Silence this globally by adding 'form.exampl': ['*'] to the settings.SHOUTY_VARIABLE_BLACKLIST dictionary.",
                {"start": 20, "end": 31, "during": "form.exampl"},
            ):
                t.render(CTX({"form": MyForm(data={"example": "1"})}))

        @override_settings(DEBUG=True)
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
            with self.assertRaisesWithTemplateDebug(
                self.MissingVariable,
                "Token 'object_i' of 'obj.object_i' in template '<unknown source>' does not resolve.\n"
                "Possibly you meant one of: 'object_id', 'objects', 'object_repr'.\n"
                "Silence this occurance only by adding 'obj.object_i': ['<unknown source>'] to the settings.SHOUTY_VARIABLE_BLACKLIST dictionary.\n"
                "Silence this globally by adding 'obj.object_i': ['*'] to the settings.SHOUTY_VARIABLE_BLACKLIST dictionary.",
                {"start": 20, "end": 32, "during": "obj.object_i"},
            ):
                t.render(CTX({"obj": example}))

        @override_settings(DEBUG=True)
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
            with self.assertRaisesWithTemplateDebug(
                self.MissingVariable,
                "Token 'logentry_se' of 'obj.logentry_se.all' in template '<unknown source>' does not resolve.\n"
                "Possibly you meant to use 'logentry_set'.\n"
                "Silence this occurance only by adding 'obj.logentry_se.all': ['<unknown source>'] to the settings.SHOUTY_VARIABLE_BLACKLIST dictionary.\n"
                "Silence this globally by adding 'obj.logentry_se.all': ['*'] to the settings.SHOUTY_VARIABLE_BLACKLIST dictionary.",
                {"start": 20, "end": 39, "during": "obj.logentry_se.all"},
            ):
                t.render(CTX({"obj": user}))

        @override_settings(DEBUG=True)
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

            with self.assertRaisesWithTemplateDebug(
                self.MissingVariable,
                "Token 'can_add_pastry' of 'chef.can_add_pastry' in template '<unknown source>' does not resolve.\n"
                "Possibly you meant one of: 'can_add_pastries', 'can_add_cakes'.\n"
                "Silence this occurance only by adding 'chef.can_add_pastry': ['<unknown source>'] to the settings.SHOUTY_VARIABLE_BLACKLIST dictionary.\n"
                "Silence this globally by adding 'chef.can_add_pastry': ['*'] to the settings.SHOUTY_VARIABLE_BLACKLIST dictionary.",
                {"start": 133, "end": 152, "during": "chef.can_add_pastry"},
            ):
                t.render(CTX({"chef": Chef()}))

        @override_settings(DEBUG=True)
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

            with self.assertRaisesWithTemplateDebug(
                self.MissingVariable,
                "Token 'can_add_pastry' of 'chef.can_add_pastry' in template '<unknown source>' does not resolve.\n"
                "Possibly you meant one of: 'can_add_pastries', 'can_add_cakes'.\n"
                "Silence this occurance only by adding 'chef.can_add_pastry': ['<unknown source>'] to the settings.SHOUTY_VARIABLE_BLACKLIST dictionary.\n"
                "Silence this globally by adding 'chef.can_add_pastry': ['*'] to the settings.SHOUTY_VARIABLE_BLACKLIST dictionary.",
                {"start": 46, "end": 65, "during": "chef.can_add_pastry"},
            ):
                t.render(CTX({"chef": Chef()}))

        @override_settings(DEBUG=True)
        def test_many_if_variables1(self):
            # type: () -> None
            t = TMPL(
                """
                {% if False and whooo == 2 or 0 and 1 and wheee and whooo.wheee %}
                whee
                {% endif %}
                """
            )
            with self.assertRaisesWithTemplateDebug(
                self.MissingVariable,
                "Variable 'wheee' in template '<unknown source>' does not resolve.\n"
                "Silence this occurance only by adding 'wheee': ['<unknown source>'] to the settings.SHOUTY_VARIABLE_BLACKLIST dictionary.\n"
                "Silence this globally by adding 'wheee': ['*'] to the settings.SHOUTY_VARIABLE_BLACKLIST dictionary.",
                {"start": 59, "end": 64, "during": "wheee"},
            ):
                t.render(CTX({"whooo": 0}))

        @override_settings(DEBUG=True)
        def test_many_if_variables2(self):
            # type: () -> None
            t = TMPL(
                """
                {% if False and whooo == 2 or 0 and 1 and wheee and x == 2 or f == None and False == True or wheee == wheeee %}
                whee
                {% endif %}
                """
            )
            with self.assertRaisesWithTemplateDebug(
                self.MissingVariable,
                "Variable 'whooo' in template '<unknown source>' does not resolve.\n"
                "Silence this occurance only by adding 'whooo': ['<unknown source>'] to the settings.SHOUTY_VARIABLE_BLACKLIST dictionary.\n"
                "Silence this globally by adding 'whooo': ['*'] to the settings.SHOUTY_VARIABLE_BLACKLIST dictionary.",
                {"start": 33, "end": 38, "during": "whooo"},
            ):
                t.render(CTX({}))

        @override_settings(DEBUG=True)
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
                {% else %}
                <!-- exhaustive if checking -->
                {% endif %}
                """
            )
            with self.assertRaisesWithTemplateDebug(
                self.MissingVariable,
                "Variable 'x' in template '<unknown source>' does not resolve.\n"
                "Silence this occurance only by adding 'x': ['<unknown source>'] to the settings.SHOUTY_VARIABLE_BLACKLIST dictionary.\n"
                "Silence this globally by adding 'x': ['*'] to the settings.SHOUTY_VARIABLE_BLACKLIST dictionary.",
                {"start": 133, "end": 134, "during": "x"},
            ):
                t.render(CTX({}))

        @override_settings(DEBUG=True)
        def test_if_elif_exhaustiveness(self):
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
            with self.assertRaisesWithTemplateDebug(
                self.MissingVariable,
                "No `else` statement found for '{% if 1 == 2 %}{% elif ... %}{% endif %}' in '<unknown source>'\n"
                "Silence this by adding 'if 1 == 2': ['<unknown source>'] to the settings.SHOUTY_VARIABLE_BLACKLIST dictionary.",
                {"start": 20, "end": 29, "during": "if 1 == 2"},
            ):
                t.render(CTX({}))

        @override_settings(DEBUG=True)
        def test_exception_debug_info(self):
            # type: () -> None
            t = TMPL(
                """
                {% if x == y %}
                {{ abc }}
                {% elif y == z %}
                {{ def }}
                {% else %}
                <!-- exhaustive if checking -->
                {% endif %}
                """
            )
            with self.assertRaisesWithTemplateDebug(
                self.MissingVariable,
                "Variable 'z' in template '<unknown source>' does not resolve.\n"
                "Silence this occurance only by adding 'z': ['<unknown source>'] to the settings.SHOUTY_VARIABLE_BLACKLIST dictionary.\n"
                "Silence this globally by adding 'z': ['*'] to the settings.SHOUTY_VARIABLE_BLACKLIST dictionary.",
                {"line": 4, "during": "z", "name": UNKNOWN_SOURCE},
            ):
                t.render(CTX({"x": 1, "y": 1, "def": 2}))

        @override_settings(DEBUG=True)
        def test_complex_exception_debug_info(self):
            # type: () -> None
            t = TMPL(
                """
                {# <title>{{ wheee }}</title> #}
                but this won't: {% include subtemplate with x=1 only %}
                {{ wheee }} 
                """
            )
            t.origin.template_name = "parent"
            st = TMPL(
                """
                {% if 0 %} {{ userwheee }}
                {% endif %}
                """
            )
            st.origin.template_name = "child"

            with self.assertRaisesWithTemplateDebug(
                self.MissingVariable,
                "Variable 'wheee' in template 'parent' does not resolve.\n"
                "Silence this occurance only by adding 'wheee': ['parent'] to the settings.SHOUTY_VARIABLE_BLACKLIST dictionary.\n"
                "Silence this globally by adding 'wheee': ['*'] to the settings.SHOUTY_VARIABLE_BLACKLIST dictionary.",
                {"start": 141, "end": 146, "during": "wheee"},
            ):
                t.render(CTX({"subtemplate": st}))

        @override_settings(DEBUG=True)
        def test_how_default_filters_work(self):
            # type: () -> None
            """
            The `default` and `default_if_none` filters won't allow you to silence
            the exception, because MissingVariable is a TemplateSyntaxError
            rather than a VariableDoesNotExist, so the FilterExpression never gets
            to run over it's possibilities.
            Which is probably correct really, but also possibly annoyingly strict.
            """
            t = TMPL(
                """
                this works: {{ a }}
                this does not work: {{ doesnt_exist|default:"" }}
                """
            )
            with self.assertRaisesWithTemplateDebug(
                self.MissingVariable,
                "Variable 'doesnt_exist' in template '<unknown source>' does not resolve.\n"
                "Possibly you meant to use 'doesntexist'.\n"
                "Silence this occurance only by adding 'doesnt_exist': ['<unknown source>'] to the settings.SHOUTY_VARIABLE_BLACKLIST dictionary.\n"
                "Silence this globally by adding 'doesnt_exist': ['*'] to the settings.SHOUTY_VARIABLE_BLACKLIST dictionary.",
                {"line": 3, "start": 76, "end": 88, "during": "doesnt_exist"},
            ):
                t.render(CTX({"a": 1, "doesntexist": 2}))

    class UrlTestCase(CustomAssertions, SimpleTestCase):  # type: ignore
        def setUp(self):
            # type: () -> None
            from shouty import MissingVariable

            self.MissingVariable = MissingVariable

        @override_settings(DEBUG=True)
        def test_most_basic(self):
            # type: () -> None
            t = TMPL(
                """
                {% url "waffle" as wheee %}
                """
            )
            with self.assertRaisesWithTemplateDebug(
                self.MissingVariable,
                '{% url "waffle" ... as wheee %} in template \'<unknown source> did not resolve.\n'
                "You may silence this globally by adding ('waffle', 'wheee') to settings.SHOUTY_URL_BLACKLIST",
                {"start": 36, "end": 41, "during": "wheee"},
            ):
                t.render(CTX())

    class SystemChecksTestCase(SimpleTestCase):  # type: ignore
        def test_tuple_or_list_iterable_ignored_everywhere(self):
            # type: () -> None
            with override_settings(SHOUTY_VARIABLE_BLACKLIST=("a",)):
                self.assertEqual(check_user_blacklists(None), [])

        def test_dict_allows_non_file_templates(self):
            # type: () -> None
            with override_settings(SHOUTY_VARIABLE_BLACKLIST={"a": [UNKNOWN_SOURCE]}):
                self.assertEqual(check_user_blacklists(None), [])

        def test_dict_allows_ignoring_var_in_all_templates(self):
            # type: () -> None
            with override_settings(SHOUTY_VARIABLE_BLACKLIST={"a": [ANY_TEMPLATE]}):
                self.assertEqual(check_user_blacklists(None), [])

        def test_dict_allows_ignoring_var_in_single_or_many_specific_templates(self):
            # type: () -> None
            with override_settings(SHOUTY_VARIABLE_BLACKLIST={"a": ["test.html"]}):
                self.assertEqual(check_user_blacklists(None), [])

        def test_catches_attempt_to_use_parens_for_1tuple_but_missing_trailing_comma(
            self,
        ):
            # type: () -> None
            with override_settings(SHOUTY_VARIABLE_BLACKLIST="a"):
                self.assertEqual(
                    check_user_blacklists(None)[0].msg,
                    "Setting appears to be a string",
                )

        def test_catches_isnt_a_string_and_also_isnt_an_iterable(self):
            # type: () -> None
            with override_settings(SHOUTY_VARIABLE_BLACKLIST=1):
                self.assertEqual(
                    check_user_blacklists(None)[0].msg,
                    "Setting doesn't appear to be a sequence",
                )

        def test_catches_iterable_containing_non_strings(self):
            # type: () -> None
            with override_settings(SHOUTY_VARIABLE_BLACKLIST=(1,)):
                self.assertEqual(
                    check_user_blacklists(None)[0].msg,
                    "Expected 1 to be a string",
                )

        def test_magic_variable_must_have_templates_specified(self):
            # type: () -> None
            with override_settings(SHOUTY_VARIABLE_BLACKLIST={"*": []}):
                error = check_user_blacklists(None)[0]
                self.assertEqual(
                    error.msg, "Magic variable * has an unexpected templates defintion"
                )
                self.assertEqual(
                    error.hint,
                    "Using * requires you to specify a specific template (or set of templates) to ignore",
                )

        def test_magic_variable_must_not_include_magic_template(self):
            with override_settings(SHOUTY_VARIABLE_BLACKLIST={"*": ["asdf.html", "*"]}):
                error = check_user_blacklists(None)[0]
                self.assertEqual(
                    error.msg, "Magic variable * has an unexpected templates defintion"
                )
                self.assertEqual(
                    error.hint,
                    "Using * for both the variable and template isn't supported, use settings.SHOUTY_VARIABLES = False",
                )

        def test_magic_variable_has_ok_templates(self):
            # type: () -> None
            with override_settings(SHOUTY_VARIABLE_BLACKLIST={"*": ["asdf.html"]}):
                self.assertEqual(check_user_blacklists(None), [])

        def test_magic_template_has_ok_variable(self):
            # type: () -> None
            with override_settings(SHOUTY_VARIABLE_BLACKLIST={"x": ["*"]}):
                self.assertEqual(check_user_blacklists(None), [])

    class SilencingTestCase(CustomAssertions, SimpleTestCase):  # type: ignore
        def setUp(self):
            # type: () -> None
            from shouty import MissingVariable

            self.MissingVariable = MissingVariable

        def test_silencing_variations_for_a_single_blacklisted_item(self):
            # type: () -> None
            """Adding a variable to the blacklist works OK"""
            with override_settings(SHOUTY_VARIABLE_BLACKLIST=("a",), DEBUG=True):
                t = TMPL("this works: {{ a }}")
                t.render(CTX({}))
            with override_settings(
                SHOUTY_VARIABLE_BLACKLIST={"a": [UNKNOWN_SOURCE]}, DEBUG=True
            ):
                t = TMPL("this works: {{ a }}")
                t.render(CTX({}))
            with override_settings(
                SHOUTY_VARIABLE_BLACKLIST={"a": [ANY_TEMPLATE]}, DEBUG=True
            ):
                t = TMPL("this works: {{ a }}")
                t.render(CTX({}))
            with override_settings(
                SHOUTY_VARIABLE_BLACKLIST={"a": ["test.html"]}, DEBUG=True
            ):
                t = TMPL("this works: {{ a }}")
                with self.assertRaises(self.MissingVariable):
                    t.render(CTX({}))

        def test_silencing_all_in_template(self):
            # type: () -> None
            """
            Allow for essentially doing "All variables in this template"
            https://github.com/kezabelle/django-shouty-templates/issues/6
            """
            with override_settings(
                SHOUTY_VARIABLE_BLACKLIST={ANY_VARIABLE: ["admin/filter.html"]},
                DEBUG=True,
            ):
                render_to_string("admin/filter.html")
                with self.assertRaises(self.MissingVariable):
                    render_to_string("admin/login.html")

            with override_settings(
                SHOUTY_VARIABLE_BLACKLIST={ANY_VARIABLE: ["admin/filter2.html"]},
                DEBUG=True,
            ):
                with self.assertRaisesWithTemplateDebug(
                    self.MissingVariable,
                    "Variable 'title' in template 'admin/filter.html' does not resolve.\n"
                    "Silence this occurance only by adding 'title': ['admin/filter.html'] to the settings.SHOUTY_VARIABLE_BLACKLIST dictionary.\n"
                    "Silence this globally by adding 'title': ['*'] to the settings.SHOUTY_VARIABLE_BLACKLIST dictionary.",
                    {"during": "title"},
                ):
                    render_to_string("admin/filter.html")

    class ReadmeExampleTestCase(CustomAssertions, SimpleTestCase):  # type: ignore
        def setUp(self):
            # type: () -> None
            from shouty import MissingVariable

            self.MissingVariable = MissingVariable

        @override_settings(DEBUG=True)
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

            with self.assertRaisesWithTemplateDebug(
                self.MissingVariable,
                "Token 'chef' of 'chef.can_add_cakes' in template '<unknown source>' does not resolve.\n"
                "Possibly you meant to use 'sous_chef'.\n"
                "Silence this occurance only by adding 'chef.can_add_cakes': ['<unknown source>'] to the settings.SHOUTY_VARIABLE_BLACKLIST dictionary.\n"
                "Silence this globally by adding 'chef.can_add_cakes': ['*'] to the settings.SHOUTY_VARIABLE_BLACKLIST dictionary.",
                {"start": 23, "during": "chef.can_add_cakes", "end": 41},
            ):
                t.render(CTX({"sous_chef": Chef()}))

        @override_settings(DEBUG=True)
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

            with self.assertRaisesWithTemplateDebug(
                self.MissingVariable,
                "Token 'is_cake_chef' of 'chef.is_cake_chef' in template '<unknown source>' does not resolve.\n"
                "Silence this occurance only by adding 'chef.is_cake_chef': ['<unknown source>'] to the settings.SHOUTY_VARIABLE_BLACKLIST dictionary.\n"
                "Silence this globally by adding 'chef.is_cake_chef': ['*'] to the settings.SHOUTY_VARIABLE_BLACKLIST dictionary.",
                {"start": 94, "during": "chef.is_cake_chef", "end": 111},
            ):
                t.render(CTX({"chef": Chef()}))

        @override_settings(DEBUG=True)
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

            with self.assertRaisesWithTemplateDebug(
                self.MissingVariable,
                "Token 'can_add_cakes' of 'chef.can_add_cakes' in template '<unknown source>' does not resolve.\n"
                "Possibly you meant to use 'can_add_pastries'.\n"
                "Silence this occurance only by adding 'chef.can_add_cakes': ['<unknown source>'] to the settings.SHOUTY_VARIABLE_BLACKLIST dictionary.\n"
                "Silence this globally by adding 'chef.can_add_cakes': ['*'] to the settings.SHOUTY_VARIABLE_BLACKLIST dictionary.",
                {"start": 23, "during": "chef.can_add_cakes", "end": 41},
            ):
                t.render(CTX({"chef": Chef()}))

    class CommonAppsTestCase(CustomAssertions, TestCase):  # type: ignore
        def setUp(self):
            # type: () -> None
            from shouty import MissingVariable
            from django.contrib.auth import get_user_model

            self.MissingVariable = MissingVariable
            self.user = get_user_model().objects.create_superuser(
                username="admin", email="admin@admin.admin", password="admin"
            )
            self.client.force_login(self.user)

        @override_settings(DEBUG=True)
        def test_admin_login_page_without_being_logged_in(self):
            # type: () -> None
            """The admin login screen should not raise MissingVariable, regardless of authentication state"""
            self.client.logout()
            r1 = self.client.get("/admin/")
            self.assertStatusCode(r1, 302)
            r2 = self.client.get("/admin/", follow=True)
            self.assertStatusCode(r2, 200)

        @skipIf(
            hasattr(TestCase, "subTest") is False,
            "using subTest requires running under Python 3+",
        )
        @override_settings(DEBUG=True)
        def test_get_requests_which_should_render_ok(self):
            # type: () -> None
            """normal requests to these admin & admindocs pages should not raise MissingVariable"""
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
            dj19_skips = {
                "/admin/doc/views/django.contrib.admin.sites.AdminSite.index/",
                "/admin/doc/views/django.contrib.admin.options.ModelAdmin.change_view/",
                "/admin/doc/views/django.contrib.admin.options.ModelAdmin.changelist_view/",
            }
            for url in urls:
                with self.subTest(url=url):
                    if DJANGO_VERSION[0:2] <= (1, 9) and url in dj19_skips:
                        self.skipTest("Django 1.9 doesn't support URL: {}".format(url))
                    response = self.client.get(url, follow=False)
                    self.assertStatusCode(response, 200)

        def test_example_404(self):
            # type: () -> None
            """The technical 404 page should not itself cause a 500 error"""
            with override_settings(DEBUG=True):
                response = self.client.get("/favicon.ico", follow=False)
                self.assertStatusCode(response, 404)
            with override_settings(DEBUG=False):
                response = self.client.get("/favicon.ico", follow=False)
                self.assertStatusCode(response, 404)

    class AdminHoneypotTestCase(CustomAssertions, TestCase):  # type: ignore
        def setUp(self):
            # type: () -> None
            from shouty import MissingVariable

            self.MissingVariable = MissingVariable

        @skipIf(
            "admin_honeypot" not in EXTRA_INSTALLED_APPS,
            "django-admin-honeypot is not installed",
        )
        def test_admin_honeypot_should_render_ok(self):
            # type: () -> None
            """Versions <= 1.1.0 don't set 'site_title' or 'site_header' variables"""
            response = self.client.get("/admin_honeypot/login/", follow=False)
            self.assertStatusCode(response, 200)

    class CrispyFormsTestCase(TestCase):  # type: ignore
        def setUp(self):
            # type: () -> None
            from shouty import MissingVariable

            self.MissingVariable = MissingVariable

        @skipIf(
            "crispy_forms" not in EXTRA_INSTALLED_APPS,
            "django-crispy-forms is not installed",
        )
        def test_crispy_forms_should_render_ok_and_allow_silencing_all_for_a_template(
            self,
        ):
            # type: () -> None
            """
            Silencing whole templates in a third party app, and crispy forms was the example, and there's a variable
            'html5_required' which is not being handled well:

            > shouty.MissingVariable: Variable 'html5_required' in template '<unknown source>' does not resolve.
            > You may silence this globally by adding 'html5_required' to the settings.SHOUTY_VARIABLE_BLACKLIST iterable.
            > You may silence this occurance only by adding one of 'bootstrap4/field.html', 'bootstrap4/uni_form.html' to the 'html5_required' key to the settings.SHOUTY_VARIABLE_BLACKLIST iterable.

            Note that first we should know the template source really, and additionally it's not part of the output of
            options...
            """

            class MyForm(Form):
                example1 = IntegerField()

            with override_settings(
                CRISPY_TEMPLATE_PACK="bootstrap4",
                SHOUTY_VARIABLE_BLACKLIST={
                    "*": [
                        "bootstrap4/uni_form.html",
                        "bootstrap4/field.html",
                        "bootstrap4/layout/help_text_and_errors.html",
                    ]
                },
            ):
                t = TMPL(
                    """
                    {% load crispy_forms_tags %}
                    <form method="post" class="uniForm">
                        {{ my_form|crispy }}
                    </form>
                    """
                )

                t.render(CTX({"my_form": MyForm(data=None, files=None)}))

        @skipIf(
            "crispy_forms" not in EXTRA_INSTALLED_APPS,
            "django-crispy-forms is not installed",
        )
        def test_crispy_forms_should_render_ok_and_allow_ignoring_a_specific_variable(
            self,
        ):
            # type: () -> None
            """
            This SHOULD behave the same as `test_crispy_forms_should_render_ok_and_allow_silencing_all_for_a_template`
            but currently doesn't, having a key of `"html5_required": ["bootstrap4/field.html"]` silences the
            error even though it already should be silenced by `"*"`
            """

            class MyForm(Form):
                example1 = IntegerField()

            with override_settings(
                CRISPY_TEMPLATE_PACK="bootstrap4",
                SHOUTY_VARIABLE_BLACKLIST={
                    "*": [
                        "bootstrap4/uni_form.html",
                        "bootstrap4/field.html",
                        "bootstrap4/layout/help_text_and_errors.html",
                    ],
                    "html5_required": ["bootstrap4/field.html"],
                },
            ):
                t = TMPL(
                    """
                    {% load crispy_forms_tags %}
                    <form method="post" class="uniForm">
                        {{ my_form|crispy }}
                    </form>
                    """
                )
                t.render(CTX({"my_form": MyForm(data=None, files=None)}))

    class InternalVariableBlacklistTestCase(SimpleTestCase):  # type: ignore
        def test_im_not_an_idiot(self):
            # type: () -> None
            for k, v in VARIABLE_BLACKLIST.items():
                if len(v) == 0:
                    self.fail(
                        "Key {!s} of the VARIABLE_BLACKLIST has no templates or wildcard defined".format(
                            k
                        )
                    )
                elif len(v) > 1 and ANY_TEMPLATE in v:
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
                elif len(v) > 1 and ANY_TEMPLATE in v:
                    self.fail(
                        "Key {!s} of the IF_VARIABLE_BLACKLIST has templates defined and also a wildcard: {!r}".format(
                            k, v
                        )
                    )

    class MyPyTestCase(SimpleTestCase):  # type: ignore
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

    test_cases = {
        "basic": BasicUsageTestCase,
        "url": UrlTestCase,
        "checks": SystemChecksTestCase,
        "silencing": SilencingTestCase,
        "readme": ReadmeExampleTestCase,
        "common": CommonAppsTestCase,
        "honeypot": AdminHoneypotTestCase,
        "crispy": CrispyFormsTestCase,
        "internal": InternalVariableBlacklistTestCase,
        "mypy": MyPyTestCase,
    }

    test_labels = sys.argv
    if test_labels is None:
        test_labels = ()
    if __file__ and __file__ in test_labels:
        test_labels.remove(__file__)

    test_cases_to_run = ()
    if test_labels:
        for test_label in test_labels:
            if test_label not in test_cases:
                sys.exit(
                    "Unknown test case label, expected one of: {!s}".format(
                        ", ".join(sorted(test_cases.keys()))
                    )
                )
            else:
                test_case = test_cases[test_label]
                sys.stdout.write("Adding {!r} to run\n".format(test_label))
                test_cases_to_run += (
                    test_runner.test_loader.loadTestsFromTestCase(test_case),
                )
    else:
        test_cases_to_run += tuple(
            test_runner.test_loader.loadTestsFromTestCase(test_case)
            for test_case in test_cases.values()
        )

    failures = test_runner.run_tests(
        test_labels=(),
        extra_tests=test_cases_to_run,
    )
    if cov is not None:
        sys.stdout.write("Writing coverage report\n")
        cov.stop()
        cov.save()
        cov.html_report()
