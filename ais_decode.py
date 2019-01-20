#!/usr/bin/python3
# -*- coding: utf-8 -*-

# Dieses Programm empfängt über UDP Port 2947 NMEA AIS Daten und entschlüsselt sie.
# aisdeco2 --freq-correction 33 --agc --udp localhost:2947
#
# Wenn das Schiff nicht die Fähre PILLAU (MMSI 211457860), ist werden die Daten angezeigt.

# pip3 install -U libais
# sudo apt-get install --no-install-recommends python-geopy

import csv
import datetime
import math
import re
import socket
import time
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import geopy.distance
import numpy as np
from ais import nmea_queue

coords_my_position = (54.36441, 9.82202)
UDP_IP_ADDRESS = "127.0.0.1"
UDP_PORT_NO = 2947
PORT_NUMBER = 9999


class MyHandler(BaseHTTPRequestHandler):


    @staticmethod
    def write_ships(wfile, ships, direction, status):
        first = True
        for ship in ships:
            if ship['direction'] == direction and ship['status'] == status:
                if first:
                    first = False
                else:
                    wfile.write(b',\n')
                wfile.write(str(ship).replace('\'', '"').encode())

    def do_GET(self):
        """Handler for the GET requests"""
        if self.path == '/data.json':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()

            self.wfile.write(b'{"from_kiel":[\n')
            self.write_ships(self.wfile, ais_decoder.current_ships.values(), 'east -> west', 'coming')
            self.wfile.write(b'], "to_kiel":[\n')
            self.write_ships(self.wfile, ais_decoder.current_ships.values(), 'west -> east', 'leaving')
            self.wfile.write(b'], "from_rendsburg":[\n')
            self.write_ships(self.wfile, ais_decoder.current_ships.values(), 'west -> east', 'coming')
            self.wfile.write(b'], "to_rendsburg":[\n')
            self.write_ships(self.wfile, ais_decoder.current_ships.values(), 'east -> west', 'leaving')
            self.wfile.write(b']}\n')

        else:
            self.send_response(404)
            self.end_headers()

        return


class WebServer(Thread):
    def __init__(self, ais_decoder):
        """ Constructor. """

        Thread.__init__(self)
        self.ais_decoder = ais_decoder

    def run(self):
        server = HTTPServer(('', PORT_NUMBER), MyHandler)
        print('Started httpserver on port ', PORT_NUMBER)

        server.serve_forever()


