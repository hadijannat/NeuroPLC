import math
import os
import random
import time
from pymodbus.client import ModbusTcpClient

SENSOR_HR_BASE = 10


class PlantModel:
    def __init__(self) -> None:
        self.speed_rpm = 0.0
        self.temp_c = 45.0
        self.pressure_bar = 1.2

    def step(self, setpoint_rpm: float, dt_s: float) -> None:
        # First-order response to setpoint
        alpha = min(1.0, dt_s / 0.8)
        self.speed_rpm += alpha * (setpoint_rpm - self.speed_rpm)

        # Temperature rises with speed
        temp_target = 40.0 + 0.02 * self.speed_rpm
        self.temp_c += 0.08 * (temp_target - self.temp_c)

        # Pressure gently oscillates
        base = 1.0 + 0.0002 * self.speed_rpm
        self.pressure_bar = base + 0.05 * math.sin(time.time() / 5.0)

        # Noise
        self.speed_rpm += random.gauss(0.0, 1.5)
        self.temp_c += random.gauss(0.0, 0.05)
        self.pressure_bar += random.gauss(0.0, 0.01)


def clamp_u16(value: float) -> int:
    return max(0, min(65535, int(value)))


def main() -> None:
    host = os.getenv("MODBUS_HOST", "modbus-sim")
    port = int(os.getenv("MODBUS_PORT", "5020"))
    dt = float(os.getenv("SIMULATION_DT", "0.1"))

    model = PlantModel()
    client = ModbusTcpClient(host, port=port)
    while True:
        if not client.connect():
            print("Plant simulator: Modbus connect failed, retrying...")
            time.sleep(1.0)
            continue

        print(f"Plant simulator connected to Modbus at {host}:{port}")
        last = time.time()
        try:
            while True:
                now = time.time()
                dt_s = now - last
                last = now

                # Read target speed setpoint from holding register 0
                result = client.read_holding_registers(0, 1)
                if result.isError():
                    raise RuntimeError(result)
                setpoint = float(result.registers[0])

                model.step(setpoint, dt_s)

                # Write sensors into holding registers 10..12
                payload = [
                    clamp_u16(model.speed_rpm),
                    clamp_u16(model.temp_c * 10.0),
                    clamp_u16(model.pressure_bar * 100.0),
                ]
                write = client.write_registers(SENSOR_HR_BASE, payload)
                if write.isError():
                    raise RuntimeError(write)

                time.sleep(dt)
        except Exception as exc:
            print(f"Plant simulator error: {exc}")
            client.close()
            time.sleep(1.0)


if __name__ == "__main__":
    main()
