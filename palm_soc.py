#!/usr/bin/env python3
"""PALM - PV Active Load Manager."""

import sys
import time
import json
from datetime import datetime, timedelta
from typing import Tuple, List
import requests
import settings as stgs

# This software in any form is covered by the following Open Source BSD license:
#
# Copyright 2023, Steve Lewis
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without modification, are permitted
# provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice, this list of conditions
# and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice, this list of
# conditions and the following disclaimer in the documentation and/or other materials provided
# with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS
# OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
# AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR
# CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY,
# WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY
# WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

###########################################
# This code sets overnight charge point, based on SolCast forecast & actual usage
#
###########################################

# Changelog:
# v0.8.3aSoC   28/Jul/22 Branch from palm - SoC only
# v0.8.3bSoC   05/Aug/22 Improved commenting, removed legacy code
# v0.8.4bSoC   31/Dec/22 Re-merge with Palm 0.8.4b, add example for second charge period
# v0.8.4cSoC   01/Jan/23 General tidy up
# v0.8.4dSoC   09/Jan/23 Updated GivEnergyObj to download & validate inverter commands
# v0.8.5SoC    04/May/23 Fixed midnight rollover issue in SoC calculation timing
# v0.8.6SOC    18/May/23 Merged G Hobson's Once Mode changes - FINAL VERSION

PALM_VERSION = "v0.8.6SoC"
# -*- coding: utf-8 -*-

