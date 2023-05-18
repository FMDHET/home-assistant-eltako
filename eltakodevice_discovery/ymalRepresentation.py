import ruamel.yaml
import json
from termcolor import colored
import logging
from eltakobus.device import BusObject
from eltakobus.message import *

EEP_MAPPING = [
    {'hw-type': 'FTS14EM', 'eep': 'F6-02-01', 'type': 'binary_sensor', 'description': 'Rocker switch', 'address_count': 1},
    {'hw-type': 'FTS14EM', 'eep': 'F6-02-02', 'type': 'binary_sensor', 'description': 'Rocker switch', 'address_count': 1},
    {'hw-type': 'FTS14EM', 'eep': 'F6-10-00', 'type': 'binary_sensor', 'description': 'Window handle', 'address_count': 1},
    {'hw-type': 'FTS14EM', 'eep': 'D5-00-01', 'type': 'binary_sensor', 'description': 'Contact sensor', 'address_count': 1},
    {'hw-type': 'FTS14EM', 'eep': 'A5-08-01', 'type': 'binary_sensor', 'description': 'Occupancy sensor', 'address_count': 1},

    {'hw-type': 'FWG14', 'eep': 'A5-13-01', 'type': 'sensor', 'description': 'Weather station', 'address_count': 1},
    {'hw-type': 'FTS14EM', 'eep': 'A5-12-01', 'type': 'sensor', 'description': 'Window handle', 'address_count': 1},
    {'hw-type': 'FSDG14', 'eep': 'A5-12-02', 'type': 'sensor', 'description': 'Automated meter reading - electricity', 'address_count': 1},
    {'hw-type': 'F3Z14D', 'eep': 'A5-13-01', 'type': 'sensor', 'description': 'Automated meter reading - gas', 'address_count': 1},
    {'hw-type': 'F3Z14D', 'eep': 'A5-12-03', 'type': 'sensor', 'description': 'Automated meter reading - water', 'address_count': 1},

    {'hw-type': 'FUD14', 'eep': 'A5-38-08', 'sender_eep': 'A5-38-08', 'type': 'light', 'description': 'Central command - gateway', 'address_count': 1},
    {'hw-type': 'FSR14_1x', 'eep': 'M5-38-08', 'sender_eep': 'A5-38-08', 'type': 'light', 'description': 'Eltako relay', 'address_count': 1},
    {'hw-type': 'FSR14_x2', 'eep': 'M5-38-08', 'sender_eep': 'A5-38-08', 'type': 'light', 'description': 'Eltako relay', 'address_count': 2},
    {'hw-type': 'FSR14_4x', 'eep': 'M5-38-08', 'sender_eep': 'A5-38-08', 'type': 'light', 'description': 'Eltako relay', 'address_count': 4},

    {'hw-type': 'FSR14_1x', 'eep': 'M5-38-08', 'sender_eep': 'A5-38-08', 'type': 'switch', 'description': 'Eltako relay', 'address_count': 1},
    {'hw-type': 'FSR14_x2', 'eep': 'M5-38-08', 'sender_eep': 'A5-38-08', 'type': 'switch', 'description': 'Eltako relay', 'address_count': 2},
    {'hw-type': 'FSR14_4x', 'eep': 'M5-38-08', 'sender_eep': 'A5-38-08', 'type': 'switch', 'description': 'Eltako relay', 'address_count': 4},

    {'hw-type': 'FSB14', 'eep': 'G5-3F-7F', 'sender_eep': 'H5-3F-7F', 'type': 'cover', 'description': 'Eltako cover', 'address_count': 1},
]

ORG_MAPPING = {
    5: {'Telegram': 'RPS', 'RORG': 'F6', 'name': 'Switch', 'type': 'binary_sensor', 'eep': 'F6-02-01'},
    6: {'Telegram': '1BS', 'RORG': 'D5', 'name': '1 Byte Communication', 'type': 'sensor', 'eep': 'D5-??-??'},
    7: {'Telegram': '4BS', 'RORG': 'A5', 'name': '4 Byte Communication', 'type': 'sensor', 'eep': 'A5-??-??'},
}

SENSOR_MESSAGE_TYPES = [EltakoWrappedRPS, EltakoWrapped4BS, RPSMessage, Regular4BSMessage, Regular1BSMessage, EltakoMessage]

