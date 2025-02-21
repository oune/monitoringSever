from asyncio import AbstractEventLoop
from typing import List

from uvicorn import Config, Server
from sensor import Sensor
from sys import exit
from configparser import ConfigParser
from fastapi import FastAPI
from time import ctime, time
from dataController import DataController
from db import Database
from model import Model
from scipy import signal
from normalization import Normalization

import nidaqmx
import asyncio
import datetime
import socketio

conf = ConfigParser()
conf.read('resource/config.ini')
model_path = conf['model']['score_model']
init_data_path = conf['model']['calc_init']
reg_model_path = conf['model']['time_model']
db_1_path = conf['database']['machine1']
db_2_path = conf['database']['machine2']
raw_directory = conf['csv']['directory']
model_sampling_rate = int(conf['model']['rate'])
model_batch_size = int(conf['model']['batch_size'])
threshold_machine1 = int(conf['model']['threshold_machine1'])
threshold_machine2 = int(conf['model']['threshold_machine2'])
send_sampling_rate = int(conf['server']['sampling_rate'])
is_test = conf['test']['is_test']
normalization_path = conf['norm']['path']

model = Model(model_path, init_data_path, reg_model_path)


async def model_req(left: List[float], right: List[float], temp: List[float], name: str):
    try:
        score, exp_time = await model.get_model_res(left, right, temp)

        if name == 'machine1':
            threshold = threshold_machine1
        else:
            threshold = threshold_machine2

        anomaly = score >= threshold
        message = {
            'name': name,
            'score': score,
            'remain_time': exp_time,
            'anomaly': bool(anomaly),
            'threshold': threshold
        }
        await sio.emit('model', message)
    except Exception as error:
        print(error)


dc = DataController(model_req, Normalization(normalization_path),
                    model_batch_size, model_sampling_rate, db_1_path, db_2_path, raw_directory)


def sensor_config_load(config: ConfigParser):
    sampling_rate = int(config['sensor']['rate'])
    sensor_buffer_size = sampling_rate * 2

    vib_device = config['vib']['device']
    vib_channel_name = vib_device + "/" + config['vib']['channels']

    temp_device = config['temp']['device']
    temp_channel_name = temp_device + "/" + config['temp']['channels']

    return sampling_rate, sensor_buffer_size, vib_channel_name, temp_channel_name


def init_sensor(sampling_rate: int, sensor_buffer_size: int, vib_channel_name: str, temp_channel_name: str):
    vib = Sensor.vib(vib_channel_name, sampling_rate, sensor_buffer_size)
    temp = Sensor.temp(temp_channel_name, sampling_rate, sensor_buffer_size)
    return vib, temp


def try_sensor_load(config: ConfigParser):
    rate, buffer_size, vib_channel, temp_channel = sensor_config_load(config)
    vib, temp = init_sensor(rate, buffer_size, vib_channel, temp_channel)
    return vib, temp


def sensor_load(config: ConfigParser):
    try:
        return try_sensor_load(config)
    except nidaqmx.errors.DaqError:
        print('잘못된 설정값이 입력 되었습니다. config.ini 파일을 올바르게 수정해 주세요.')
        exit()


def server_load(_app, _config: ConfigParser, loop: AbstractEventLoop):
    config = Config(app=_app,
                    host=_config['server']['ip'],
                    port=int(_config['server']['port']),
                    loop=loop)
    return Server(config)


sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*')


async def read_sensor(sensor: Sensor):
    return sensor.task.read(number_of_samples_per_channel=sensor.read_count, timeout=10.0)


async def get_sensor_message(now_time, data_tag_names, data_list):
    message = {
        'time': now_time
    }
    for idx, data in enumerate(data_list):
        message[data_tag_names[idx]] = data

    return message


async def resample_message(message, sampling_rate, data_tag_names):
    me = message.copy()
    for tag in data_tag_names:
        me[tag] = signal.resample(me[tag], sampling_rate).tolist()

    return me


async def add_data_by_event(event_name, message):
    if event_name == 'vib':
        await dc.add_vib(message)
    elif event_name == 'temp':
        await dc.add_temp(message)


async def try_read(sensor: Sensor, event_name: str, data_tag_names: list):
    now_time = ctime(time())
    data_list = await read_sensor(sensor)
    message = await get_sensor_message(now_time, data_tag_names, data_list)
    resampled_message = await resample_message(message, send_sampling_rate, data_tag_names)

    await add_data_by_event(event_name, message)
    await sio.sleep(1)
    await sio.emit(event_name, resampled_message)


async def read(sensor: Sensor, event_name: str, data_tag_names: list):
    try:
        await try_read(sensor, event_name, data_tag_names)
    except nidaqmx.errors.DaqReadError:
        pass
    except Exception as error:
        print(error)


async def sensor_loop_vib():
    while True:
        await read(sensor_vib, 'vib', ['machine2_left', 'machine2_right', 'machine1_left', 'machine1_right'])


async def sensor_loop_temp():
    while True:
        await read(sensor_temp, 'temp', ['machine2', 'machine1'])


app = FastAPI()


@app.get("/{start}/{end}")
async def get_stat_month(start: datetime.date, end: datetime.date):
    try:
        machine_1_res = await Database(db_1_path).get_by_duration(start, end)
        machine_2_res = await Database(db_2_path).get_by_duration(start, end)

        return {'machine_1': machine_1_res, 'machine_2': machine_2_res}
    except Exception as e:
        print(e)


@app.get("/{date}")
async def get_stat_day(date: datetime.date):
    try:
        machine_1_res = await Database(db_1_path).get_by_one_day(date)
        machine_2_res = await Database(db_2_path).get_by_one_day(date)

        return {'machine_1': machine_1_res,
                'machine_2': machine_2_res}
    except Exception as error:
        print(error)


if __name__ == "__main__":
    sensor_vib, sensor_temp = sensor_load(conf)
    socket_app = socketio.ASGIApp(sio, app)
    main_loop = asyncio.get_event_loop()
    socket_server = server_load(socket_app, conf, main_loop)

    sensor_task_vib = sio.start_background_task(sensor_loop_vib)
    sensor_task_temp = sio.start_background_task(sensor_loop_temp)

    main_loop.run_until_complete(socket_server.serve())
    main_loop.run_until_complete(sensor_task_vib)
    main_loop.run_until_complete(sensor_task_temp)
