# -*- coding: utf-8 -*-
# eipbootstrapper.py
# Copyright (C) 2013 LEAP
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""
EIP bootstrapping
"""

import requests
import logging
import os
import errno

from PySide import QtGui, QtCore

from leap.config.providerconfig import ProviderConfig
from leap.services.eip.eipconfig import EIPConfig

logger = logging.getLogger(__name__)


class EIPBootstrapper(QtCore.QThread):
    """
    Sets up EIP for a provider a series of checks and emits signals
    after they are passed.
    If a check fails, the subsequent checks are not executed
    """

    PASSED_KEY = "passed"
    ERROR_KEY = "error"

    IDLE_SLEEP_INTERVAL = 100

    # All dicts returned are of the form
    # {"passed": bool, "error": str}
    download_config = QtCore.Signal(dict)
    download_client_certificate = QtCore.Signal(dict)

    def __init__(self):
        QtCore.QThread.__init__(self)

        self._checks = []
        self._checks_lock = QtCore.QMutex()

        self._should_quit = False
        self._should_quit_lock = QtCore.QMutex()

        # **************************************************** #
        # Dependency injection helpers, override this for more
        # granular testing
        self._fetcher = requests
        # **************************************************** #

        self._session = self._fetcher.session()
        self._provider_config = None
        self._eip_config = None
        self._download_if_needed = False

    def get_should_quit(self):
        """
        Returns wether this thread should quit

        @rtype: bool
        @return: True if the thread should terminate itself, Flase otherwise
        """

        QtCore.QMutexLocker(self._should_quit_lock)
        return self._should_quit

    def set_should_quit(self):
        """
        Sets the should_quit flag to True so that this thread
        terminates the first chance it gets
        """
        QtCore.QMutexLocker(self._should_quit_lock)
        self._should_quit = True
        self.wait()

    def start(self):
        """
        Starts the thread and resets the should_quit flag
        """
        with QtCore.QMutexLocker(self._should_quit_lock):
            self._should_quit = False

        QtCore.QThread.start(self)

    def _download_config(self):
        """
        Downloads the EIP config for the given provider

        @return: True if the checks passed, False otherwise
        @rtype: bool
        """

        assert self._provider_config, "We need a provider configuration!"

        logger.debug("Downloading EIP config for %s" %
                     (self._provider_config.get_domain(),))

        download_config_data = {
            self.PASSED_KEY: False,
            self.ERROR_KEY: ""
        }

        self._eip_config = EIPConfig()

        if self._download_if_needed and \
                os.path.exists(os.path.join(self._eip_config.get_path_prefix(),
                                            "leap",
                                            "providers",
                                            self._provider_config.get_domain(),
                                            "eip-service.json")):
                download_config_data[self.PASSED_KEY] = True
                self.download_config.emit(download_config_data)
                return True

        try:
            res = self._session.get("%s/%s/%s/%s" %
                                    (self._provider_config.get_api_uri(),
                                     self._provider_config.get_api_version(),
                                     "config",
                                     "eip-service.json"),
                                    verify=self._provider_config
                                    .get_ca_cert_path())
            res.raise_for_status()

            eip_definition = res.content

            self._eip_config.load(data=eip_definition)
            self._eip_config.save(["leap",
                                   "providers",
                                   self._provider_config.get_domain(),
                                   "eip-service.json"])

            download_config_data[self.PASSED_KEY] = True
        except Exception as e:
            download_config_data[self.ERROR_KEY] = "%s" % (e,)

        logger.debug("Emitting download_config %s" % (download_config_data,))
        self.download_config.emit(download_config_data)

        return download_config_data[self.PASSED_KEY]

    def _download_client_certificates(self):
        """
        Downloads the EIP client certificate for the given provider

        @return: True if the checks passed, False otherwise
        @rtype: bool
        """
        assert self._provider_config, "We need a provider configuration!"
        assert self._eip_config, "We need an eip configuration!"

        logger.debug("Downloading EIP client certificate for %s" %
                     (self._provider_config.get_domain(),))

        download_cert = {
            self.PASSED_KEY: False,
            self.ERROR_KEY: ""
        }

        client_cert_path = self._eip_config.\
            get_client_cert_path(self._provider_config,
                                 about_to_download=True)

        if self._download_if_needed and \
                os.path.exists(client_cert_path):
            download_cert[self.PASSED_KEY] = True
            self.download_client_certificate.emit(download_cert)
            return True

        try:
            res = self._session.get("%s/%s/%s/" %
                                    (self._provider_config.get_api_uri(),
                                     self._provider_config.get_api_version(),
                                     "cert"),
                                    verify=self._provider_config
                                    .get_ca_cert_path())
            res.raise_for_status()

            client_cert = res.content

            # TODO: check certificate validity

            try:
                os.makedirs(os.path.dirname(client_cert_path))
            except OSError as e:
                if e.errno == errno.EEXIST and \
                        os.path.isdir(os.path.dirname(client_cert_path)):
                    pass
                else:
                    raise

            with open(client_cert_path, "w") as f:
                f.write(client_cert)

            download_cert[self.PASSED_KEY] = True
        except Exception as e:
            download_cert[self.ERROR_KEY] = "%s" % (e,)

        logger.debug("Emitting download_client_certificates %s" %
                     (download_cert,))
        self.download_client_certificate.emit(download_cert)

        return download_cert[self.PASSED_KEY]

    def run_eip_setup_checks(self, provider_config, download_if_needed=False):
        """
        Starts the checks needed for a new eip setup

        @param provider_config: Provider configuration
        @type provider_config: ProviderConfig
        """
        assert provider_config, "We need a provider config!"
        assert isinstance(provider_config, ProviderConfig), "Expected " + \
            "ProviderConfig type, not %r" % (type(provider_config),)

        self._provider_config = provider_config
        self._download_if_needed = download_if_needed

        QtCore.QMutexLocker(self._checks_lock)
        self._checks = [
            self._download_config,
            self._download_client_certificates
        ]

    def run(self):
        """
        Main run loop for this thread. Executes the checks.
        """
        shouldContinue = False
        while True:
            if self.get_should_quit():
                logger.debug("Quitting provider bootstrap thread")
                return
            checkSomething = False
            with QtCore.QMutexLocker(self._checks_lock):
                if len(self._checks) > 0:
                    check = self._checks.pop(0)
                    shouldContinue = check()
                    checkSomething = True
                    if not shouldContinue:
                        logger.debug("Something went wrong with the checks, "

                                     "clearing...")
                        self._checks = []
                        checkSomething = False
            if not checkSomething:
                self.usleep(self.IDLE_SLEEP_INTERVAL)


if __name__ == "__main__":
    import sys
    from functools import partial
    app = QtGui.QApplication(sys.argv)

    import signal

    def sigint_handler(*args, **kwargs):
        logger.debug('SIGINT catched. shutting down...')
        bootstrapper_thread = args[0]
        bootstrapper_thread.set_should_quit()
        QtGui.QApplication.quit()

    def signal_tester(d):
        print d

    logger = logging.getLogger(name='leap')
    logger.setLevel(logging.DEBUG)
    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        '%(asctime)s '
        '- %(name)s - %(levelname)s - %(message)s')
    console.setFormatter(formatter)
    logger.addHandler(console)

    eip_thread = EIPBootstrapper()

    sigint = partial(sigint_handler, eip_thread)
    signal.signal(signal.SIGINT, sigint)

    timer = QtCore.QTimer()
    timer.start(500)
    timer.timeout.connect(lambda: None)
    app.connect(app, QtCore.SIGNAL("aboutToQuit()"),
                eip_thread.set_should_quit)
    w = QtGui.QWidget()
    w.resize(100, 100)
    w.show()

    eip_thread.start()

    provider_config = ProviderConfig()
    if provider_config.load(os.path.join("leap",
                                         "providers",
                                         "bitmask.net",
                                         "provider.json")):
        eip_thread.run_eip_setup_checks(provider_config)

    sys.exit(app.exec_())