class AisDecoder:
    msg_stats = 0
    ships = {}
    ship_types = {}
    mid_list = {}
    current_ships = {}

    def __init__(self):
        self.read_dictionary(self.mid_list, 'mid.csv')
        self.read_dictionary(self.ship_types, 'shiptypes.txt')

    @staticmethod
    def read_dictionary(target, file_name):
        """Liest eine Tab-getrennte CSV mit zwei Spalten ein und speichert die Werte in dem angegebenen Dictionary.
        Der Wert aus der ersten Spalte wird als Key verwendet. """
        with open(file_name, mode='r') as csv_file:
            csv_reader = csv.reader(csv_file, delimiter='\t')
            for row in csv_reader:
                target[row[0]] = row[1].strip()

    def readlines(self, sock, recv_buffer=1024, delim='\n'):
        buf = ''
        data = True
        while data:
            data, addr = sock.recvfrom(recv_buffer)
            buf += data.decode('utf-8')
            while buf.find(delim) != -1:
                line, buf = buf.split('\n', 1)
                self.msg_stats += 1
                yield line
        return

    @staticmethod
    def get_bearing(p1, p2):
        """ Bestimmt zu den beiden angegeben Postionen die Richung in Grad zwischen 0 und 360."""
        lat1 = p1[0]
        lon1 = p1[1]
        lat2 = p2[0]
        lon2 = p2[1]
        d_lon = lon2 - lon1
        y = math.sin(d_lon) * math.cos(lat2)
        x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(d_lon)
        bearing = np.rad2deg(math.atan2(y, x))
        if bearing < 0:
            bearing += 360
        return bearing

    def recv_over_socket(self):
        server_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        server_sock.bind((UDP_IP_ADDRESS, UDP_PORT_NO))
        print('Ready to receive AIS messages via UDP port %d' % UDP_PORT_NO)

        buf = ""
        part1 = False
        part2 = False
        while True:
            for data in self.readlines(server_sock):
                try:
                    # Confirm it is a NMEA String
                    if data.startswith("!"):
                        msg = ",".join(data.split(','))
                        if data.split(',')[1] == "1":  # Message is a 1 line message
                            self.process_queue(msg)
                        elif data.split(',')[1] == "2":  # Message is a 2 line message
                            if data.split(',')[2] == "1":  # Line 1 of 2
                                buf = msg
                                part1 = True
                            elif data.split(',')[2] == "2":  # Line 2 of 2
                                if part1:
                                    buf = str(buf) + "\n" + str(msg)
                                    self.process_queue(buf)
                                    buf = ""
                                    part1 = False
                                else:
                                    buf = ""
                                    part1 = False
                            else:
                                buf = ""
                                part1 = False
                        elif data.split(',')[1] == "3":  # Message is a 3 line message
                            if data.split(',')[2] == "1":  # Line 1 of 3
                                buf = msg
                                part1 = True
                            elif data.split(',')[2] == "2":  # Line 2 of 3
                                if part1:
                                    buf = str(buf) + "\n" + str(msg)
                                    part2 = True
                                    part1 = False
                            elif data.split(',')[2] == "3":  # Line 3 of 3
                                if part2:
                                    buf = str(buf) + "\n" + str(msg)
                                    self.process_queue(buf)
                                    buf = ""
                                    part1 = False
                                    part2 = False
                                else:
                                    buf = ""
                                    part1 = False
                                    part2 = False
                            else:
                                buf = ""
                                part1 = False
                                part2 = False
                except:
                    traceback.print_exc()
                    print("Message parsing error")
                    pass

    def process_queue(self, msg):
        msg_queue = nmea_queue.NmeaQueue()
        if msg:
            result = {}
            for line in msg.splitlines():  # Splits multi-line messages into separate line
                msg_queue.put(line)
            if not msg_queue.empty():
                result = msg_queue.get()  # The decoded result
            if 'decoded' in result:  # Decoded result is saved as dict with key 'decoded'
                self.process_message(result['decoded'])

    def process_message(self, msg):
        msg_type = msg['id']
        mmsi = str(msg['mmsi'])

        if msg_type == 1 or msg_type == 3:
            # Only messages with id == 1 contain information about position and speed
            if '211457860' != mmsi and '211274960' != mmsi:
                coords_ship = (msg['y'], msg['x'])
                distance = geopy.distance.great_circle(coords_ship, coords_my_position).m
                bearing = self.get_bearing(coords_ship, coords_my_position)
                direction = 'unknown'
                status = 'unknown'
                if msg['cog'] < 110:
                    direction = 'west -> east'
                    if bearing < 90:
                        status = 'leaving'
                    elif bearing > 250:
                        status = 'coming'
                elif msg['cog'] > 230:
                    direction = 'east -> west'
                    if bearing < 90:
                        status = 'coming'
                    elif bearing > 250:
                        status = 'leaving'

                speed = msg['sog'] * 1.852
                if speed == 0:
                    speed = 0.1
                seconds_to_arrival = distance / (speed / 3.6)
                name = self.get_ship_name(mmsi)
                country = self.mid_list[mmsi[:3]]
                ship_data = {
                    'date': datetime.datetime.now().isoformat(),
                    'timestamp': time.time(),
                    'name': name,
                    'mmsi': mmsi,
                    'country': country,
                    'distance': distance,
                    'speed': speed,
                    'direction': direction,
                    'status': status,
                    'seconds_to_arrival': seconds_to_arrival
                }
                if mmsi in self.ships:
                    ship = self.ships[mmsi]
                    ship_data['length'] = ship['length']
                    ship_data['width'] = ship['width']
                    ship_data['type'] = ship['type']
                    ship_data['draught'] = ship['draught']
                self.current_ships[mmsi] = ship_data
                self.remove_old_ships()
                print(self.current_ships)
        else:
            if msg_type == 5:
                self.store_ship_data(msg)
            else:
                print(msg)

    def remove_old_ships(self):
        now = time.time()
        for mmsi in self.current_ships:
            ship = self.current_ships[mmsi]
            if (now - ship['timestamp']) > 360:  # six minutes
                del self.current_ships[mmsi]

    def get_ship_name(self, mmsi):
        """Returns the name of the ship if it is known or its MMSI otherwise."""
        if mmsi in self.ships:
            return self.ships[mmsi]['name']
        return mmsi

    def store_ship_data(self, msg):
        mmsi = str(msg['mmsi'])

        # store data in a file
        f = open("/tmp/ships.txt", "a+")
        f.write(datetime.datetime.now().isoformat())
        f.write("\n")
        f.write(str(msg))
        f.write("\n")
        f.close()

        if mmsi not in self.ships:
            self.ships[mmsi] = {}

        name = msg['name']
        name = re.sub(r"@+$", "", name)
        name = re.sub(r" +$", "", name)

        country = self.mid_list[mmsi[:3]]
        self.ships[mmsi]['country'] = country
        self.ships[mmsi]['length'] = int(msg['dim_a']) + int(msg['dim_b'])
        self.ships[mmsi]['width'] = int(msg['dim_c']) + int(msg['dim_d'])
        self.ships[mmsi]['draught'] = msg['draught']
        self.ships[mmsi]['name'] = name
        if msg['type_and_cargo']:
            self.ships[mmsi]['type'] = self.ship_types[str(msg['type_and_cargo'])]
        else:
            self.ships[mmsi]['type'] = 'Unknown'

if __name__ == '__main__':
    ais_decoder = AisDecoder()
    web_server = WebServer(ais_decoder)
    web_server.start()
    ais_decoder.recv_over_socket()
