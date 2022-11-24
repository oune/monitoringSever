from sensor import Sensor
from time import ctime, time
from sys import exit
from uvicorn import Config, Server

import configparser
import nidaqmx
import socketio
import asyncio

config = configparser.ConfigParser()
config.read('config.ini')

try:
    pass
    # TODO 센서 로딩에서 config 파일로 부터 불러 오도록 수정
    # sensor = Sensor.of(device_name,
    #                    device_channel_name,
    #                    sampling_rate,
    #                    samples_per_channel * 2, model_type)
except nidaqmx.errors.DaqError:
    print('잘못된 설정값이 입력 되었습니다. config.ini 파일을 올바르게 수정해 주세요.')
    exit()


async def sensor_loop():
    while True:
        datas = await sensor.read(samples_per_channel, 30.0)
        now_time = ctime(time())

        await sio.sleep(1)
        for idx in range(0, len(datas)):
            await sio.emit('data', {'sensor_id': idx, 'time': now_time})
            await sio.sleep(1)

        # 모델 결과 전송 부분
        # await sio.emit('model', {'time': now_time, 'mse': output_mse.item(),  'result': model_result})
        await sio.sleep(1)


sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*')
sensor_task = sio.start_background_task(sensor_loop)
socket_app = socketio.ASGIApp(sio)

if __name__ == "__main__":
    main_loop = asyncio.get_event_loop()
    socket_config = Config(app=socket_app, host=config['server']['host_ip'], port=config['server']['port'], loop=main_loop)
    socket_server = Server(socket_config)
    main_loop.run_until_complete(socket_server.serve())
    main_loop.run_until_complete(sensor_task)