class GivEnergyObj:
    """Class for GivEnergy inverter"""

    def __init__(self):
        sys_item = {'time': '',
                    'solar': {'power': 0, 'arrays':
                                  [{'array': 1, 'voltage': 0, 'current': 0, 'power': 0},
                                   {'array': 2, 'voltage': 0, 'current': 0, 'power': 0}]},
                    'grid': {'voltage': 0, 'current': 0, 'power': 0, 'frequency': 0},
                    'battery': {'percent': 0, 'power': 0, 'temperature': 0},
                    'inverter': {'temperature': 0, 'power': 0, 'output_voltage': 0, \
                        'output_frequency': 0, 'eps_power': 0},
                    'consumption': 0}
        self.sys_status: List[str] = [sys_item] * 5

        meter_item = {'time': '',
                      'today': {'solar': 0, 'grid': {'import': 0, 'export': 0},
                                'battery': {'charge': 0, 'discharge': 0}, 'consumption': 0},
                      'total': {'solar': 0, 'grid': {'import': 0, 'export': 0},
                                'battery': {'charge': 0, 'discharge': 0}, 'consumption': 0}}
        self.meter_status: List[str] = [meter_item] * 5

        self.read_time_mins: int = -100
        self.line_voltage: float = 0
        self.grid_power: int = 0
        self.grid_energy: int = 0
        self.pv_power: int = 0
        self.pv_energy: int = 0
        self.batt_power: int = 0
        self.consumption: int = 0
        self.soc: int = 0
        self.base_load = stgs.GE.base_load

        #  Download commands which are alid for inverter
        url = stgs.GE.url + "settings"
        key = "Bearer " + stgs.GE.key

        payload = {}
        headers = {
                'Authorization': key,
                'Content-Type': 'application/json',
                'Accept': 'application/json'
                }

        try:
            response = requests.request('GET', url, headers=headers, json=payload)
        except requests.exceptions.RequestException as error:
            print(error)
            return

        self.cmd_list = json.loads(response.content.decode('utf-8'))['data']

        if DEBUG_MODE:
            print("Inverter command list downloaded") # GH - typo corrected
            print("Valid commands:")
            for line in self.cmd_list:
                print(line['id'], " - ", line['name'])
            print("")

    def get_latest_data(self):
        """Download latest data from GivEnergy."""

        utc_timenow_mins = time_to_mins(time.strftime("%H:%M:%S", time.gmtime()))
        if (utc_timenow_mins > self.read_time_mins + 5 or
            utc_timenow_mins < self.read_time_mins):  # Update every 5 minutes plus day rollover

            url = stgs.GE.url + "system-data/latest"
            key = stgs.GE.key
            headers = {
                'Authorization': 'Bearer  ' + key,
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            }

            try:
                resp = requests.request('GET', url, headers=headers)
            except requests.exceptions.RequestException as error:
                print(error)
                return

            if len(resp.content) > 100:
                for index in range(4, -1, -1):  # Right shift old data
                    if index > 0:
                        self.sys_status[index] = self.sys_status[index - 1]
                    else:
                        try:
                            self.sys_status[index] = \
                                json.loads(resp.content.decode('utf-8'))['data']
                        except Exception:
                            print("Error reading GivEnergy system status ", TIME_NOW_VAR)
                            print(resp.content)
                            self.sys_status[index] = self.sys_status[index + 1]
                if LOOP_COUNTER_VAR == 0:  # Pack array on startup
                    index = 1
                    while index < 5:
                        self.sys_status[index] = self.sys_status[0]
                        index += 1
                self.read_time_mins = time_to_mins(self.sys_status[0]['time'][11:])
                self.line_voltage = float(self.sys_status[0]['grid']['voltage'])
                self.grid_power = -1 * int(self.sys_status[0]['grid']['power'])  # -ve = export
                self.pv_power = int(self.sys_status[0]['solar']['power'])
                self.batt_power = int(self.sys_status[0]['battery']['power'])  # -ve = charging
                self.consumption = int(self.sys_status[0]['consumption'])
                self.soc = int(self.sys_status[0]['battery']['percent'])

            url = stgs.GE.url + "meter-data/latest"
            try:
                resp = requests.request('GET', url, headers=headers)
            except requests.exceptions.RequestException as error:
                print(error)
                return

            if len(resp.content) > 100:
                for index in range(4, -1, -1):  # Right shift old data
                    if index > 0:
                        self.meter_status[index] = self.meter_status[index - 1]
                    else:
                        try:
                            self.meter_status[index] = \
                                json.loads(resp.content.decode('utf-8'))['data']
                        except Exception:
                            print("Error reading GivEnergy meter status ", TIME_NOW_VAR)
                            print(resp.content)
                            self.meter_status[index] = self.meter_status[index + 1]
                if LOOP_COUNTER_VAR == 0:  # Pack array on startup
                    index = 1
                    while index < 5:
                        self.meter_status[index] = self.meter_status[0]
                        index += 1

                self.pv_energy = int(self.meter_status[0]['today']['solar'] * 1000)

                # Daily grid energy must be >=0 for PVOutput.org (battery charge >= midnight value)
                self.grid_energy = max(int(self.meter_status[0]['today']['consumption'] * 1000), 0)

    def get_load_hist(self):
        """Download historical consumption data from GivEnergy and pack array for next SoC calc"""

        day_delta = 0 if (TIME_NOW_MINS_VAR > 1430) else 1  # Use latest full day
        day = datetime.strftime(datetime.now() - timedelta(day_delta), '%Y-%m-%d')
        url = stgs.GE.url + "data-points/" + day
        key = stgs.GE.key
        headers = {
            'Authorization': 'Bearer  ' + key,
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        params = {
            'page': '1',
            'pageSize': '2000'
        }

        try:
            resp = requests.request('GET', url, headers=headers, params=params)
        except requests.exceptions.RequestException as error:
            print(error)
            return

        if len(resp.content) > 100:
            history = json.loads(resp.content.decode('utf-8'))
            index = 0
            counter = 0
            current_energy = prev_energy = 0
            while index < 284:
                try:
                    current_energy = float(history['data'][index]['today']['consumption'])
                except Exception:
                    break
                if counter == 0:
                    self.base_load[counter] = round(current_energy, 1)
                else:
                    self.base_load[counter] = round(current_energy - prev_energy, 1)
                counter += 1
                prev_energy = current_energy
                index += 12
            print("Info; Load Calc Summary:", current_energy, self.base_load)

    def set_mode(self, cmd: str, *arg: str):
        """Configures inverter operating mode"""

        def set_inverter_register(register: str, value: str):
            """Exactly as it says"""

            result = False
            for line in self.cmd_list:
                if line['id'] == int(register):
                    cmd_name = line['name']
                    result = True
                    break

            if result:
                url = stgs.GE.url + "settings/" + register + "/write"
                key = stgs.GE.key
                headers = {
                    'Authorization': 'Bearer  ' + key,
                    'Content-Type': 'application/json',
                    'Accept': 'application/json'
                }
                payload = {
                    'value': value
                }
                resp = ""
                if not TEST_MODE:
                    try:
                        resp = requests.request('POST', url, headers=headers, json=payload)
                    except requests.exceptions.RequestException as error:
                        print(error)
                        return
                print("Info; Setting Register ", register, " (", cmd_name, ") to ", value, ", \
                      Response:", resp, sep='')
            else:
                print("Error: write to invalid inverter register: ", register)

        if cmd == "set_soc":  # Sets target SoC to value
            set_inverter_register("77", arg[0])
            if stgs.GE.start_time != "":
                set_inverter_register("64", stgs.GE.start_time)
            if stgs.GE.end_time != "":
                set_inverter_register("65", stgs.GE.end_time)

        elif cmd == "set_soc_winter":  # Restore default overnight charge params
            set_inverter_register("77", "100")
            if stgs.GE.start_time != "":
                set_inverter_register("64", stgs.GE.start_time)
            if stgs.GE.end_time_winter != "":
                set_inverter_register("65", stgs.GE.end_time_winter)

        elif cmd == "charge_now":
            set_inverter_register("77", "100")
            set_inverter_register("64", "00:30")
            set_inverter_register("65", "23:59")

        elif cmd == "pause":
            set_inverter_register("72", "0")
            set_inverter_register("73", "0")

        elif cmd == "resume":
            set_inverter_register("72", "3000")
            set_inverter_register("73", "3000")

        else:
            print("error: unknown inverter command:", cmd)

    def compute_tgt_soc(self, gen_fcast, weight: int, commit: bool):
        """Compute tomorrow's overnight SoC target"""

        batt_max_charge: float = stgs.GE.batt_max_charge

        weight = min(max(weight,10),90)  # Range check
        wgt_10 = max(0, 50 - weight)  # Triangular approximation to Solcast normal distrbution
        if weight > 50:
            wgt_50 = 90 - weight
        else:
            wgt_50 = weight - 10
        wgt_90 = max(0, weight - 50)

        tgt_soc = 100
        if gen_fcast.pv_est50_day[0] > 0:
            if MONTH_VAR in stgs.GE.winter and commit:  # No need for sums...
                print("info; winter month, SoC set to 100%")
                self.set_mode("set_soc_winter")
                return

            #  Step through forecast generation and consumption for coming day to identify
            #  lowest minimum before overcharge and maximum overcharge. If there is overcharge
            #  and the first min is above zero, reduce overnight charge for min export.

            batt_charge: float = [0] * 24
            batt_charge[0] = batt_max_charge
            max_charge = 0
            min_charge = batt_max_charge

            print()
            print("{:<20} {:>10} {:>10} {:>10}  {:>10} {:>10}".format("Info; SoC Calcs;",
                "Hour", "Charge", "Cons", "Gen", "SoC"))

            index = 0
            while index < 24:
                if index < 5:  # Battery is in AC Charge mode
                    total_load = 0
                else:
                    total_load = ge.base_load[index]
                est_gen = (gen_fcast.pv_est10_hrly[index] * wgt_10 +
                    gen_fcast.pv_est50_hrly[index] * wgt_50 +
                    gen_fcast.pv_est90_hrly[index] * wgt_90) / (wgt_10 + wgt_50 + wgt_90)
                if index > 0:
                    batt_charge[index] = (batt_charge[index - 1] +
                        max(-1 * stgs.GE.charge_rate,
                            min(stgs.GE.charge_rate, est_gen - total_load)))
                    # Capture min charge on lowest down-slope before charge exceeds 100%
                    if (batt_charge[index] <= batt_charge[index - 1] and
                        max_charge < batt_max_charge):
                        min_charge = min(min_charge, batt_charge[index])
                    elif index > 4:  # Charging after overnight boost
                        max_charge = max(max_charge, batt_charge[index])
                print("{:<20} {:>10} {:>10} {:>10}  {:>10} {:>10}".format("Info; SoC Calc;",
                    index, round(batt_charge[index], 2), round(total_load, 2),
                    round(est_gen, 2), int(100 * batt_charge[index] / batt_max_charge)))
                index += 1

            max_charge_pcnt = int(100 * max_charge / batt_max_charge)
            min_charge_pcnt = int(100 * min_charge / batt_max_charge)

            #  Reduce nightly charge to capture max export
            if MONTH_VAR in stgs.GE.shoulder:
                tgt_soc = max(stgs.GE.max_soc_target, 100 - min_charge_pcnt,
                                200 - max_charge_pcnt)
            else:
                tgt_soc = max(stgs.GE.min_soc_target, 100 - min_charge_pcnt,
                                200 - max_charge_pcnt)
            tgt_soc = int(min(max(tgt_soc, 0), 100))  # Limit range

            print()
            print("{:<25} {:>10} {:>10} {:>10} {:>10} {:>10}".format("Info; SoC Calc Summary;",
                "Max Charge", "Min Charge", "Max %", "Min %", "Target SoC"))
            print("{:<25} {:>10} {:>10} {:>10} {:>10} {:>10}".format("Info; SoC Calc Summary;",
                round(max_charge, 2), round(min_charge, 2),
                max_charge_pcnt, min_charge_pcnt, tgt_soc))
            print("{:<25} {:>10} {:>10} {:>10} {:>10} {:>10}".format("Info; SoC (Adjusted);",
                round(max_charge, 2), round(min_charge, 2),
                max_charge_pcnt - 100 + tgt_soc, min_charge_pcnt - 100 + tgt_soc, "\n"))

        else:
            print("Info; Incomplete Solcast data, setting target SoC to 100%")

        print("Info; SoC Summary; ", LONG_TIME_NOW_VAR, "; Tom Fcast Gen (kWh); ",
            gen_fcast.pv_est10_day[0], ";", gen_fcast.pv_est50_day[0], ";",
            gen_fcast.pv_est90_day[0], "; SoC Target (%); ", tgt_soc,
            "; Today Gen (kWh); ", round(self.pv_energy) / 1000, 2)

        if commit:
            self.set_mode("set_soc", str(tgt_soc))

# End of GivEnergyObj() class definition

class SolcastObj:
    """Stores and manipulates daily Solcast forecast."""

    def __init__(self):
        # Skeleton solcast summary array
        self.pv_est10_day: [int] = [0] * 7
        self.pv_est50_day: [int] = [0] *  7
        self.pv_est90_day: [int] = [0] * 7

        self.pv_est10_hrly: [int] = [0] * 24
        self.pv_est50_hrly: [int] = [0] * 24
        self.pv_est90_hrly: [int] = [0] * 24

    def update(self):
        """Updates forecast generation from Solcast server."""

        def get_solcast(url) -> Tuple[bool, str]:
            """Download latest Solcast forecast."""

            solcast_url = url + stgs.Solcast.cmd + "&api_key=" + stgs.Solcast.key
            try:
                req = requests.get(solcast_url, timeout=5)
                req.raise_for_status()
            except requests.exceptions.RequestException as error:
                print(error)
                return False, ""

            if len(req.content) < 50:
                print("Warning: Solcast data missing/short")
                print(req.content)
                return False, ""

            solcast_data = json.loads(req.content.decode('utf-8'))
            return True, solcast_data
        #  End of get_solcast()

        # Download latest data for each array, abort if unsuccessful

        result, solcast_data_1 = get_solcast(stgs.Solcast.url_se)
        if not result:
            print("Error; Problem reading Solcast data, using previous values (if any)")
            return

        if stgs.Solcast.url_sw != "":  # Two arrays are specified
            result, solcast_data_2 = get_solcast(stgs.Solcast.url_sw)
            if not result:
                print("Error; Problem reading Solcast data, using previous values (if any)")
                return

        print("Successful Solcast download.")

        # Combine forecast for PV arrays & align data with day boundaries
        pv_est10 = [0] * 10080
        pv_est50 = [0] * 10080
        pv_est90 = [0] * 10080

        if stgs.Solcast.url_sw != "":  # Two arrays are specified
            forecast_lines = min(len(solcast_data_1['forecasts']), len(solcast_data_2['forecasts']))
        else:
            forecast_lines = len(solcast_data_1['forecasts'])

        interval = int(solcast_data_1['forecasts'][0]['period'][2:4])
        solcast_offset = (60 * int(solcast_data_1['forecasts'][0]['period_end'][11:13]) +
            int(solcast_data_1['forecasts'][0]['period_end'][14:16]) - interval - 60)

        index = solcast_offset
        cntr = 0
        while index < forecast_lines * interval:
            if stgs.Solcast.url_sw != "":  # Two arrays are specified
                pv_est10[index] = (int(solcast_data_1['forecasts'][cntr]['pv_estimate10'] * 1000) +
                    int(solcast_data_2['forecasts'][cntr]['pv_estimate10'] * 1000))
                pv_est50[index] = (int(solcast_data_1['forecasts'][cntr]['pv_estimate'] * 1000) +
                    int(solcast_data_2['forecasts'][cntr]['pv_estimate'] * 1000))
                pv_est90[index] = (int(solcast_data_1['forecasts'][cntr]['pv_estimate90'] * 1000) +
                    int(solcast_data_2['forecasts'][cntr]['pv_estimate90'] * 1000))
            else:
                pv_est10[index] = int(solcast_data_1['forecasts'][cntr]['pv_estimate10'] * 1000)
                pv_est50[index] = int(solcast_data_1['forecasts'][cntr]['pv_estimate'] * 1000)
                pv_est90[index] = int(solcast_data_1['forecasts'][cntr]['pv_estimate90'] * 1000)

            if index > 1 and index % interval == 0:
                cntr += 1
            index += 1

        index = 0
        if solcast_offset > 720:  # Forget obout current day
            offset = 1440 - 90
        else:
            offset = 0

        while index < 7:  # Summarise daily forecasts
            start = index * 1440 + offset + 1
            end = start + 1439
            self.pv_est10_day[index] = round(sum(pv_est10[start:end]) / 60000, 3)
            self.pv_est50_day[index] = round(sum(pv_est50[start:end]) / 60000, 3)
            self.pv_est90_day[index] = round(sum(pv_est90[start:end]) / 60000, 3)
            index += 1

        index = 0
        while index < 24:  # Calculate hourly generation
            start = index * 60 + offset + 1
            end = start + 59
            self.pv_est10_hrly[index] = round(sum(pv_est10[start:end])/60000, 3)
            self.pv_est50_hrly[index] = round(sum(pv_est50[start:end])/60000, 3)
            self.pv_est90_hrly[index] = round(sum(pv_est90[start:end])/60000, 3)
            index += 1

        timestamp = time.strftime("%d-%m-%Y %H:%M:%S", time.localtime())
        print("PV Estimate 10% (hrly, 7 days) / kWh", ";", timestamp, ";",
            self.pv_est10_hrly[0:23], self.pv_est10_day[0:6])
        print("PV Estimate 50% (hrly, 7 days) / kWh", ";", timestamp, ";",
            self.pv_est50_hrly[0:23], self.pv_est50_day[0:6])
        print("PV Estimate 90% (hrly, 7 days) / kWh", ";", timestamp, ";",
            self.pv_est90_hrly[0:23], self.pv_est90_day[0:6])

# End of SolcastObj() class definition

def time_to_mins(time_in_hrs: str) -> int:
    """Convert times from HH:MM format to mins after midnight."""

    time_in_mins = 60 * int(time_in_hrs[0:2]) + int(time_in_hrs[3:5])
    return time_in_mins

#  End of time_to_mins()

def time_to_hrs(time_in: int) -> str:
    """Convert times from mins after midnight format to HH:MM."""

    hours = int(time_in // 60)
    mins = int(time_in - hours * 60)
    time_in_hrs = '{:02d}{}{:02d}'.format(hours, ":", mins)
    return time_in_hrs

#  End of time_to_hrs()

if __name__ == '__main__':

    LOOP_COUNTER_VAR: int = 0

    print("PALM... PV Automated Load Manager Version:", PALM_VERSION)
    print("Command line options (only one can be used):")
    print("-t | --test  | test mode (12x speed, no external server writes)")
    print("-d | --debug | debug mode, extra verbose")
    print("-o | --once  | once mode, collects forecast, updates inverter SoC target and then exits")

    # Parse any command-line arguments
    TEST_MODE: bool = False
    DEBUG_MODE: bool = False
    ONCE_MODE: bool = False

    if len(sys.argv) > 1:
        if str(sys.argv[1]) in ["-t", "--test"]:
            TEST_MODE = True
            DEBUG_MODE = True
            print("Info; Running in test mode... 12x speed, no external server writes")
        elif str(sys.argv[1]) in ["-d", "--debug"]:
            DEBUG_MODE = True
            print("Info; Running in debug mode, extra verbose")
        elif str(sys.argv[1]) in ["-o", "--once"]:
            ONCE_MODE = True
            print("Info; Running in once mode, execute forecast and inverter SoC update, then exit")

    sys.stdout.flush()

    while True:  # Main Loop
        # Current time definitions
        LONG_TIME_NOW_VAR: str = time.strftime("%d-%m-%Y %H:%M:%S", time.localtime())
        MONTH_VAR: str = LONG_TIME_NOW_VAR[3:5]
        TIME_NOW_VAR: str = LONG_TIME_NOW_VAR[11:]
        TIME_NOW_MINS_VAR: int = time_to_mins(TIME_NOW_VAR)

        if LOOP_COUNTER_VAR == 0:  # Initialise
            # GivEnergy power object initialisation
            print("Info; ----------- STARTING MAIN LOOP ---------- ", LONG_TIME_NOW_VAR)

            ge: GivEnergyObj = GivEnergyObj()
            ge.get_load_hist()
            # Solcast PV prediction object initialisation
            solcast: SolcastObj = SolcastObj()
            solcast.update()

        else:
            # Note: times are not relevant if in Once Mode
            start_time_mins = time_to_mins(stgs.GE.start_time)
            if start_time_mins < 6:  # Correct for off-peak start = 00:00
                start_time_mins = start_time_mins + 144

            # 5 minutes before off-peak start for next day's forecast
            if (TEST_MODE and LOOP_COUNTER_VAR == 0) or \
                (ONCE_MODE is False and TIME_NOW_MINS_VAR == start_time_mins - 5):
                try:
                    solcast.update()
                except Exception:
                    print("Warning; Solcast download failure")

            # 2 minutes before off-peak start for setting overnight battery charging target
            if (TEST_MODE and LOOP_COUNTER_VAR == 2) or \
                (ONCE_MODE and LOOP_COUNTER_VAR == 2) or \
                TIME_NOW_MINS_VAR == start_time_mins - 2:
                # compute & set SoC target
                try:
                    ge.get_load_hist()
                    # Change weighting in command according to desired risk/reward profile
                    print("Info; 35% weighted forecast...")
                    ge.compute_tgt_soc(solcast, 35, True)
                except Exception:
                    print("Warning; unable to set SoC")

                # if running in once mode, quit after inverter SoC update
                if ONCE_MODE:
                    exit()

            # Afternoon battery boost in winter months to load shift from peak period
            if MONTH_VAR in stgs.GE.winter and \
                time_to_mins(stgs.GE.boost_start) < time_to_mins(stgs.GE.boost_finish):
                if TIME_NOW_MINS_VAR == time_to_mins(stgs.GE.boost_start):
                    print("info; Enabling afternoon battery boost")
                    ge.set_mode("charge_now", "")
                if TIME_NOW_MINS_VAR == time_to_mins(stgs.GE.boost_finish):
                    ge.set_mode("set_winter_charge", "")

        LOOP_COUNTER_VAR += 1

        if TIME_NOW_MINS_VAR == 0:  # Reset frame counter every 24 hours
            ge.pv_energy = 0  # Avoids carry-over issues with PVOutput
            ge.grid_energy = 0
            LOOP_COUNTER_VAR = 1

        if TEST_MODE:  # Wait 5 seconds
            time.sleep(5)
        else:  # Sync to minute rollover on system clock
            CURRENT_MINUTE = int(time.strftime("%M", time.localtime()))
            while int(time.strftime("%M", time.localtime())) == CURRENT_MINUTE:
                time.sleep(10)

        sys.stdout.flush()

# End of main
