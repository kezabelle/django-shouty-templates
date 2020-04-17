django-shouty-templates
=======================

:author: Keryn Knight
:version: 0.1.4


Rationale
---------

Given a template like this::

    <html><head></head>
    <body>
    {% if chef.can_add_cakes %}
        <label class="alert alert-{{ chef.is_cake_chef|yesno:"success,danger,default" }}
    {% endif %}

everything works fine, until any of the following happens:

- ``chef`` is no longer the name of the variable.
- ``can_add_cakes`` is refactored to be called ``can_add_pastries``
- ``is_cake_chef`` is renamed ``is_pastry_king``

If those happen, the template will either silently display nothing, or will
display the label incorrectly.

This app applies a monkeypatch which forces Django's template language to error
far more loudly about invalid assumptions.

Specifically:

- ``chef`` would raise an exception if the variable were called ``sous_chef``
- ``chef.can_add_cakes`` would raise an exception if ``can_add_cakes`` was no longer a valid attribute/property/method of ``chef``
- ``chef.is_cake_chef`` would raise an exception for the same reasons.

Thus you can refactor somewhat more freely, knowing that if the template renders
it's OK. It ain't compile time safety, but it's better than silently swallowing
errors because you forgot something!

Setup
-----

Add ``shouty`` or ``shouty.Shout`` to your ``settings.INSTALLED_APPS``

Optional configuration
^^^^^^^^^^^^^^^^^^^^^^

- ``settings.SHOUTY_VARIABLES`` may be ``True|False`` and determines if the
  exception is raised when trying to use a variable which doesn't exist.
  Defaults to ``True``.
- ``settings.SHOUTY_URLS`` may be ``True|False`` and determines if an
  exception is raised when doing ``{% url 'myurl' as my_var %}`` and ``myurl``
  doesn't actually resolve to a view.  Defaults to ``True``.
- ``settings.SHOUTY_VARIABLE_BLACKLIST`` is a ``tuple`` of ``str`` where each one
  represents a template usage to ignore. Useful for if you are trying to fix up
  an existing project. eg: ``chef.can_add_cakes`` may be supressed if necessary.
- ``settings.SHOUTY_URL_BLACKLIST`` is a ``tuple`` of ``2-tuple`` to prevent
  certain URLs and their output variables from shouting at you loudly. Useful for
  existing projects or third-party apps which are less strict.
  eg: ``{% url "myurl" as my_var %}`` may be suppressed with ``('myurl', 'my_var')``
  which would still let ``{% url "myurl as "my_other_var %}`` raise an exception.

Default configuration
^^^^^^^^^^^^^^^^^^^^^

There's a hard-coded blacklist of variables and URLs to make sure the Django admin and
django-debug-toolbar work.

Tests
-----

Literally none. Enjoy!

The license
-----------

It's `FreeBSD`_. There's should be a ``LICENSE`` file in the root of the repository, and in any archives.

.. _FreeBSD: http://en.wikipedia.org/wiki/BSD_licenses#2-clause_license_.28.22Simplified_BSD_License.22_or_.22FreeBSD_License.22.29
