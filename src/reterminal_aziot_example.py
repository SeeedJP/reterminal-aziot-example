#!/usr/bin/env python3

import asyncio
import base64
import hashlib
import hmac
import json
import os
import sys
import time

import reterminal.core as rt
import reterminal.acceleration as rt_accel
import reterminal.button as rt_btn

from azure.iot.device.aio import ProvisioningDeviceClient
from azure.iot.device.aio import IoTHubDeviceClient
from azure.iot.device import exceptions

from mj_azure_iot_pnp_device.device import IoTHubDeviceClient as MjClient
import mj_azure_iot_pnp_device.contents as MjCont
from varname import nameof

FIRMWARE_VERSION = "1.1"

LED_OFF = 1
LED_ON = 2

acceleration = {}
telemetry_interval = 2  # [sec.]

MODEL_ID = "dtmi:seeedkk:reterminal:reterminal_aziot_example;2"

SECURITY_TYPE = os.getenv("IOTHUB_DEVICE_SECURITY_TYPE")
DPS_DEVICE_ENDPOINT_HOST = os.getenv("IOTHUB_DEVICE_DPS_ENDPOINT")
ID_SCOPE = os.getenv("IOTHUB_DEVICE_DPS_ID_SCOPE")
REGISTRATION_ID = os.getenv("IOTHUB_DEVICE_DPS_DEVICE_ID")
SYMMETRIC_KEY = os.getenv("IOTHUB_DEVICE_DPS_DEVICE_KEY")
DEVICE_CONNECTION_STRING = os.getenv("IOTHUB_DEVICE_CONNECTION_STRING")


class RingBuzzerCommand(MjCont.Command):
    def handler(self, payload):
        if payload and type(payload) == int:
            duration = payload
            print(f"ringBuzzer command received. duration={duration}")
            rt.buzzer = True
            time.sleep(duration / 1000)
            rt.buzzer = False

            return 200, "ringBuzzer succeeded"

        return 400, "ringBuzzer error"


class StaLedGreenProperty(MjCont.WritableProperty):
    @property
    def value(self):
        return LED_ON if rt.sta_led_green == True else LED_OFF

    @value.setter
    def value(self, value):
        rt.sta_led_green = value == LED_ON


class StaLedRedProperty(MjCont.WritableProperty):
    @property
    def value(self):
        return LED_ON if rt.sta_led_red == True else LED_OFF

    @value.setter
    def value(self, value):
        rt.sta_led_red = value == LED_ON


class UsrLedProperty(MjCont.WritableProperty):
    @property
    def value(self):
        return LED_ON if rt.usr_led == True else LED_OFF

    @value.setter
    def value(self, value):
        rt.usr_led = value == LED_ON


class TelemetryIntervalProperty(MjCont.WritableProperty):
    @property
    def value(self):
        return telemetry_interval

    @value.setter
    def value(self, value):
        global telemetry_interval
        telemetry_interval = value


def generate_device_key(device_id, group_symmetric_key):
    message = device_id.encode("utf-8")
    signing_key = base64.b64decode(group_symmetric_key.encode("utf-8"))
    signed_hmac = hmac.HMAC(signing_key, message, hashlib.sha256)
    device_key_encoded = base64.b64encode(signed_hmac.digest())
    return device_key_encoded.decode("utf-8")


async def provision_device(provisioning_host, id_scope, registration_id, symmetric_key, model_id):

    provisioning_device_client = None

    try:
        provisioning_device_client = ProvisioningDeviceClient.create_from_symmetric_key(
            provisioning_host=provisioning_host,
            registration_id=registration_id,
            id_scope=id_scope,
            symmetric_key=symmetric_key,
        )

        provisioning_device_client.provisioning_payload = {"modelId": model_id}
        return await provisioning_device_client.register()

    except exceptions.ClientError as ex:
        print(f'[Exception] : Provisioning client raised error. {ex}')
        return None

    except Exception as ex:
        print(f'[Exception] : Exception during provisioning {ex}')

    return None


def stdin_listener():
    while True:
        selection = input("Press Q to quit.\n")
        if selection == "Q" or selection == "q":
            print("Quitting...")
            break


async def send_telemetry_acceleration_loop():
    while True:
        if "x" in acceleration and "y" in acceleration and "z" in acceleration:
            print("Sending telemetry for acceleration.")
            pnp_client.acceleration.value = {
                "x": acceleration["x"],
                "y": acceleration["y"],
                "z": acceleration["z"],
            }
            await pnp_client.send_telemetry(nameof(pnp_client.acceleration))

        await asyncio.sleep(telemetry_interval)


async def accel_coroutine(device):
    async for event in device.async_read_loop():
        accelEvent = rt_accel.AccelerationEvent(event)

        if accelEvent.name == rt_accel.AccelerationName.X:
            acceleration["x"] = accelEvent.value / 1000  # [g]
        elif accelEvent.name == rt_accel.AccelerationName.Y:
            acceleration["y"] = accelEvent.value / 1000  # [g]
        elif accelEvent.name == rt_accel.AccelerationName.Z:
            acceleration["z"] = accelEvent.value / 1000  # [g]


