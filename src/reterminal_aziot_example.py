#!/usr/bin/env python3

import asyncio
import json
import os
import sys
import reterminal.core as rt
import reterminal.acceleration as rt_accel
import reterminal.button as rt_btn
from azure.iot.device.aio import ProvisioningDeviceClient
from azure.iot.device.aio import IoTHubDeviceClient
from azure.iot.device import Message, MethodResponse, exceptions
from enum import Enum
import base64
import hmac
import hashlib

FIRMWARE_VERSION = "1.0"

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


# Properties
properties = []


class PropertyType(Enum):
    Writable = 1
    ReadOnly = 2


class TwinProperties:
    def __init__(self, propertyName, propertyType, callback=None):
        self.name = propertyName
        self.type = propertyType
        self.callback = callback


def processLed(key, value):

    print(f"Processing {key} Value {value}")
    ack_code = 200

    if key == "ledStaGreen":
        rt.sta_led_green = True if value == LED_ON else False
    elif key == "ledStaRed":
        rt.sta_led_red = True if value == LED_ON else False
    elif key == "usrLed":
        rt.usr_led = True if value == LED_ON else False
    else:
        ack_code = 400

    return ack_code


def processTelemetryInterval(key, value):

    global telemetry_interval
    telemetry_interval = value
    return 200


properties.append(TwinProperties("ledStaGreen", PropertyType.Writable, processLed))
properties.append(TwinProperties("ledStaRed", PropertyType.Writable, processLed))
properties.append(TwinProperties("usrLed", PropertyType.Writable, processLed))
properties.append(TwinProperties("firmwareVer", PropertyType.ReadOnly))
properties.append(TwinProperties("telemetryInterval", PropertyType.Writable, processTelemetryInterval))


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


async def send_writable_property_confirm(
    name, value, ack_code, ack_version, description="Completed"
):
    print(f'Reported Property {name} value {value} ac {ack_code} av {ack_version}')
    prop_dict = {}
    prop_dict[name] = {
        "value": value,
        "ac": ack_code,
        "av": ack_version,
        "ad": description,
    }

    await device_client.patch_twin_reported_properties(prop_dict)


async def direct_method_handler(command_request):

    print("Command request (%s) received with payload %s" % (command_request.name, command_request.payload))

    if command_request.name == 'ringBuzzer':
        values = {}
        if not command_request.payload:
            print("Payload was empty.")
        else:
            values = command_request.payload

        await ring_buzzer_handler(values)

        response_status = 200
        response_payload = ring_buzzer_response(values)

        command_response = MethodResponse.create_from_method_request(
            command_request, response_status, response_payload
        )

        try:
            await device_client.send_method_response(command_response)
        except Exception:
            print(f"responding to the {command_request.name} command failed.")


async def twin_patch_handler(patch):

    ack_code = 200

    for key in patch.keys():
        if key == '$version':
            continue

        prop = next((x for x in properties if x.name == key), None)

        if prop == None:
            ack_code = 400
        elif prop.type == PropertyType.ReadOnly:
            ack_code = 400
        else:
            if prop.callback != None:
                ack_code = prop.callback(key, patch[key])

        await send_writable_property_confirm(key, patch[key], ack_code, patch["$version"])


async def send_telemetry(device_client, telemetry_msg):
    msg = Message(json.dumps(telemetry_msg))
    msg.content_encoding = "utf-8"
    msg.content_type = "application/json"
    await device_client.send_message(msg)


async def send_telemetry_acceleration_loop():
    global device_client

    while True:
        if "x" in acceleration and "y" in acceleration and "z" in acceleration:
            print("Sending telemetry for acceleration.")
            await send_telemetry(
                device_client,
                {
                    "acceleration": {
                        "x": acceleration["x"],
                        "y": acceleration["y"],
                        "z": acceleration["z"],
                    }
                },
            )

        await asyncio.sleep(telemetry_interval)


async def send_telemetry_button_f1():
    print("Sending telemetry for F1 button.")
    await send_telemetry(device_client, {"f1Button": "click"})


async def send_telemetry_button_f2():
    print("Sending telemetry for F2 button.")
    await send_telemetry(device_client, {"f2Button": "click"})


async def send_telemetry_button_f3():
    print("Sending telemetry for F3 button.")
    await send_telemetry(device_client, {"f3Button": "click"})


async def send_telemetry_button_o():
    print("Sending telemetry for O button.")
    await send_telemetry(device_client, {"oButton": "click"})


async def ring_buzzer_handler(values):
    if values and type(values) == int:
        duration = values
        print(f"ringBuzzer command received. duration={duration}")
        rt.buzzer = True
        await asyncio.sleep(duration / 1000)
        rt.buzzer = False


def ring_buzzer_response(values):
    return "ringBuzzer succeeded"


def stdin_listener():
    while True:
        selection = input("Press Q to quit.\n")
        if selection == "Q" or selection == "q":
            print("Quitting...")
            break


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
            await send_telemetry_button_f1()
        if (buttonEvent.name, buttonEvent.value) == (rt_btn.ButtonName.F2, 1):
            await send_telemetry_button_f2()
        if (buttonEvent.name, buttonEvent.value) == (rt_btn.ButtonName.F3, 1):
            await send_telemetry_button_f3()
        if (buttonEvent.name, buttonEvent.value) == (rt_btn.ButtonName.O, 1):
            await send_telemetry_button_o()


async def main():
    global device_client
    global telemetry_interval

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

    # Connect the client.
    await device_client.connect()

    # Get the device twin.
    twin = await device_client.get_twin()
    if "$version" not in twin["desired"]:
        print("ERROR: DeviceTwin does not contain $version.", file=sys.stderr)
        return 1

    for prop in properties:
        value = -1
        version = 1
        ack_code = 200

        if prop.type == PropertyType.Writable:

            if prop.name in twin["desired"]:
                value = twin["desired"][prop.name]
                version = twin["desired"]["$version"]
                if prop.callback != None:
                    ack_code = prop.callback(prop.name, twin["desired"][prop.name])
            else:
                version = 1
                if prop.name == "ledStaGreen":
                    value = LED_ON if rt.sta_led_green == True else LED_OFF

                elif prop.name == "ledStaRed":
                    value = LED_ON if rt.sta_led_red == True else LED_OFF

                elif prop.name == "usrLed":
                    value = LED_ON if rt.usr_led == True else LED_OFF

                elif prop.name == "telemetryInterval":
                    value = telemetry_interval

            await send_writable_property_confirm(prop.name, value, ack_code, version)

        elif prop.type == PropertyType.ReadOnly:

            sendReported = False

            if prop.name == "firmwareVer":
                if prop.name in twin["reported"]:
                    value = twin["reported"]["firmwareVer"]

                    if FIRMWARE_VERSION != value:
                        value = FIRMWARE_VERSION
                        sendReported = True
                else:
                    value = FIRMWARE_VERSION
                    sendReported = True

                if sendReported:
                    await device_client.patch_twin_reported_properties(
                        {"firmwareVer": FIRMWARE_VERSION}
                    )

    device_client.on_twin_desired_properties_patch_received = twin_patch_handler
    device_client.on_method_request_received = direct_method_handler

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
