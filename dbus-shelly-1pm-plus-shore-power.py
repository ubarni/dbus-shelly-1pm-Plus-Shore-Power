#!/usr/bin/env python

# import normal packages
import platform
import logging
import sys
import os
import sys

if sys.version_info.major == 2:
    import gobject
else:
    from gi.repository import GLib as gobject
import sys
import time
import requests  # for http GET
import configparser  # for config/ini file
from requests.auth import HTTPDigestAuth

# our own packages from victron
sys.path.insert(1, os.path.join(os.path.dirname(__file__),"/opt/victronenergy/dbus-systemcalc-py/ext/velib_python",),)
from vedbus import VeDbusService


class DbusShelly1pmService:
    def __init__(self,servicename,paths,productname="Shelly(Plus) 1PM",connection="Shelly(Plus) 1PM HTTP JSON service",):
        config = self._getConfig()
        deviceinstance = int(config["DEFAULT"]["Deviceinstance"])
        customname = config["DEFAULT"]["CustomName"]

        self._dbusservice = VeDbusService("{}.http_{:02d}".format(servicename, deviceinstance))
        self._paths = paths

        logging.debug("%s /DeviceInstance = %d" % (servicename, deviceinstance))

        # Create the generic path objects, as specified in the dbus-api document
        self._dbusservice.add_path("/ProductName", productname)
        self._dbusservice.add_path("/CustomName", customname)
        self._dbusservice.add_path("/Mgmt/Connection", connection)
        self._dbusservice.add_path("/Mgmt/ProcessName", __file__)
        self._dbusservice.add_path("/Mgmt/ProcessVersion","Unkown version, and running on Python " + platform.python_version(),)
        self._dbusservice.add_path("/Connected", 1)
        self._dbusservice.add_path("/DeviceInstance", deviceinstance)
        self._dbusservice.add_path("/ProductId", 0xFFFF)  # id assigned by Victron Support from SDM630v2.py
        self._dbusservice.add_path("/Serial", self._getShellySerial())
        self._dbusservice.add_path("/HardwareVersion", 0)
        self._dbusservice.add_path("/FirmwareVersion", 0.1)
        self._dbusservice.add_path("/UpdateIndex", 0)

        # add path values to dbus
        for path, settings in self._paths.items():
            self._dbusservice.add_path(
                path,
                settings["initial"],
                gettextcallback=settings["textformat"],
                writeable=True,
                onchangecallback=self._handlechangedvalue,
            )

        # last update
        self._lastUpdate = 0

        # add _update function 'timer'
        gobject.timeout_add(5 * 1000, self._update)  # pause 5 sec. before the next request

        # add _signOfLife 'timer' to get feedback in log every 5minutes
        gobject.timeout_add(self._getSignOfLifeInterval() * 60 * 1000, self._signOfLife)

    def _getShellySerial(self):
        serial = "Offline"
        try:
            meter_data = self._getShellyData()
        except:
            meter_data = None

        if meter_data == None:
            serial = "Offline"
        else:
            if not meter_data["sys"]["mac"]:
                raise ValueError("Response does not contain 'sys' 'mac' attribute")
            
            serial = meter_data['sys']['mac']
                
        return serial

    def _getConfig(self):
        config = configparser.ConfigParser()
        config.read("%s/config.ini" % (os.path.dirname(os.path.realpath(__file__))))

        return config

    def _getSignOfLifeInterval(self):
        config = self._getConfig()
        value = config["DEFAULT"]["SignOfLifeLog"]

        if not value:
            value = 0

        return int(value)

    def _getShellyStatusUrl(self):
        config = self._getConfig()

        URL = "http://%s/rpc/Shelly.GetStatus" % (config["SHELLY"]["Host"])
        URL = URL.replace(":@", "")

        return URL

    def _getShellyData(self):
        config = self._getConfig()
        timeout = config.getfloat('SHELLY', 'timeout')

        URL = self._getShellyStatusUrl()
        if config["SHELLY"]["Username"] != "" and config["SHELLY"]["Password"] != "":
            meter_r = requests.get(url=URL,auth=HTTPDigestAuth(config["SHELLY"]["Username"], config["SHELLY"]["Password"]), timeout=timeout)
        else:
            meter_r = requests.get(url=URL, timeout=timeout)

        # check for response
        if not meter_r:
            raise ConnectionError("No response from Shelly(Plus) 1PM - %s" % (URL))

        meter_data = meter_r.json()

        # check for Json
        if not meter_data:
            raise ValueError("Converting response to JSON failed")

        return meter_data

    def _signOfLife(self):
        logging.info("--- Start: sign of life ---")
        logging.info("Last _update() call: %s" % (self._lastUpdate))
        logging.info("Last '/Ac/Power': %s" % (self._dbusservice["/Ac/Power"]))
        logging.info("--- End: sign of life ---")
        return True

    def _update(self):
        try:
            # get data from Shelly Plus 1pm
            try:
                meter_data = self._getShellyData()
            except:
                meter_data = None

            # send data to DBus
            if meter_data == None:
                power = 0
                logging.info("Shelly Offline")
            else:
                logging.debug("Shelly data: %s", meter_data)
                power = meter_data["switch:0"]["apower"]
                total = meter_data["switch:0"]["aenergy"]["total"]
                voltage = meter_data["switch:0"]["voltage"]
                current = power / voltage

            if power > 0:
                self._dbusservice["/Ac/Energy/Forward"] = total / 1000
                self._dbusservice["/Ac/Power"] = power

                self._dbusservice["/Ac/L1/Current"] = current
                self._dbusservice["/Ac/L1/Energy/Forward"] = total / 1000
                self._dbusservice["/Ac/L1/Power"] = power
                self._dbusservice["/Ac/L1/Voltage"] = voltage
            else:
                self._dbusservice["/Ac/Energy/Forward"] = 0
                self._dbusservice["/Ac/Power"] = 0

                self._dbusservice["/Ac/L1/Current"] = 0
                self._dbusservice["/Ac/L1/Energy/Forward"] = 0
                self._dbusservice["/Ac/L1/Power"] = 0
                self._dbusservice["/Ac/L1/Voltage"] = 0

            # increment UpdateIndex - to show that new data is available
            index = self._dbusservice["/UpdateIndex"] + 1  # increment index
            if index > 255:  # maximum value of the index
                index = 0  # overflow from 255 to 0
            self._dbusservice["/UpdateIndex"] = index

            # update lastupdate vars
            self._lastUpdate = time.time()
        except Exception as e:
            logging.critical("Error at %s", "_update", exc_info=e)

        # return true, otherwise add_timeout will be removed from GObject - see docs http://library.isr.ist.utl.pt/docs/pygtk2reference/gobject-functions.html#function-gobject--timeout-add
        return True

    def _handlechangedvalue(self, path, value):
        logging.debug("someone else updated %s to %s" % (path, value))
        return True  # accept the change


