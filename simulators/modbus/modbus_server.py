import asyncio
import time
from pymodbus.server import StartAsyncTcpServer
from pymodbus.datastore import ModbusSequentialDataBlock, ModbusSlaveContext, ModbusServerContext

SENSOR_HR_BASE = 10
INPUT_IR_BASE = 0


async def mirror_sensor_registers(context: ModbusServerContext) -> None:
    while True:
        # Read sensor values from holding registers written by plant-sim.
        sensor_values = context[0].getValues(3, SENSOR_HR_BASE, count=3)
        if sensor_values:
            context[0].setValues(4, INPUT_IR_BASE, sensor_values)
        await asyncio.sleep(0.1)


async def run_server() -> None:
    store = ModbusSlaveContext(
        di=ModbusSequentialDataBlock(0, [0] * 100),
        co=ModbusSequentialDataBlock(0, [0] * 100),
        # HR[0] = target speed setpoint
        # HR[10..12] = sensor values (speed rpm, temp*10, pressure*100)
        hr=ModbusSequentialDataBlock(0, [1500] + [0] * 9 + [0, 450, 120] + [0] * 87),
        ir=ModbusSequentialDataBlock(0, [0] * 100),
    )
    context = ModbusServerContext(slaves=store, single=True)

    asyncio.create_task(mirror_sensor_registers(context))
    print("Starting Modbus TCP simulator on port 5020...")
    await StartAsyncTcpServer(context=context, address=("0.0.0.0", 5020))


if __name__ == "__main__":
    asyncio.run(run_server())
