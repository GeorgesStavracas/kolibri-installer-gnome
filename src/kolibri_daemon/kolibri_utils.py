from __future__ import annotations

import filecmp
import importlib.util
import json
import logging
import os
import shutil
import typing
from pathlib import Path

from kolibri_app.config import KOLIBRI_HOME_TEMPLATE_DIR
from kolibri_app.globals import KOLIBRI_HOME_PATH

from .content_extensions_manager import ContentExtensionsManager

logger = logging.getLogger(__name__)

# These Kolibri plugins will be dynamically enabled if they are
# available:
OPTIONAL_PLUGINS = [
    "kolibri_app_desktop_xdg_plugin",
    "kolibri_desktop_auth_plugin",
]


def init_kolibri(**kwargs):
    _kolibri_update_from_home_template()

    _init_kolibri_env()

    from kolibri.plugins.registry import registered_plugins
    from kolibri.plugins.utils import enable_plugin
    from kolibri.utils.main import initialize

    registered_plugins.register_plugins(["kolibri.plugins.app"])
    enable_plugin("kolibri.plugins.app")

    available_plugins = [
        optional_plugin
        for optional_plugin in OPTIONAL_PLUGINS
        if importlib.util.find_spec(optional_plugin)
    ]

    registered_plugins.register_plugins(available_plugins)

    for plugin_name in available_plugins:
        logger.debug(f"Enabling optional plugin {plugin_name}")
        enable_plugin(plugin_name)

    initialize(**kwargs)


def _init_kolibri_env():
    os.environ["DJANGO_SETTINGS_MODULE"] = "kolibri_app.kolibri_settings"

    # Kolibri defaults to a very large thread pool. Because we expect this
    # application to be used in a single user environment with a limited
    # workload, we can use a smaller number of threads.
    os.environ.setdefault("KOLIBRI_CHERRYPY_THREAD_POOL", "10")

    # Automatically provision with $KOLIBRI_HOME/automatic_provision.json if it
    # exists.
    # TODO: Once kolibri-gnome supports automatic login for all cases, use an
    #       included automatic provision file by default.
    automatic_provision_path = _get_automatic_provision_path()
    if automatic_provision_path:
        os.environ.setdefault(
            "KOLIBRI_AUTOMATIC_PROVISION_FILE", automatic_provision_path.as_posix()
        )

    content_extensions_manager = ContentExtensionsManager()
    content_extensions_manager.apply(os.environ)


def _get_automatic_provision_path() -> typing.Optional[Path]:
    path = KOLIBRI_HOME_PATH.joinpath("automatic_provision.json")

    if not path.is_file():
        return None

    with path.open("r") as in_file:
        try:
            data = json.load(in_file)
        except json.JSONDecodeError as error:
            logger.warning(
                "Error reading automatic provision data from '{path}': {error}".format(
                    path=path.as_posix(), error=error
                )
            )
            return None

    if not data.keys().isdisjoint(
        ["facility", "superusername", "superuserpassword", "preset"]
    ):
        # If a file has an attribute unique to the old format, we will asume it
        # is outdated.
        return None

    return path


def _kolibri_update_from_home_template():
    """
    Construct a Kolibri home directory based on the Kolibri home template, if
    necessary.
    """

    # TODO: This code should probably be in Kolibri itself

    kolibri_home_template_dir = Path(KOLIBRI_HOME_TEMPLATE_DIR)

    if not kolibri_home_template_dir.is_dir():
        return

    if not KOLIBRI_HOME_PATH.is_dir():
        KOLIBRI_HOME_PATH.mkdir(parents=True, exist_ok=True)

    compare = filecmp.dircmp(
        kolibri_home_template_dir,
        KOLIBRI_HOME_PATH,
        ignore=["logs", "job_storage.sqlite3"],
    )

    if len(compare.common) > 0:
        return

    # If Kolibri home was not already initialized, copy files from the
    # template directory to the new home directory.

    logger.info("Copying KOLIBRI_HOME template to '{}'".format(KOLIBRI_HOME_PATH))

    for filename in compare.left_only:
        left_file = Path(compare.left, filename)
        right_file = Path(compare.right, filename)
        if left_file.is_dir():
            shutil.copytree(left_file, right_file)
        else:
            shutil.copy2(left_file, right_file)