async def btn_coroutine(device):
    async for event in device.async_read_loop():
        buttonEvent = rt_btn.ButtonEvent(event)

        if (buttonEvent.name, buttonEvent.value) == (rt_btn.ButtonName.F1, 1):
            pnp_client.f1Button.value = "click"
            await pnp_client.send_telemetry(nameof(pnp_client.f1Button))
        if (buttonEvent.name, buttonEvent.value) == (rt_btn.ButtonName.F2, 1):
            pnp_client.f2Button.value = "click"
            await pnp_client.send_telemetry(nameof(pnp_client.f2Button))
        if (buttonEvent.name, buttonEvent.value) == (rt_btn.ButtonName.F3, 1):
            pnp_client.f3Button.value = "click"
            await pnp_client.send_telemetry(nameof(pnp_client.f3Button))
        if (buttonEvent.name, buttonEvent.value) == (rt_btn.ButtonName.O, 1):
            pnp_client.oButton.value = "click"
            await pnp_client.send_telemetry(nameof(pnp_client.oButton))


def check_environment_variables():
    ok = True

    if SECURITY_TYPE == "DPS":
        if DPS_DEVICE_ENDPOINT_HOST is None:
            print("ERROR: The environment variable DPS_DEVICE_ENDPOINT_HOST is invalid.", file=sys.stderr)
            ok = False

        if ID_SCOPE is None:
            print("ERROR: The environment variable ID_SCOPE is invalid.", file=sys.stderr)
            ok = False

        if REGISTRATION_ID is None:
            print("ERROR: The environment variable REGISTRATION_ID is invalid.", file=sys.stderr)
            ok = False

        if SYMMETRIC_KEY is None:
            print("ERROR: The environment variable SYMMETRIC_KEY is invalid.", file=sys.stderr)
            ok = False

    elif SECURITY_TYPE == "connectionString":
        if DEVICE_CONNECTION_STRING is None:
            print("ERROR: The environment variable DEVICE_CONNECTION_STRING is invalid.", file=sys.stderr)
            ok = False

    else:
        print("ERROR: The environment variable SECURITY_TYPE is invalid.", file=sys.stderr)
        ok = False

    return ok


async def main():
    global device_client
    global pnp_client

    if not check_environment_variables():
        return 1

    symmeticKey = SYMMETRIC_KEY
    # Create the device_client.
    if SECURITY_TYPE == "DPS":
        registration_result = await provision_device(
            DPS_DEVICE_ENDPOINT_HOST, ID_SCOPE, REGISTRATION_ID, SYMMETRIC_KEY, MODEL_ID
        )

        if registration_result == None:
            symmeticKey = generate_device_key(REGISTRATION_ID, SYMMETRIC_KEY)

            registration_result = await provision_device(
                DPS_DEVICE_ENDPOINT_HOST, ID_SCOPE, REGISTRATION_ID, symmeticKey, MODEL_ID
            )

        if registration_result.status == "assigned":
            print("Device was assigned.")
            print(f'IoT Hub   : {registration_result.registration_state.assigned_hub}')
            print(f'Device ID : {registration_result.registration_state.device_id}')

            device_client = IoTHubDeviceClient.create_from_symmetric_key(
                symmetric_key=symmeticKey,
                hostname=registration_result.registration_state.assigned_hub,
                device_id=registration_result.registration_state.device_id,
                product_info=MODEL_ID,
            )
        else:
            print("ERROR: Could not provision device. Aborting Plug and Play device connection.", file=sys.stderr)
            return 1

    elif SECURITY_TYPE == "connectionString":
        print(f"Connecting using Connection String {DEVICE_CONNECTION_STRING}")
        device_client = IoTHubDeviceClient.create_from_connection_string(
            DEVICE_CONNECTION_STRING, product_info=MODEL_ID
        )

    else:
        print("ERROR: At least one choice needs to be made for complete functioning of this sample.", file=sys.stderr)
        return 1

    # Create the pnp client.
    pnp_client = MjClient()
    pnp_client.acceleration = MjCont.Telemetry()
    pnp_client.ambientLight = MjCont.Telemetry()
    pnp_client.f1Button = MjCont.Telemetry()
    pnp_client.f2Button = MjCont.Telemetry()
    pnp_client.f3Button = MjCont.Telemetry()
    pnp_client.oButton = MjCont.Telemetry()
    pnp_client.ringBuzzer = RingBuzzerCommand()
    pnp_client.ledStaGreen = StaLedGreenProperty()
    pnp_client.ledStaRed = StaLedRedProperty()
    pnp_client.usrLed = UsrLedProperty()
    pnp_client.firmwareVer = MjCont.ReadOnlyProperty()
    pnp_client.telemetryInterval = TelemetryIntervalProperty()

    pnp_client.firmwareVer.value = FIRMWARE_VERSION

    # Connect the client.
    pnp_client.set_iot_hub_device_client(device_client)
    await pnp_client.connect()

    # Assign acceleration and button events.
    accel_device = rt.get_acceleration_device()
    btn_device = rt.get_button_device()
    asyncio.ensure_future(accel_coroutine(accel_device))
    asyncio.ensure_future(btn_coroutine(btn_device))

    # Assign the send_telemetry_acceleration task.
    send_telemetry_acceleration_task = asyncio.create_task(
        send_telemetry_acceleration_loop()
    )

    # Run the stdin listener in the event loop
    loop = asyncio.get_running_loop()
    user_finished = loop.run_in_executor(None, stdin_listener)
    await user_finished

    await device_client.disconnect()

    # Cleanup.
    send_telemetry_acceleration_task.cancel()
    await device_client.shutdown()

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