def getLogLevel():
    config = configparser.ConfigParser()
    config.read("%s/config.ini" % (os.path.dirname(os.path.realpath(__file__))))
    logLevelString = config["DEFAULT"]["LogLevel"]

    if logLevelString:
        level = logging.getLevelName(logLevelString)
    else:
        level = logging.INFO

    return level


def main():
    # configure logging
    logging.basicConfig(
        format="%(asctime)s,%(msecs)d %(name)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        level=getLogLevel(),
        handlers=[logging.FileHandler("%s/current.log" % (os.path.dirname(os.path.realpath(__file__)))),logging.StreamHandler(),],)

    try:
        logging.info("Start")

        from dbus.mainloop.glib import DBusGMainLoop

        # Have a mainloop, so we can send/receive asynchronous calls to and from dbus
        DBusGMainLoop(set_as_default=True)

        # formatting
        _kwh = lambda p, v: (str(round(v, 2)) + " kWh")
        _a = lambda p, v: (str(round(v, 2)) + " A")
        _w = lambda p, v: (str(round(v, 1)) + " W")
        _v = lambda p, v: (str(round(v, 1)) + " V")

        # start our main-service
        pvac_output = DbusShelly1pmService(
            servicename="com.victronenergy.grid",
            paths={
                "/Ac/Energy/Forward": {"initial": None,"textformat": _kwh,},  # energy from shore power
                "/Ac/Power": {"initial": 0, "textformat": _w},
                "/Ac/L1/Current": {"initial": 0, "textformat": _a},
                "/Ac/L1/Energy/Forward": {"initial": None, "textformat": _kwh},
                "/Ac/L1/Power": {"initial": 0, "textformat": _w},
                "/Ac/L1/Voltage": {"initial": 0, "textformat": _v},
            },
        )

        logging.info("Connected to dbus, and switching over to gobject.MainLoop() (= event based)")
        mainloop = gobject.MainLoop()
        mainloop.run()
    except Exception as e:
        logging.critical("Error at %s", "main", exc_info=e)


if __name__ == "__main__":
    main()
