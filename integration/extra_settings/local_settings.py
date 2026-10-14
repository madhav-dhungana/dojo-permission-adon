"""Local demo-stack settings for the permission library.

Never mount this directory over a customized deployment's settings. The
first-install script appends an environment-gated hook to that deployment's
existing ``local_settings.py`` instead.
"""

from dojo_permission.integration import configure

configure(globals())
