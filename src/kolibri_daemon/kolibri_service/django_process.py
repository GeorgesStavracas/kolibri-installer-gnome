from __future__ import annotations

import multiprocessing
from enum import auto
from enum import Enum

from kolibri.dist.magicbus import ProcessBus
from kolibri.dist.magicbus.plugins import SimplePlugin
from kolibri_app.globals import KOLIBRI_HOME_PATH

from ..kolibri_utils import init_kolibri
from .context import KolibriServiceContext
from .context import KolibriServiceProcess

# TODO: We need to use multiprocessing because Kolibri occasionally calls
#       os.kill against its own process ID.


class DjangoProcess(KolibriServiceProcess):
    """
    Starts Kolibri in the foreground and shares its device app key.
    - Sets context.is_starting to True when Kolibri is being started.
    - Sets context.is_stopped to True when Kolibri stops for any reason.
    """

    PROCESS_NAME: str = "kolibri-daemon-django"

    __command_rx: multiprocessing.connection.Connection
    __keep_alive: bool
    __commands: dict

    __kolibri_bus: ProcessBus

    class Command(Enum):
        START_KOLIBRI = auto()
        STOP_KOLIBRI = auto()
        SHUTDOWN = auto()

    def __init__(
        self, *args, command_rx: multiprocessing.connection.Connection, **kwargs
    ):
        super().__init__(*args, **kwargs)
        self.__command_rx = command_rx
        self.__keep_alive = True
        self.__commands = {
            self.Command.START_KOLIBRI: self.__start_kolibri,
            self.Command.STOP_KOLIBRI: self.__stop_kolibri,
            self.Command.SHUTDOWN: self.__shutdown,
        }

    def run(self):
        super().run()

        init_kolibri()

        from kolibri.utils.conf import OPTIONS
        from kolibri.utils.server import KolibriProcessBus

        self.__update_app_key()
        self.__update_kolibri_home()

        self.__kolibri_bus = KolibriProcessBus(
            port=OPTIONS["Deployment"]["HTTP_PORT"],
            zip_port=OPTIONS["Deployment"]["ZIP_CONTENT_PORT"],
            background=False,
        )

        kolibri_daemon_plugin = _KolibriDaemonPlugin(self.__kolibri_bus, self.context)
        kolibri_daemon_plugin.subscribe()

        while self.__keep_alive:
            if not self.__run_next_command(timeout=5):
                self.__shutdown()

        # TODO: Equivalent to this?
        # self.__join()

    def __run_next_command(self, timeout: int) -> bool:
        has_next = self.__command_rx.poll(timeout)

        if not has_next:
            return True

        try:
            command = self.__command_rx.recv()
        except EOFError:
            return False

        try:
            self.__run_command(command)
        except ValueError:
            return False

        return True

    def __run_command(self, command: DjangoProcess.Command):
        fn = self.__commands.get(command, None)

        if not callable(fn):
            raise ValueError("Unknown command '{}'".format(command))

        return fn()

    def __start_kolibri(self):
        # FIXME: KolibriProcessBus figures out its bind address early on and
        #        then replaces port=0 with port=bind_port. This means it will
        #        continue using the same port after stopping and starting. It
        #        becomes an issue because another process could bind to the same
        #        port at a time when Kolibri is not running.
        self.__kolibri_bus.transition("START")

    def __stop_kolibri(self):
        self.__kolibri_bus.transition("IDLE")

    def __shutdown(self):
        self.__stop_kolibri()
        self.__keep_alive = False

    def stop(self):
        pass

    def reset_context(self):
        self.context.is_starting = False
        if self.context.start_result != self.context.StartResult.ERROR:
            self.context.start_result = None
        self.context.is_stopped = True
        self.context.base_url = ""
        self.context.extra_url = ""
        self.context.app_key = ""

    def __update_app_key(self):
        from kolibri.core.device.models import DeviceAppKey

        self.context.app_key = DeviceAppKey.get_app_key()

    def __update_kolibri_home(self):
        self.context.kolibri_home = KOLIBRI_HOME_PATH.as_posix()


class _KolibriDaemonPlugin(SimplePlugin):
    __context: KolibriServiceContext

    def __init__(self, bus: ProcessBus, context: KolibriServiceContext):
        self.bus = bus
        self.__context = context

        self.bus.subscribe("SERVING", self.SERVING)
        self.bus.subscribe("ZIP_SERVING", self.ZIP_SERVING)
        self.bus.subscribe("STOP", self.STOP)

    def SERVING(self, port: int):
        from kolibri.utils.server import get_urls

        _, base_urls = get_urls(listen_port=port)

        self.__context.base_url = base_urls[0]
        self.__context.start_result = self.__context.StartResult.SUCCESS
        self.__context.is_starting = False

    def ZIP_SERVING(self, zip_port: int):
        from kolibri.utils.server import get_urls

        _, zip_urls = get_urls(listen_port=zip_port)

        self.__context.extra_url = zip_urls[0]

    def STOP(self):
        self.__context.is_starting = False
        self.__context.start_result = self.__context.StartResult.NONE
        self.__context.base_url = ""
        self.__context.extra_url = ""
        self.__context.is_stopped = True
