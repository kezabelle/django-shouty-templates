django-shouty-templates
=======================

:author: Keryn Knight
:version: 0.2.0

Brief
-----

This app applies a monkeypatch which forces Django's template language to error
**very loudly** about variables which are *used* in a template but *don't exist* in the context.

Rationale
---------

Given a template like this::

    <html><head></head>
    <body>
    {% if chef.can_add_cakes %}
        <label class="alert alert-{{ chef.is_cake_chef|yesno:"success,danger,default" }}
    {% endif %}

everything works fine, until you refactor and any of the following happens:

- ``chef`` is no longer the name of the variable.
- ``can_add_cakes`` is refactored to be called ``can_add_pastries``
- ``is_cake_chef`` is renamed ``is_pastry_king``

If those happen, the template will either silently display nothing, or will
display the label incorrectly. This monkeypatch attempts to fix that.

Specifically:

- ``chef`` will raise an exception if the variable were called ``sous_chef``
- ``chef.can_add_cakes`` will raise an exception if ``can_add_cakes`` was no longer a valid attribute/property/method of ``chef``
- ``chef.is_cake_chef`` will raise an exception for the same reasons.

Thus you can refactor somewhat more freely, knowing that if the template renders
it's OK. It ain't compile time safety, but it's better than silently swallowing
errors because you forgot something!

The exception itself would look something like::

    Token 'chef' of 'chef.can_add_cakes' in template 'my/cool/template.html' does not resolve.
    Possibly you meant to use 'sous_chef'.
    Silence this occurance only by adding 'chef.can_add_cakes': ['my/cool/template.html'] to the settings.SHOUTY_VARIABLE_BLACKLIST dictionary.
    Silence this globally by adding 'chef.can_add_cakes': ['*'] to the settings.SHOUTY_VARIABLE_BLACKLIST dictionary.


Setup
-----

This package is available on `PyPI`_ and can be installed using pip or whatever like so::

    pip install django-shouty-templates==0.2.0

Then add ``shouty.Shout`` or ``shouty`` to your ``settings.INSTALLED_APPS``

Optional configuration
^^^^^^^^^^^^^^^^^^^^^^

A list of values which may be set in your project's settings module:

settings.SHOUTY_VARIABLES
+++++++++++++++++++++++++

May be ``True|False`` and determines if the exception is raised when trying to
use a variable which doesn't exist.

Defaults to ``True``.


settings.SHOUTY_URLS
++++++++++++++++++++

May be ``True|False`` and determines if an exception is raised when
doing ``{% url 'myurl' as my_var %}`` and ``myurl`` doesn't actually resolve to a view.

Defaults to ``True``.

settings.SHOUTY_VARIABLE_BLACKLIST
++++++++++++++++++++++++++++++++++

Useful for if you are trying to fix up an existing project, or ignore problems
in third-party templates.

Expects a ``dict`` of ``str`` keys and a sequence (eg: ``tuple`` or ``list``) of templates in which to ignore it::

    SHOUTY_VARIABLE_BLACKLIST = {
        "chef.can_add_cakes": ("*",),
        "my_sometimes_set_variable": ["admin/custom_view.html", "admin/custom_view_detail.html"],
        "random_in_memory_template": ["<unknown source>"],
        "*": ["admin/login.html", "<unknown source>"],
    }

Of special note is the use of ``*``, which has a more magical meaning.

- Using ``"key": ["*"]`` would silence errors about the variable named ``key`` in **all templates**
- Using ``"*": ["path/to/template.html"]`` would silence **all** variable errors in **that specific template** only (see `GitHub issue 6`_)

And also the far less frequently useful ``<unknown source>`` or ``django.template.base.UNKNOWN_SOURCE`` which is essentially usually for ``Template`` instances
not loaded from a *file on disk*

settings.SHOUTY_URL_BLACKLIST
+++++++++++++++++++++++++++++

A ``tuple`` of ``2-tuple`` to prevent certain URLs and their output variables f
rom shouting at you loudly. Useful for existing projects or third-party apps which are less strict.

By way of example, ``{% url "myurl" as my_var %}`` may be suppressed with::

    SHOUTY_URL_BLACKLIST = (
        ('myurl', 'my_var'),
    )

which would still let ``{% url "myurl" as "my_other_var" %}`` raise an exception.

Default configuration
^^^^^^^^^^^^^^^^^^^^^

There's a hard-coded blacklist of variables and URLs to make sure the Django admin (+ admindocs),
django-debug-toolbar, django-pipeline, django-admin-honeypot, djangorestframework, etc all work.

if/elif/else testing
--------------------

When an ``{% if x %}`` statement is seen in the template, all conditions are checked
to ensure the context will always resolve correctly. Additionally, if you use ``{% if %}``
and ``{% elif %}`` together and **don't** have an ``{% else %}`` it'll raise an error
to remind you to handle that case, even if handling it is just to output nothing.

Tests
-----

Just run ``python3 -m shouty`` and hope for the best. I usually do.

The license
-----------

It's `FreeBSD`_. There's should be a ``LICENSE`` file in the root of the repository, and in any archives.

.. _FreeBSD: http://en.wikipedia.org/wiki/BSD_licenses#2-clause_license_.28.22Simplified_BSD_License.22_or_.22FreeBSD_License.22.29
.. _PyPI: https://pypi.org/
.. _GitHub issue 6: https://github.com/kezabelle/django-shouty-templates/issues/6