class HaConfig():

    def __init__(self, default_sender_address, save_debug_log_config:bool=False):
        super()

        self.eltako = {}
        self.sener_id_list = []
        self.sender_address = default_sender_address
        self.export_logger = save_debug_log_config


    def find_device_info(self, name):
        for i in EEP_MAPPING:
            if i['hw-type'] == name:
                return i
        return None
    
    
    def get_formatted_address(self, address):
        a = f"{address:08x}".upper()
        return f"{a[0:2]}-{a[2:4]}-{a[4:6]}-{a[6:8]}"


    async def add_device(self, device: BusObject):
        device_name = type(device).__name__
        info = self.find_device_info(device_name)

        if info != None:
            for i in range(0,info['address_count']):

                dev_obj = {
                    'id': self.get_formatted_address(device.address+i),
                    'eep': f"{info['eep']}",
                    'name': f"{device_name} - {device.address+i}",
                }

                if info['type'] in ['light', 'switch', 'cover']:
                    dev_obj['sender'] = {
                        'id': f"{self.get_formatted_address(self.sender_address+device.address+i)}",
                        'eep': f"{info['sender_eep']}",
                    }
                
                if info['type'] == 'cover':
                    dev_obj['device_class'] = 'shutter'
                    dev_obj['time_closes'] = 24
                    dev_obj['time_opens'] = 25

                if info['type'] not in self.eltako:
                    self.eltako[info['type']] = []    
                self.eltako[info['type']].append(dev_obj)
                
                logging.info(colored(f"Add device {info['type']}: id: {dev_obj['id']}, eep: {dev_obj['eep']}, name: {dev_obj['name']}",'yellow'))


    def guess_sensor_type_by_address(self, msg:ESP2Message)->str:
        if type(msg) == Regular1BSMessage:
            try:
                data = b"\xa5\x5a" + msg.body[:-1]+ (sum(msg.body[:-1]) % 256).to_bytes(2, 'big')
                _msg = Regular1BSMessage.parse(data)
                min_address = b'\x00\x00\x10\x01'
                max_address = b'\x00\x00\x14\x89'
                if min_address <= _msg.address and _msg.address <= max_address:
                    return "FTS14EM switch"
            except:
                pass
        
        if type(msg) == RPSMessage:
            if b'\xFE\xDB\x00\x00' < msg.address:
                return "Wall Switch /Rocker Switch"

        if type(msg) == Regular4BSMessage:
            return "Multi-Sensor ? "

        return "???"
    

    async def add_sensor(self, msg: ESP2Message):
        if type(msg) in SENSOR_MESSAGE_TYPES:
            logging.debug(msg)
            if hasattr(msg, 'outgoing'):
                if msg.address not in self.sener_id_list:

                    info = ORG_MAPPING[msg.org]
                    address = b2a(msg.address).replace(' ','-').upper()
                    sensor_type = self.guess_sensor_type_by_address(msg)
                    msg_type = type(msg).__name__
                    comment = f"Sensor Type: {sensor_type}, Derived from Msg Type: {msg_type}"

                    sensor = {
                        'id': address,
                        'eep': info['eep'],
                        'name': f"{info['name']} {address}",
                        'comment': comment
                    }

                    if info['type'] == 'binary_sensor':
                        sensor['device_class'] = 'window / door / smoke / motion / ?'

                    if info['type'] not in self.eltako:
                        self.eltako[info['type']] = []

                    self.eltako[info['type']].append(sensor)
                    self.sener_id_list.append(msg.address)
                    
                    logging.info(colored(f"Add Sensor ({msg_type} - {info['name']}): address: {address}, Sensor Type: {sensor_type}", 'yellow'))
        else:
            if type(msg) == EltakoDiscoveryRequest and msg.address == 127:
                logging.info(colored('Wait for incoming sensor singals. After you have recorded all your sensor singals press Ctrl+c to exist and store the configuration file.', 'red', attrs=['bold']))
            # to find message which are not displayed. Only for debugging because most of the messages are poll messages.
            # logging.debug(msg)


    def save_as_yaml_to_flie(self, filename:str):
        logging.info(colored(f"\nStore config into {filename}", 'red', attrs=['bold']))
        
        # go through manually to be able to add comments
        yaml = ruamel.yaml.YAML()
        e = self.eltako

        with open(filename, 'w') as f:
            print("eltako:", file=f)
            for type_key in e.keys():
                print(f"  {type_key}:", file=f)
                for item in e[type_key]:
                    print(f"  - id: {item['id']}", file=f)
                    for entry_key in item.keys():
                        if entry_key not in ['id', 'sender', 'comment']:
                            value = item[entry_key]
                            if type(value).__name__ == 'str' and '?' in value:
                                value += " # <= NEED TO BE COMPLETED!!!"
                            print(f"    {entry_key}: {value}", file=f)
                        if entry_key == 'sender':
                            print("    sender:", file=f)
                            print(f"      id: {item[entry_key]['id']}", file=f)
                            print(f"      eep: {item[entry_key]['eep']}", file=f)
                    if 'comment' in item.keys():
                        print(f"    #{item['comment']}", file=f)

            # logs
            print("logger:", file=f)
            print("  default: info", file=f)
            print("  logs:", file=f)
            print("    eltako: debug", file=f)